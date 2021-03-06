import os
import random
from functools import reduce
from collections import namedtuple
from web3 import Web3
from raiden_contracts.utils.config import SETTLE_TIMEOUT_MIN
from raiden_contracts.utils.merkle import compute_merkle_tree
from eth_abi import encode_abi


PendingTransfersTree = namedtuple('PendingTransfersTree', [
    'transfers',
    'unlockable',
    'expired',
    'packed_transfers',
    'merkle_tree'
])


def random_secret():
    secret = os.urandom(32)
    return (Web3.soliditySha3(['bytes32'], [secret]), secret)


def get_pending_transfers(
        web3,
        unlockable_amounts,
        expired_amounts,
        min_expiration_delta,
        max_expiration_delta
):
    current_block = web3.eth.blockNumber
    min_expiration_delta = min_expiration_delta or (len(unlockable_amounts) + 1)
    max_expiration_delta = max_expiration_delta or (min_expiration_delta + SETTLE_TIMEOUT_MIN)
    unlockable_locks = [
        [
            current_block + random.randint(min_expiration_delta, max_expiration_delta),
            amount,
            *random_secret()
        ]
        for amount in unlockable_amounts
    ]
    expired_locks = [
        [current_block, amount, *random_secret()]
        for amount in expired_amounts
    ]
    return (unlockable_locks, expired_locks)


def get_pending_transfers_tree(
        web3,
        unlockable_amounts=[],
        expired_amounts=[],
        min_expiration_delta=None,
        max_expiration_delta=None
):
    types = ['uint256', 'uint256', 'bytes32']
    (unlockable_locks, expired_locks) = get_pending_transfers(
        web3,
        unlockable_amounts,
        expired_amounts,
        min_expiration_delta,
        max_expiration_delta
    )

    pending_transfers = unlockable_locks + expired_locks

    hashed_pending_transfers = [
        Web3.soliditySha3(types, transfer_data[:-1])
        for transfer_data in pending_transfers
    ]

    hashed_pending_transfers, pending_transfers = zip(*sorted(zip(
        hashed_pending_transfers,
        pending_transfers
    )))

    merkle_tree = compute_merkle_tree(hashed_pending_transfers)
    packed_transfers = get_packed_transfers(pending_transfers, types)

    return PendingTransfersTree(
        transfers=pending_transfers,
        unlockable=unlockable_locks,
        expired=expired_locks,
        packed_transfers=packed_transfers,
        merkle_tree=merkle_tree,
    )


def get_packed_transfers(pending_transfers, types):
    packed_transfers = [encode_abi(types, x[:-1]) for x in pending_transfers]
    return reduce((lambda x, y: x + y), packed_transfers)


def get_settlement_amounts(
        participant1_deposit,
        participant1_transferred_amount,
        participant1_locked_amount,
        participant2_deposit,
        participant2_transferred_amount,
        participant2_locked_amount
):
    """ Settlement algorithm

    Calculates the token amounts to be transferred to the channel participants when
    a channel is settled
    """
    total_deposit = participant1_deposit + participant2_deposit
    participant1 = (
        participant1_deposit +
        participant2_transferred_amount -
        participant1_transferred_amount)
    participant1 = min(participant1, total_deposit)
    participant1 = max(participant1, 0)
    participant2 = total_deposit - participant1

    participant1 = max(participant1 - participant1_locked_amount, 0)
    participant2 = max(participant2 - participant2_locked_amount, 0)

    return (participant1, participant2, participant1_locked_amount + participant2_locked_amount)


def get_unlocked_amount(secret_registry, merkle_tree_leaves):
    unlocked_amount = 0

    for i in range(0, len(merkle_tree_leaves), 96):
        lock = merkle_tree_leaves[i:(i + 96)]
        expiration_block = int.from_bytes(lock[0:32], byteorder='big')
        locked_amount = int.from_bytes(lock[32:64], byteorder='big')
        secrethash = lock[64:96]

        reveal_block = secret_registry.call().getSecretRevealBlockHeight(secrethash)
        if reveal_block > 0 and reveal_block < expiration_block:
            unlocked_amount += locked_amount
    return unlocked_amount


def get_locked_amount(pending_transfers):
    return reduce((lambda x, y: x + y[1]), pending_transfers, 0)

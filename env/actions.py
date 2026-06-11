"""Discrete action space for the Zulrah agent (matches the server's ZulrahControl action ids).

Attack is decoupled from weapon-switching: the policy issues `equip_range` / `equip_mage` separately from
`attack`, so it has to learn *when* to switch styles, not get it for free.
"""
from enum import IntEnum


class Action(IntEnum):
    ATTACK = 0          # attack Zulrah with the currently equipped weapon
    EQUIP_RANGE = 1     # switch to the ranged weapon (crossbow)
    EQUIP_MAGE = 2      # switch to the magic weapon (trident)
    PROTECT_MAGIC = 3
    PROTECT_MISSILES = 4
    EAT = 5
    ANTIVENOM = 6
    RESTORE_PRAYER = 7
    MOVE_N = 8
    MOVE_E = 9
    MOVE_S = 10
    MOVE_W = 11
    DROP_PRAYER = 12    # turn off all overhead prayers (enables prayer flicking)


NUM_ACTIONS = len(Action)
ACTION_NAMES = [a.name.lower() for a in Action]

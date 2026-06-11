#!/usr/bin/env python3
"""Mechanical env regression check (NOT a policy / NOT strategy).

Confirms the env's action plumbing works so PPO can actually learn:
  - spamming the 'attack range' action must reduce Zulrah's HP (the attack action does damage).
It encodes no Zulrah strategy; it just mashes one button and checks the world reacts.
"""
import json
import socket
import sys

# action ids
ATTACK, EQUIP_RANGE, EQUIP_MAGE = 0, 1, 2
HOST, PORT = "127.0.0.1", 43500


def main():
    steps = int(sys.argv[1]) if len(sys.argv) > 1 else 40
    style = sys.argv[2] if len(sys.argv) > 2 else "range"  # "range" or "mage"
    f = socket.create_connection((HOST, PORT), timeout=30).makefile("rwb")

    def cmd(line):
        f.write((line + "\n").encode()); f.flush()
        return json.loads(f.readline().decode())

    obs = cmd("reset")
    z0 = obs["zulrah"]["hp"] if obs["zulrah"].get("present") else None
    print(f"reset: zulrah hp={z0} form={obs['zulrah'].get('form')} style={style}")
    cmd(f"step {EQUIP_MAGE if style == 'mage' else EQUIP_RANGE}")  # equip the weapon once, then spam attack

    zhp_min = z0
    for i in range(steps):
        obs = cmd(f"step {ATTACK}")
        z = obs["zulrah"]
        if z.get("present"):
            zhp_min = min(zhp_min, z["hp"])
        if i % 5 == 0 or obs.get("done"):
            print(f"  step {i:3d} php={obs['player']['hp']} zhp={z.get('hp') if z.get('present') else '-'}")
        if obs.get("done"):
            print(f"  done outcome={obs.get('outcome')}")
            break

    dealt = (z0 - zhp_min) if (z0 is not None and zhp_min is not None) else 0
    print(f"\nattack action dealt {dealt} damage to Zulrah -> {'OK' if dealt > 0 else 'BROKEN'}")
    f.close()
    sys.exit(0 if dealt > 0 else 1)


if __name__ == "__main__":
    main()

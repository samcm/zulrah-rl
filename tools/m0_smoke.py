#!/usr/bin/env python3
"""M0 acceptance smoke test for the Zulrah control socket.

Connects to the Zenyte RL control server, resets (spawns a headless bot into a fresh Zulrah
instance), prints the live state vector, then issues a manual move and confirms the player
tile actually changes — i.e. an action visibly takes effect.

No dependencies; pure stdlib. Run the server first (gradlew runOfflineDev), then:
    python3 tools/m0_smoke.py
"""
import json
import socket
import sys
import time

HOST, PORT = "127.0.0.1", 43500


class Control:
    def __init__(self, host=HOST, port=PORT, timeout=20.0):
        self.sock = socket.create_connection((host, port), timeout=timeout)
        self.f = self.sock.makefile("rwb")

    def cmd(self, line):
        self.f.write((line + "\n").encode())
        self.f.flush()
        reply = self.f.readline().decode().strip()
        return reply

    def cmd_json(self, line):
        return json.loads(self.cmd(line))

    def close(self):
        try:
            self.f.close()
            self.sock.close()
        except OSError:
            pass


def pp(label, obj):
    print(f"\n=== {label} ===")
    print(json.dumps(obj, indent=2))


def main():
    print(f"connecting to {HOST}:{PORT} ...")
    c = Control()

    assert c.cmd("ping") == '{"pong":true}', "ping failed"
    print("ping ok")

    print("reset (spawning bot + launching Zulrah instance; may take a few ticks) ...")
    obs = c.cmd_json("reset")
    pp("state after reset", obs)

    if not obs.get("ready"):
        print("FAIL: bot not ready after reset")
        c.close()
        sys.exit(1)

    p0 = obs["player"]
    z = obs.get("zulrah", {})
    print(f"\nplayer at ({p0['x']},{p0['y']},{p0['z']})  hp={p0['hp']}/{p0['maxHp']} prayer={p0['prayer']}")
    print(f"zulrah present={z.get('present')} form={z.get('form')} hp={z.get('hp')}")

    if not z.get("present"):
        print("WARN: Zulrah not present yet (instance may still be constructing).")

    # Issue a few move actions and watch the tile change.
    print("\nissuing move actions: E, E, N ...")
    moves = [(8, "E"), (8, "E"), (7, "N")]
    last = (p0["x"], p0["y"])
    moved = False
    for action, label in moves:
        obs = c.cmd_json(f"step {action}")
        p = obs["player"]
        now = (p["x"], p["y"])
        delta = (now[0] - last[0], now[1] - last[1])
        print(f"  move {label}: ({last[0]},{last[1]}) -> ({now[0]},{now[1]})  delta={delta}")
        if now != last:
            moved = True
        last = now

    # Toggle a protection prayer and confirm it registers in state.
    print("\nissuing 'protect from magic' (action 2) ...")
    obs = c.cmd_json("step 2")
    overhead = obs["player"]["overhead"]
    print(f"  overhead prayer now: {overhead}")

    print("\n" + "=" * 40)
    if moved:
        print("PASS: state vector populated and movement took effect.")
    else:
        print("FAIL: player did not move.")
    print(f"PASS (prayer): overhead={overhead}" if overhead == "magic"
          else f"NOTE: prayer overhead={overhead} (needs prayer level/points)")
    c.close()
    sys.exit(0 if moved else 1)


if __name__ == "__main__":
    main()

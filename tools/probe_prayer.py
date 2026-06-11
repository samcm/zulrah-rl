"""Quantify whether the current policy actually prayer-switches (vs face-tanks in BIS gear).

Loads the newest checkpoint, plays a few episodes, and reports, over ticks where Zulrah is in an
attackable form (mage/range): how often the overhead prayer matched the form, how often incoming
hits actually landed (last_atk != none), and the per-episode outcome / pool spent.

    python tools/probe_prayer.py --episodes 4
"""
import argparse
import glob
import os
import sys
from collections import Counter

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stable_baselines3 import PPO
from env import ZulrahEnv

NEEDED = {"range": "missiles", "mage": "magic"}  # form -> overhead that protects


def newest(pattern):
    c = glob.glob(pattern)
    return max(c, key=os.path.getmtime) if c else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/*.zip")
    ap.add_argument("--episodes", type=int, default=4)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=43500)
    ap.add_argument("--max-steps", type=int, default=600)
    args = ap.parse_args()

    path = newest(args.model)
    model = PPO.load(path, device="cpu")
    env = ZulrahEnv(host=args.host, port=args.port, max_steps=args.max_steps)
    print(f"model: {os.path.basename(path)}")

    for ep in range(args.episodes):
        obs, _ = env.reset()
        prayable = correct = hits_taken = ticks = 0
        min_hp, max_hp = 99, 0
        hp_drops = ever_venom = 0
        actions = Counter()
        last_atks = Counter()
        done = trunc = False
        prev_hp = None
        while not (done or trunc):
            a, _ = model.predict(obs, deterministic=True)
            a = int(a)
            actions[a] += 1
            obs, r, done, trunc, info = env.step(a)
            raw = info["raw"]
            p, z = raw.get("player", {}), raw.get("zulrah", {})
            ticks += 1
            hp = p.get("hp", 0)
            max_hp = max(max_hp, p.get("maxHp", 0))
            min_hp = min(min_hp, hp)
            if prev_hp is not None and hp < prev_hp:
                hp_drops += 1
            prev_hp = hp
            if p.get("venomed"):
                ever_venom += 1
            la = p.get("last_atk", "none")
            last_atks[la] += 1
            if la and la != "none":
                hits_taken += 1
            need = NEEDED.get(z.get("form")) if z.get("present") else None
            if need:
                prayable += 1
                if p.get("overhead") == need:
                    correct += 1
        outcome = info.get("outcome", "?")
        pool0 = None  # pool delta hard to get post-hoc; report final supplies instead
        sup = raw.get("supplies", {})
        pr = (100.0 * correct / prayable) if prayable else float("nan")
        print(f"ep{ep}: outcome={outcome:6s} ticks={ticks:3d} "
              f"prayer_correct={correct}/{prayable} ({pr:4.0f}%) "
              f"hits_taken={hits_taken} hp_drops={hp_drops} hp={min_hp}/{max_hp} venom_ticks={ever_venom} "
              f"last_atk={dict(last_atks)} "
              f"food_left={sup.get('food','?')} restore_left={sup.get('prayer','?')} "
              f"top_actions={actions.most_common(4)}")
    env.close()


if __name__ == "__main__":
    main()

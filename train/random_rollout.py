"""M1: run a RANDOM policy through ZulrahEnv end-to-end and log episode length + outcome to TensorBoard.

    python train/random_rollout.py --episodes 50

Acceptance: episodes start, step, and terminate (kill/death/timeout); length + outcome curves appear in
TensorBoard (tensorboard --logdir runs/). Per-episode traces are written for the ghost renderer.
"""
import argparse
import os
import sys
import time
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import numpy as np
from tensorboardX import SummaryWriter

from env import ZulrahEnv


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--episodes", type=int, default=50)
    ap.add_argument("--max-steps", type=int, default=300)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=43500)
    ap.add_argument("--logdir", default="runs/random")
    ap.add_argument("--tracedir", default="traces/random")
    ap.add_argument("--seed", type=int, default=0)
    args = ap.parse_args()

    rng = np.random.default_rng(args.seed)
    writer = SummaryWriter(args.logdir)
    env = ZulrahEnv(host=args.host, port=args.port, max_steps=args.max_steps, trace_dir=args.tracedir)

    outcomes = {"kill": 0, "death": 0, "left": 0, "timeout": 0}
    recent_kill = deque(maxlen=50)
    total_steps = 0
    t0 = time.time()

    try:
        for ep in range(args.episodes):
            obs, info = env.reset()
            done = trunc = False
            total_r = 0.0
            length = 0
            while not (done or trunc):
                action = int(rng.integers(0, env.action_space.n))
                obs, r, done, trunc, info = env.step(action)
                total_r += r
                length += 1
                total_steps += 1
            outcome = info["outcome"] if done else "timeout"
            outcomes[outcome] = outcomes.get(outcome, 0) + 1
            recent_kill.append(1.0 if outcome == "kill" else 0.0)

            writer.add_scalar("episode/length", length, ep)
            writer.add_scalar("episode/reward", total_r, ep)
            for name in ("kill", "death", "left", "timeout"):
                writer.add_scalar(f"episode/{name}", 1.0 if outcome == name else 0.0, ep)
            writer.add_scalar("rate/kill_rate_50", float(np.mean(recent_kill)), ep)
            print(f"ep {ep:4d}  len={length:4d}  reward={total_r:8.3f}  outcome={outcome}")

        elapsed = time.time() - t0
        sps = total_steps / elapsed if elapsed > 0 else 0.0
        writer.add_scalar("summary/steps_per_sec", sps, 0)
        print("\n=== summary ===")
        print(f"episodes: {args.episodes}  outcomes: {outcomes}")
        print(f"steps: {total_steps}  elapsed: {elapsed:.1f}s  steps/sec: {sps:.2f}")
    finally:
        env.close()
        writer.close()


if __name__ == "__main__":
    main()

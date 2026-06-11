"""Publish the PPO trainer's latest rollout metrics as JSON so the viewer can render them live.

The training metrics (entropy, approx_kl, kill_rate, reward components, ...) live in the trainer's stdout, not in
the viewer's own episodes. This tail-parses that stdout log and writes the most recent value of each metric to a
stable JSON path the viewer polls — so the viewer never needs to know the (harness-specific) log path, and we don't
have to restart the in-progress trainer to add CSV logging.

    python tools/train_metrics_sidecar.py <trainer_stdout_log> [--out runs/train_metrics.json] [--interval 3]
"""
import argparse
import json
import os
import re
import time

KEYS = {
    "total_timesteps", "fps", "ep_len_mean", "ep_rew_mean", "kill_rate", "death_rate", "left_rate",
    "timeout_rate", "zulrah_min_hp_mean", "entropy_loss", "approx_kl", "explained_variance", "value_loss",
    "policy_gradient_loss", "clip_fraction", "learning_rate", "dmg_dealt", "pool_lost", "kill", "death",
    "ent_coef_now",
}
LINE = re.compile(r"\|\s*([A-Za-z_]+)\s*\|\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*\|")
BORDER = re.compile(r"^-{6,}$")
# the per-rollout fields the dashboard charts plot, so they can show the whole run on load instead of
# filling one point every ~40s.
SERIES_KEYS = ("total_timesteps", "kill_rate", "ep_rew_mean", "zulrah_min_hp_mean",
               "entropy_loss", "ent_coef_now", "dmg_dealt", "kill", "death_rate")


def parse_series(path):
    """Every rollout's metrics as an ordered series (one record per SB3 table block, deduped by step)."""
    try:
        with open(path) as f:
            data = f.read()
    except OSError:
        return []
    by_step, block = {}, {}
    for line in data.splitlines():
        s = line.strip()
        if BORDER.match(s):
            if "total_timesteps" in block:
                by_step[block["total_timesteps"]] = {k: block[k] for k in SERIES_KEYS if k in block}
            block = {}
            continue
        m = LINE.match(s)
        if m and m.group(1) in KEYS:
            block[m.group(1)] = float(m.group(2))
    return [by_step[k] for k in sorted(by_step)]


def parse_tail(path, tail_bytes=16384):
    """Latest value of each metric, read from the tail of the log (cheap as the log grows)."""
    vals = {}
    try:
        size = os.path.getsize(path)
        with open(path) as f:
            if size > tail_bytes:
                f.seek(size - tail_bytes)
                f.readline()  # discard partial line
            data = f.read()
    except OSError:
        return vals
    for line in data.splitlines():
        m = LINE.match(line.strip())
        if m and m.group(1) in KEYS:
            vals[m.group(1)] = float(m.group(2))
    return vals


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("log", help="trainer stdout log path")
    ap.add_argument("--out", default="runs/train_metrics.json")
    ap.add_argument("--series", default="", help="if set, also write the full per-rollout series here (for chart history)")
    ap.add_argument("--interval", type=float, default=3.0)
    args = ap.parse_args()
    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    if args.series:
        os.makedirs(os.path.dirname(args.series) or ".", exist_ok=True)

    def write_atomic(path, obj):
        tmp = path + ".tmp"
        with open(tmp, "w") as f:
            json.dump(obj, f)
        os.replace(tmp, path)  # atomic so the viewer never reads a half-written file

    while True:
        write_atomic(args.out, parse_tail(args.log))
        if args.series:
            write_atomic(args.series, parse_series(args.log))
        time.sleep(args.interval)


if __name__ == "__main__":
    main()

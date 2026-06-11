"""Parse PPO trainer stdout logs into a tidy CSV for cross-version charting.

SB3 prints one table per iteration; each `| key | value |` row is a metric. This splits the log into those tables
and emits one CSV row per rollout (per run), so you can chart any metric vs total_timesteps across versions.

    python tools/parse_train_logs.py v2=/path/a.log v3=/path/b.log ... --out metrics/training_history.csv
"""
import argparse
import csv
import os
import re

ROW = re.compile(r"\|\s*([A-Za-z_]+)\s*\|\s*(-?\d+\.?\d*(?:[eE][-+]?\d+)?)\s*\|")
SEP = re.compile(r"^-{6,}$")


def parse_run(path):
    """Yield one dict of metrics per table block that has a total_timesteps."""
    try:
        with open(path) as f:
            lines = f.read().splitlines()
    except OSError:
        return
    block = {}
    for line in lines:
        if SEP.match(line.strip()):
            if "total_timesteps" in block:
                yield block
            block = {}
            continue
        m = ROW.match(line.strip())
        if m:
            block[m.group(1)] = float(m.group(2))
    if "total_timesteps" in block:
        yield block


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("runs", nargs="+", help="run=path pairs (e.g. v3=/tmp/.../bo906rdwn.output)")
    ap.add_argument("--out", default="metrics/training_history.csv")
    args = ap.parse_args()

    rows, cols = [], ["run", "total_timesteps"]
    for spec in args.runs:
        name, _, path = spec.partition("=")
        for block in parse_run(path):
            block["run"] = name
            rows.append(block)
            for k in block:
                if k not in cols:
                    cols.append(k)
    rows.sort(key=lambda r: (r["run"], r["total_timesteps"]))

    os.makedirs(os.path.dirname(args.out) or ".", exist_ok=True)
    with open(args.out, "w", newline="") as f:
        w = csv.DictWriter(f, fieldnames=cols)
        w.writeheader()
        w.writerows(rows)
    print(f"wrote {len(rows)} rows ({len(cols)} cols) across {len(set(r['run'] for r in rows))} runs -> {args.out}")
    for run in sorted(set(r["run"] for r in rows)):
        rr = [r for r in rows if r["run"] == run]
        last = rr[-1]
        print(f"  {run}: {len(rr)} rollouts, up to {int(last['total_timesteps']):,} steps, "
              f"final kill_rate={last.get('kill_rate','?')}")


if __name__ == "__main__":
    main()

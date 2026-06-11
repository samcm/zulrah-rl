"""Per-episode trace recording for the ghost/montage renderer.

Each episode -> one JSON file under traces/<run>/ep_<idx>.json containing the per-tick player/world
state, the action taken, and the reward. The renderer replays these (overlaying many as translucent
"ghosts"). Near-zero cost: we already read the full state every step.
"""
import json
import os


class EpisodeTracer:
    def __init__(self, run_dir, enabled=True):
        self.run_dir = run_dir
        self.enabled = enabled
        if enabled:
            os.makedirs(run_dir, exist_ok=True)
        self._frames = []
        self._meta = {}

    def begin(self, episode, offset, extra=None):
        self._frames = []
        self._meta = {"episode": int(episode), "offset": list(offset)}
        if extra:
            self._meta.update(extra)

    def record(self, raw, action, reward, components):
        if not self.enabled:
            return
        self._frames.append({
            "tick": raw.get("tick"),
            "action": int(action) if action is not None else None,
            "reward": round(float(reward), 4),
            "components": {k: round(float(v), 4) for k, v in (components or {}).items()},
            "player": raw.get("player"),
            "zulrah": raw.get("zulrah"),
            "snakelings": raw.get("snakelings", []),
            "clouds": raw.get("clouds", []),
        })

    def end(self, outcome, training_step=None):
        if not self.enabled:
            return None
        self._meta.update({
            "outcome": outcome,
            "length": len(self._frames),
            "training_step": training_step,
        })
        path = os.path.join(self.run_dir, f"ep_{self._meta['episode']:06d}.json")
        with open(path, "w") as f:
            json.dump({"meta": self._meta, "frames": self._frames}, f)
        return path

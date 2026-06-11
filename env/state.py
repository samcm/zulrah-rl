"""State-vector construction + normalization.

The server reports absolute tiles inside a dynamically-allocated instance, so absolute coordinates
differ every episode. We capture the per-episode instance *offset* from the player's spawn tile and
express everything in fixed arena-template coordinates, normalized to [0,1] / [-1,1]. This keeps the
observation Markov-complete and episode-invariant (the policy needs no memory; see the project brief).
"""
import numpy as np

# Zulrah arena template (canonical OSRS tiles) and the player's spawn within it.
SPAWN_TEMPLATE = (2268, 3068)
ARENA_X0, ARENA_X1 = 2256, 2277
ARENA_Y0, ARENA_Y1 = 3062, 3080
ARENA_W = ARENA_X1 - ARENA_X0  # 21
ARENA_H = ARENA_Y1 - ARENA_Y0  # 18
REL_SCALE = 16.0  # tiles, for relative-position normalization

FORMS = ["mage", "range", "melee"]
OVERHEADS = ["none", "magic", "missiles", "melee"]
STYLES = ["none", "mage", "range"]
LAST_ATTACKS = ["none", "range", "mage", "melee"]

# Observation layout (each entry: (label, width)); OBS_DIM is the sum.
LAYOUT = [
    ("form_onehot", 3),
    ("zulrah_present", 1),
    ("zulrah_hp_frac", 1),
    ("player_hp_frac", 1),
    ("player_prayer_frac", 1),
    ("overhead_onehot", 4),
    ("attack_style_onehot", 3),
    ("venomed", 1),
    ("poisoned", 1),
    ("last_attack_onehot", 4),
    ("last_attack_ago", 1),
    ("player_tile", 2),
    ("zulrah_tile", 2),
    ("player_rel_zulrah", 2),
    ("phase_frac", 1),
    ("sequence_frac", 1),
    ("supplies", 3),
    ("snakeling_count", 1),
    ("nearest_snakeling_rel", 2),
]
OBS_DIM = sum(w for _, w in LAYOUT)


def offset_from_spawn(raw):
    """Instance offset = player's first (spawn) tile minus the arena-template spawn tile."""
    p = raw["player"]
    return p["x"] - SPAWN_TEMPLATE[0], p["y"] - SPAWN_TEMPLATE[1]


def _onehot(value, options):
    v = np.zeros(len(options), dtype=np.float32)
    if value in options:
        v[options.index(value)] = 1.0
    return v


def _norm_tile(x, y, offset):
    tx, ty = x - offset[0], y - offset[1]
    nx = np.clip((tx - ARENA_X0) / ARENA_W, 0.0, 1.0)
    ny = np.clip((ty - ARENA_Y0) / ARENA_H, 0.0, 1.0)
    return np.float32(nx), np.float32(ny)


def build_observation(raw, offset):
    p = raw.get("player", {})
    z = raw.get("zulrah", {})
    present = bool(z.get("present"))

    parts = []
    parts.append(_onehot(z.get("form", "none"), FORMS))
    parts.append([1.0 if present else 0.0])
    parts.append([float(z.get("hp", 0)) / max(1.0, float(z.get("maxHp", 1))) if present else 0.0])
    parts.append([float(p.get("hp", 0)) / max(1.0, float(p.get("maxHp", 1)))])
    parts.append([float(p.get("prayer", 0)) / 99.0])
    parts.append(_onehot(p.get("overhead", "none"), OVERHEADS))
    parts.append(_onehot(p.get("attack_style", "none"), STYLES))
    parts.append([1.0 if p.get("venomed") else 0.0])
    parts.append([1.0 if p.get("poisoned") else 0.0])
    # most recent incoming attack: its type (which prayer it needed) + how long ago (the rhythm, for flicking)
    parts.append(_onehot(p.get("last_atk", "none"), LAST_ATTACKS))
    parts.append([float(np.clip(p.get("last_atk_ago", 99) / 6.0, 0.0, 1.0))])

    pnx, pny = _norm_tile(p.get("x", 0), p.get("y", 0), offset)
    parts.append([pnx, pny])

    if present:
        znx, zny = _norm_tile(z.get("x", 0), z.get("y", 0), offset)
        parts.append([znx, zny])
        dx = np.clip((p.get("x", 0) - z.get("x", 0)) / REL_SCALE, -1.0, 1.0)
        dy = np.clip((p.get("y", 0) - z.get("y", 0)) / REL_SCALE, -1.0, 1.0)
        parts.append([dx, dy])
        parts.append([float(z.get("phase", 0)) / 12.0])
        parts.append([float(z.get("sequence", 0)) / 15.0])
    else:
        parts.append([0.0, 0.0])
        parts.append([0.0, 0.0])
        parts.append([0.0])
        parts.append([0.0])

    supplies = raw.get("supplies", {})
    parts.append([
        float(supplies.get("food", 0)) / 20.0,
        float(supplies.get("antivenom", 0)) / 10.0,
        float(supplies.get("prayer", 0)) / 10.0,
    ])

    snakes = raw.get("snakelings", []) or []
    parts.append([min(len(snakes), 8) / 8.0])
    if snakes and present:
        px, py = p.get("x", 0), p.get("y", 0)
        nearest = min(snakes, key=lambda s: abs(s[0] - px) + abs(s[1] - py))
        parts.append([
            np.clip((px - nearest[0]) / REL_SCALE, -1.0, 1.0),
            np.clip((py - nearest[1]) / REL_SCALE, -1.0, 1.0),
        ])
    else:
        parts.append([0.0, 0.0])

    obs = np.concatenate([np.asarray(x, dtype=np.float32).ravel() for x in parts])
    assert obs.shape[0] == OBS_DIM, f"obs dim {obs.shape[0]} != {OBS_DIM}"
    return obs

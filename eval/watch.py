"""Live dashboard: watch the current policy fight Zulrah, and watch the policy improve.

Loads the newest training checkpoint, plays an episode in its own bot, and renders the fight top-down plus a
progress panel (inventory, kills, and a moving-average of how low Zulrah's HP got per episode = "the hill"). Reloads
the newest checkpoint each episode, so leave it running while training trains and watch it climb.

    python eval/watch.py
    SDL_VIDEODRIVER=dummy python eval/watch.py --steps 5     # headless self-test
"""
import argparse
import glob
import json
import os
import sys
from collections import deque

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import pygame
from stable_baselines3 import PPO

from env import ZulrahEnv
from env.actions import ACTION_NAMES

X0, X1, Y0, Y1 = 2256, 2277, 3062, 3080            # arena template bounds (north up)
TILE = 26
ARENA_W, ARENA_H = (X1 - X0 + 1) * TILE, (Y1 - Y0 + 1) * TILE
RIGHT, BOTTOM = 270, 120
TRAIN = 326                                   # training-health panel (entropy, KL, kill_rate, reward components...)
W, H = ARENA_W + RIGHT + TRAIN, ARENA_H + BOTTOM
TRAIN_METRICS_PATH = "runs/train_metrics.json"  # published by tools/train_metrics_sidecar.py

BG = (24, 26, 32)
PANEL = (30, 33, 41)
GRID = (40, 44, 54)
WATER = (26, 42, 66)
PLATFORM = (58, 64, 56)
WHITE = (235, 238, 245)
DIM = (120, 128, 140)
GOOD = (70, 200, 110)
BAD = (220, 90, 80)
FORM_COLOR = {"range": (70, 200, 110), "mage": (80, 150, 240), "melee": (220, 90, 80), "unknown": (150, 150, 150)}
OVERHEAD_COLOR = {"magic": (80, 150, 240), "missiles": (70, 200, 110), "melee": (220, 90, 80), "none": (90, 95, 105)}
NEEDED_PRAY = {"range": "missiles", "mage": "magic"}
ITEM_LABEL = {385: "shark", 6685: "brew", 12913: "anti-ven", 5952: "antidt", 2434: "pray-pot", 3024: "restore",
              9185: "c'bow", 11905: "trident", 9144: "bolts", 9244: "bolts"}
ZHP_MAX = 500.0


def latest_model(pattern):
    cands = glob.glob(pattern)
    return max(cands, key=os.path.getmtime) if cands else None


def to_px(x, y, off):
    return int((x - off[0] - X0) * TILE + TILE / 2), int((Y1 - (y - off[1])) * TILE + TILE / 2)


def bar(surf, x, y, w, h, frac, color, label=None, font=None):
    pygame.draw.rect(surf, (50, 54, 62), (x, y, w, h))
    pygame.draw.rect(surf, color, (x, y, int(w * max(0.0, min(1.0, frac))), h))
    if label and font:
        surf.blit(font.render(label, True, WHITE), (x + w + 6, y - 1))


def sparkline(surf, font, x, y, w, h, hist, lo=0.0, hi=ZHP_MAX):
    pygame.draw.rect(surf, (18, 20, 26), (x, y, w, h))
    surf.blit(font.render("Zulrah min HP / episode  (down = better)", True, DIM), (x, y - 16))
    # kill line at hp=0 (bottom)
    pygame.draw.line(surf, (60, 120, 70), (x, y + h - 1), (x + w, y + h - 1))
    if len(hist) >= 2:
        pts = []
        n = len(hist)
        for i, v in enumerate(hist):
            px = x + int(i / (n - 1) * (w - 1))
            py = y + int((1 - (max(lo, min(hi, v)) - lo) / (hi - lo)) * (h - 1))
            pts.append((px, py))
        pygame.draw.lines(surf, (90, 170, 240), False, pts, 2)


def load_train_metrics(path):
    try:
        with open(path) as f:
            return json.load(f)
    except Exception:
        return {}


def mini_spark(surf, font, x, y, w, h, hist, label, color, lo=None, hi=None, invert=False):
    """Small labelled sparkline with auto y-range. invert=True draws low values at the top (for 'down=better')."""
    pygame.draw.rect(surf, (18, 20, 26), (x, y, w, h))
    surf.blit(font.render(label, True, DIM), (x, y - 14))
    vals = [v for v in hist if v is not None]
    if len(vals) < 2:
        return
    lo = min(vals) if lo is None else lo
    hi = max(vals) if hi is None else hi
    if hi - lo < 1e-9:
        hi = lo + 1.0
    pts = []
    n = len(vals)
    for i, v in enumerate(vals):
        frac = (max(lo, min(hi, v)) - lo) / (hi - lo)
        if not invert:
            frac = 1 - frac
        pts.append((x + int(i / (n - 1) * (w - 1)), y + int(frac * (h - 1))))
    pygame.draw.lines(surf, color, False, pts, 2)
    surf.blit(font.render(f"{vals[-1]:.3g}", True, color), (x + w - 42, y + 2))


def draw(surf, font, big, raw, off, action, reward, ep, model_name, arena, stats, tm, train_hist, banner=None):
    surf.fill(BG)
    # --- arena map (water vs platform from server collision grid) ---
    if arena and arena.get("blocked"):
        for r, row in enumerate(arena["blocked"]):
            ty = Y0 + r
            for c, b in enumerate(row):
                pygame.draw.rect(surf, WATER if b else PLATFORM, ((c) * TILE, (Y1 - ty) * TILE, TILE, TILE))
    for gx in range(X1 - X0 + 2):
        pygame.draw.line(surf, GRID, (gx * TILE, 0), (gx * TILE, ARENA_H))
    for gy in range(Y1 - Y0 + 2):
        pygame.draw.line(surf, GRID, (0, gy * TILE), (ARENA_W, gy * TILE))

    p = raw.get("player", {})
    z = raw.get("zulrah", {})

    for cx, cy in raw.get("clouds", []) or []:
        px, py = to_px(cx, cy, off)
        s = pygame.Surface((TILE, TILE), pygame.SRCALPHA)
        s.fill((150, 90, 200, 110))
        surf.blit(s, (px - TILE // 2, py - TILE // 2))
    for sx, sy in raw.get("snakelings", []) or []:
        px, py = to_px(sx, sy, off)
        pygame.draw.circle(surf, (120, 200, 120), (px, py), 6)

    if z.get("present"):
        zx, zy = to_px(z["x"], z["y"], off)
        col = FORM_COLOR.get(z.get("form"), FORM_COLOR["unknown"])
        pygame.draw.circle(surf, col, (zx, zy), TILE)
        pygame.draw.circle(surf, WHITE, (zx, zy), TILE, 2)
        bar(surf, zx - TILE, zy - TILE - 9, TILE * 2, 6, z.get("hp", 0) / max(1, z.get("maxHp", 1)), col)

    px, py = to_px(p.get("x", 0), p.get("y", 0), off)
    pygame.draw.circle(surf, OVERHEAD_COLOR.get(p.get("overhead", "none"), DIM), (px, py), 11, 3)
    style = p.get("attack_style", "none")
    pygame.draw.circle(surf, {"mage": (80, 150, 240), "range": (70, 200, 110)}.get(style, (200, 200, 200)), (px, py), 6)

    # --- right panel: inventory + progress ---
    rx = ARENA_W
    pygame.draw.rect(surf, PANEL, (rx, 0, RIGHT, H))
    surf.blit(big.render("inventory", True, WHITE), (rx + 10, 8))
    inv = raw.get("inv", []) or []
    sw = 60
    for i, (iid, amt) in enumerate(inv[:28]):
        cx, cy = rx + 10 + (i % 4) * (sw + 4), 30 + (i // 4) * 30
        pygame.draw.rect(surf, (44, 48, 58), (cx, cy, sw, 26))
        lbl = ITEM_LABEL.get(iid, str(iid))
        surf.blit(font.render(lbl, True, WHITE), (cx + 3, cy + 1))
        surf.blit(font.render(f"x{amt}", True, DIM), (cx + 3, cy + 13))

    surf.blit(big.render("progress", True, WHITE), (rx + 10, 252))
    surf.blit(font.render(f"episodes {stats['episodes']}   kills {stats['kills']}", True, WHITE), (rx + 10, 276))
    surf.blit(font.render(f"Zulrah min-HP avg(20): {stats['avg_min_hp']:.0f}", True, GOOD), (rx + 10, 294))
    surf.blit(font.render(f"best (closest to kill): {stats['best_min_hp']:.0f}", True, GOOD), (rx + 10, 310))
    surf.blit(font.render(f"ep reward  last {stats['last_ret']:+.1f}  avg {stats['avg_ret']:+.1f}", True, WHITE), (rx + 10, 326))
    sparkline(surf, font, rx + 10, 366, RIGHT - 20, 100, stats["zhp_hist"])

    # --- training-health panel (the trainer's own rollout metrics, via the sidecar JSON) ---
    tx = ARENA_W + RIGHT
    pygame.draw.rect(surf, (26, 29, 37), (tx, 0, TRAIN, H))
    ts = tm.get("total_timesteps")
    surf.blit(big.render("training", True, WHITE), (tx + 12, 8))
    surf.blit(font.render(f"{int(ts):,} steps" if ts else "(waiting for trainer…)", True, DIM), (tx + 104, 12))

    yy = [34]

    def row(label, val, fmt="{:.3f}", color=WHITE):
        surf.blit(font.render(label, True, DIM), (tx + 12, yy[0]))
        surf.blit(font.render("-" if val is None else fmt.format(val), True, color), (tx + 172, yy[0]))
        yy[0] += 17

    def header(label):
        yy[0] += 5
        surf.blit(font.render(label, True, (150, 160, 180)), (tx + 12, yy[0]))
        yy[0] += 17

    kr = tm.get("kill_rate")
    rew = tm.get("ep_rew_mean")
    header("outcomes")
    row("kill_rate", kr, "{:.3f}", GOOD if (kr or 0) > 0 else BAD)
    row("death_rate", tm.get("death_rate"), "{:.3f}", BAD)
    row("left_rate", tm.get("left_rate"))
    row("ep_len_mean", tm.get("ep_len_mean"), "{:.1f}")
    row("ep_rew_mean", rew, "{:+.2f}", GOOD if (rew or -1) > 0 else BAD)
    row("fps", tm.get("fps"), "{:.0f}")
    header("ppo internals")
    row("entropy_loss", tm.get("entropy_loss"))
    row("approx_kl (KL)", tm.get("approx_kl"), "{:.4f}")
    row("explained_var", tm.get("explained_variance"))
    row("value_loss", tm.get("value_loss"))
    row("pg_loss", tm.get("policy_gradient_loss"), "{:.4f}")
    row("clip_frac", tm.get("clip_fraction"))
    header("reward components")
    row("kill", tm.get("kill"), "{:+.3f}", GOOD)
    row("dmg_dealt", tm.get("dmg_dealt"), "{:+.3f}", GOOD)
    row("pool_lost", tm.get("pool_lost"), "{:+.3f}", BAD)
    row("death", tm.get("death"), "{:+.3f}", BAD)

    sy = yy[0] + 12
    mini_spark(surf, font, tx + 12, sy, TRAIN - 24, 46, train_hist["kill_rate"], "kill_rate (up=better)", GOOD, lo=0.0)
    mini_spark(surf, font, tx + 12, sy + 70, TRAIN - 24, 46, train_hist["zmin"],
               "Zulrah min-HP / rollout (down=better)", (90, 170, 240), lo=0.0, hi=ZHP_MAX)
    mini_spark(surf, font, tx + 12, sy + 140, TRAIN - 24, 46, train_hist["entropy"], "entropy_loss", (210, 170, 90))

    # --- bottom HUD ---
    y = ARENA_H + 8
    bar(surf, 8, y, 150, 12, p.get("hp", 0) / max(1, p.get("maxHp", 1)), BAD, f"HP {p.get('hp',0)}/{p.get('maxHp',0)}", font)
    bar(surf, 8, y + 18, 150, 12, p.get("prayer", 0) / 99.0, (90, 160, 240), f"Prayer {p.get('prayer',0)}", font)
    need = NEEDED_PRAY.get(z.get("form"))
    prayable = bool(z.get("present")) and need is not None
    correct = prayable and p.get("overhead") == need
    badge = "OK" if correct else ("WRONG" if prayable else "-")
    line_col = GOOD if correct else (BAD if prayable else DIM)
    surf.blit(font.render(
        f"form {z.get('form','-')}  pray {p.get('overhead')} [{badge}]  style {style}  "
        f"last-hit {p.get('last_atk','-')} ({p.get('last_atk_ago','-')}t)  pool {p.get('pool','-')}  "
        f"rot{z.get('rotation','-')}/ph{z.get('phase','-')}  venom {'Y' if p.get('venomed') else '-'}",
        True, line_col), (8, y + 40))
    act = ACTION_NAMES[action] if action is not None and action < len(ACTION_NAMES) else "-"
    surf.blit(big.render(f"ep {ep}   action: {act}   reward {reward:+.2f}", True, WHITE), (8, y + 62))
    surf.blit(font.render(os.path.basename(model_name or "(no model yet)"), True, DIM), (8, y + 88))
    if banner:
        bc = {"kill": GOOD, "death": BAD, "left": (224, 162, 72), "timeout": DIM}.get(banner, DIM)
        txt = big.render(f" {banner.upper()} ", True, (10, 12, 16))
        bw, bh = txt.get_width() + 24, txt.get_height() + 18
        bx, by = (ARENA_W - bw) // 2, (ARENA_H - bh) // 2
        pygame.draw.rect(surf, bc, (bx, by, bw, bh))
        pygame.draw.rect(surf, WHITE, (bx, by, bw, bh), 2)
        surf.blit(txt, (bx + 12, by + 9))
    pygame.display.flip()


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--model", default="models/*.zip", help="path or glob; newest match loaded each episode")
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=43500)
    ap.add_argument("--max-steps", type=int, default=800)
    ap.add_argument("--steps", type=int, default=0, help="if >0, stop after N steps (headless self-test)")
    args = ap.parse_args()

    pygame.init()
    screen = pygame.display.set_mode((W, H))
    pygame.display.set_caption("Zulrah RL — watch")
    font = pygame.font.SysFont("menlo,monospace", 13)
    big = pygame.font.SysFont("menlo,monospace", 16, bold=True)
    clock = pygame.time.Clock()

    env = ZulrahEnv(host=args.host, port=args.port, max_steps=args.max_steps)
    model, model_name = None, None
    zhp_hist, ret_hist = deque(maxlen=60), deque(maxlen=60)
    train_hist = {k: deque(maxlen=300) for k in ("kill_rate", "zmin", "entropy")}
    last_train_step = None
    episodes, kills, best_min_hp = 0, 0, ZHP_MAX
    total, running = 0, True

    while running:
        path = latest_model(args.model) if "*" in args.model else (args.model if os.path.exists(args.model) else None)
        if path and path != model_name:
            try:
                m = PPO.load(path, device="cpu")
                if m.observation_space.shape == env.observation_space.shape:  # skip incompatible (old obs-dim) models
                    model, model_name = m, path
            except Exception:
                pass
        episodes += 1
        obs, info = env.reset()
        try:
            arena = env._client.map()
        except Exception:
            arena = None
        ep_ret, ep_min_hp, action, reward, done, trunc = 0.0, ZHP_MAX, None, 0.0, False, False
        stats, tm = None, {}
        while not (done or trunc) and running:
            for e in pygame.event.get():
                if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                    running = False
            if model is not None:
                try:
                    action, _ = model.predict(obs, deterministic=True)
                    action = int(action)
                except Exception:
                    action = 0  # obs/model shape drift mid-run: fall back, reload picks up a fresh model
            else:
                action = 0
            obs, reward, done, trunc, info = env.step(action)
            ep_ret += reward
            z = info["raw"].get("zulrah", {})
            if z.get("present"):
                ep_min_hp = min(ep_min_hp, float(z.get("hp", ZHP_MAX)))
            stats = {
                "episodes": episodes, "kills": kills, "best_min_hp": best_min_hp,
                "avg_min_hp": (sum(zhp_hist) / len(zhp_hist)) if zhp_hist else ZHP_MAX,
                "last_ret": ret_hist[-1] if ret_hist else 0.0,
                "avg_ret": (sum(ret_hist) / len(ret_hist)) if ret_hist else 0.0,
                "zhp_hist": list(zhp_hist),
            }
            tm = load_train_metrics(TRAIN_METRICS_PATH)
            tstep = tm.get("total_timesteps")
            if tstep is not None and tstep != last_train_step:  # new rollout published -> extend the trend lines
                train_hist["kill_rate"].append(tm.get("kill_rate"))
                train_hist["zmin"].append(tm.get("zulrah_min_hp_mean"))
                train_hist["entropy"].append(tm.get("entropy_loss"))
                last_train_step = tstep
            draw(screen, font, big, info["raw"], env._offset, action, reward, episodes, model_name, arena, stats,
                 tm, train_hist)
            clock.tick(30)
            total += 1
            if args.steps and total >= args.steps:
                running = False
        zhp_hist.append(ep_min_hp)
        ret_hist.append(ep_ret)
        best_min_hp = min(best_min_hp, ep_min_hp)
        outcome = info.get("outcome", "ongoing")
        if outcome == "kill":
            kills += 1
        # linger ~1.5s on the finished episode with an outcome banner — episodes are short (die/leave fast),
        # so without this the fights just flash past and the next reset stall reads as a freeze.
        if running and not args.steps and stats is not None:
            for _ in range(45):
                for e in pygame.event.get():
                    if e.type == pygame.QUIT or (e.type == pygame.KEYDOWN and e.key == pygame.K_ESCAPE):
                        running = False
                if not running:
                    break
                draw(screen, font, big, info["raw"], env._offset, action, reward, episodes, model_name, arena,
                     stats, tm, train_hist, banner=outcome)
                clock.tick(30)
    env.close()
    pygame.quit()


if __name__ == "__main__":
    main()

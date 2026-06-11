"""Reward: kill Zulrah as fast as possible. Collapsed to two aligned, positive terms — no penalties.

    reward = + W_dmg  * damage_dealt_to_zulrah        # dense DPS / progress breadcrumb
             + (on kill) W_kill * (1 + W_speed * time_remaining_frac)   # terminal, scaled UP for a faster kill

Why this shape (vs the old kill/dmg/pool/death weighting):
  * "Kill fast" is the whole objective, and it *subsumes* survival and prayer as INSTRUMENTAL, so they don't need
    their own penalty terms:
      - Dying is self-penalising — it ends the episode, forfeiting the (large) kill bonus and all future damage reward.
      - Taking damage is self-penalising — it costs attack-ticks to eat, which slows the kill.
    So a separate `death` penalty and `pool_lost` (damage-taken) penalty are redundant. Worse, `pool_lost` rewarded
    *not taking damage* = passivity, which plausibly fed the occasional-kill plateau. Dropped both.
  * We still need ONE dense term to bootstrap (a purely sparse "reward on kill" is the phantom-reward trap: before
    the agent can kill, it gets no signal). Damage-to-Zulrah is that term — and it doubles as the speed signal,
    because it is potential-based (Δ of -zulrah_hp) and PPO discounts future reward (γ<1): dealing the damage *sooner*
    (a faster kill) yields higher discounted return. So "fast" is rewarded by discounting; no explicit time term, and
    deliberately NO per-tick time penalty (that would reward suicide, since dying also ends the episode).
  * The kill bonus is additionally scaled by how much of the step budget was left when it died — a kill-conditional
    speed bonus, so it can never reward dying. Any kill is worth >= W_kill; a fast kill up to ~(1+W_speed)x that.
"""
from dataclasses import dataclass


@dataclass
class RewardConfig:
    w_dmg: float = 0.04     # dense progress / DPS signal (damage dealt to Zulrah this tick)
    w_kill: float = 30.0    # base kill bonus (the objective)
    w_speed: float = 1.5    # extra kill bonus for speed: kill = w_kill * (1 + w_speed * fraction_of_budget_left)


def compute(prev_raw, cur_raw, cfg: RewardConfig, steps=0, max_steps=1):
    """Returns (reward, components_dict). `steps`/`max_steps` let a faster kill earn more."""
    pz, cz = prev_raw.get("zulrah", {}), cur_raw.get("zulrah", {})

    damage_dealt = 0.0
    if pz.get("present") and cz.get("present"):
        damage_dealt = max(0.0, float(pz.get("hp", 0)) - float(cz.get("hp", 0)))

    kill_r = 0.0
    if cur_raw.get("outcome", "ongoing") == "kill":
        remaining = max(0.0, min(1.0, (max_steps - steps) / max(1, max_steps)))
        kill_r = cfg.w_kill * (1.0 + cfg.w_speed * remaining)

    comp = {
        "dmg_dealt": cfg.w_dmg * damage_dealt,
        "kill": kill_r,
    }
    return sum(comp.values()), comp

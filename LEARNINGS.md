# Training notes

How training actually went, version by version. Raw logs are in `logs/`, the
cross-version history in `metrics/training_history.csv`.

## Why

I wanted to learn RL by watching it happen, not by reading about it. The brief I
wrote for myself treats the project as a teaching artifact: each milestone makes one
PPO concept concrete and observable, and if you can't see it, it isn't done. Zulrah
because the fight is genuinely hard for a small network (four rotations, prayer
switching, phase timing), and because the payoff is watching a 20k-parameter MLP
prayer-flick a boss in a real 3D client. No LLM anywhere in the control loop.

Short version: v2-v7 tried six reward and hyperparameter fixes and none produced
reliable kills, because a full kill had never appeared in the agent's rollouts and
on-policy RL can't learn from outcomes it never samples. A reverse-HP curriculum (v8)
fixed that in the first rollout and climbed to reliable kills from ~340/500 starting
HP. v9 pushed the frontier to 395, then collapsed. The remaining gap looks like a
capability limit of the memoryless policy, not a tuning problem.

## What I learned

In rough order of how much each one cost me:

- The environment really is the hard part. The three worst bugs were an invalid
  config enum that silently ate every player hit, a wall-clock lock that put Zulrah
  to sleep at fast tick rates, and a 59% kill rate I celebrated before realizing the
  boss had never attacked once. None of them were RL.
- Whatever you reward densely is what gets optimized. My first reward had a clever
  anti-cheat term (penalize losing HP, prayer, and supplies) and it taught the agent
  that the safest move was to not fight. I spent a while hoping it "just needed more
  time to grok" before accepting the value function itself was the problem.
- Most penalty terms are traps. Death and taking damage are already self-penalizing,
  a time penalty rewards suicide, and discounting already prices speed. The reward
  that survived to the end was two positive terms and nothing else. Late in the
  project I caught myself again: when v8 stalled, the proposed fix was a
  prayer-accuracy reward term, and it felt wrong for the same reason. Rewarding a
  means instead of the end is how you die by a thousand shaping terms.
- On-policy RL cannot learn from a success it never samples. This one sentence
  explains five failed versions. Reward weights, entropy, and gamma all changed
  behavior, but none of them make the agent stumble into its first multi-minute
  kill. You have to manufacture the first success and grow it from there.
- Curricula fail in their own way. Mine advanced on the average kill rate over a
  band of difficulties, which the easy fights carry, so it kept promoting the agent
  past its actual ability. And sitting on an unwinnable level doesn't just stall,
  it actively grinds the policy back into the old chip-and-die behavior.
- Entropy is noise, not depth. Bumping the coefficient got first kills 2.5x earlier
  and did nothing about the plateau. The useful skill is reading entropy against
  kill rate: falling together with rising kills means commitment, falling with flat
  reward means collapse into a local optimum.
- With no death penalty, gamma is the price of dying. My v7 horizon hunch was right,
  the agent fought visibly better, and it still got zero kills. That was what
  finally convinced me the wall wasn't in the reward at all.
- Don't half-cheat the observations. I noticed very late that Zulrah's internal
  phase counters had been bolted into the state: privileged information that broke
  the "let it discover the pattern" rule, delivered in a form the network couldn't
  use anyway. If you give the policy information, give it what a human can actually
  perceive. The designed follow-up is a ring buffer of the last few surface
  positions, because that's literally what a human reads.
- Build the watching tools first. The dashboard and the 3D spectator client felt
  like indulgence and caught more problems than any metric: the asleep boss, the
  bot wandering out of the arena, the chip-and-die loop. Same lesson as the brief:
  if you can't see it, it isn't done.

## The environment was most of the work

The plan said Elvarg; Elvarg turned out to have no Zulrah at all. Switched to a
Zenyte-based server (Zyrox) with a complete 4-rotation Zulrah.

Two environment bugs ate more time than any RL problem:

- For hours, no player hit landed on any NPC. Offline-dev mode sets
  `combat_xp_rate=1`, an invalid enum value, so every hit's XP grant threw an
  exception that aborted the hit before it was scheduled.
- Cranking the server tick from 600ms to 25ms (for 24x-real-time training) silently
  disabled Zulrah. `Entity.lock()` was wall-clock based with a hardcoded 600ms/tick,
  so Zulrah's spawn lock outlasted entire episodes and it never attacked once. An
  early agent hit a celebrated 59% kill rate against a boss that was asleep. All
  results before the fix were thrown out, which is why the version numbering starts
  at v2.

Rules set before training and kept throughout: the env exposes faithful controls
(attack, equip, pray, eat, move), never strategy. State must be Markov, no LSTM. If
the agent does something dumb but high-reward, suspect the reward before the
algorithm.

## v2-v7: six fixes, zero kills

| version | change | outcome |
|---|---|---|
| v2 | shaped reward: +kill, +damage, -resource-pool-lost, -death | 0 kills in 1.4M steps |
| v3 | rebalanced weights (kill 30 / dmg 0.04 / pool 0.003 / death 1.5) | first kills at ~320k steps, plateau at ~1% |
| v4 | two positive terms only: damage + speed-scaled kill bonus | same plateau |
| v5 | entropy coef 0.01 → 0.04 | first kill 2.5x earlier, same plateau |
| v6 | entropy hold-then-decay schedule | flat |
| v7 | gamma 0.99 → 0.997 | 0 kills, but chips Zulrah to ~1/3 HP |

v2 learned to disengage. The pool penalty (HP + prayer + remaining supplies, meant to
stop the agent eating its way to a "perfect" score) dominated everything: -0.97 per
episode against +0.003 of damage reward, and per tick a landed hit earned less than
the retaliation hit cost. The safest policy under that reward is to not fight.

The redesign settled on a few rules. The objective is sparse (kill); shaping is dense
breadcrumbs whose total per episode stays under one kill, so they can't outvote it.
Whatever you reward densely is what actually gets optimized, so the dense term has to
be progress toward the kill (damage), and it's potential-based (Ng et al. 1999) so it
can't corrupt the optimum.

v4 is the reward that survived to the end (`env/reward.py`):

```
reward = 0.04 * damage_this_tick
       + on kill: 30 * (1 + 1.5 * fraction_of_time_left)
```

No death penalty: dying already forfeits the kill bonus and all future damage reward.
No damage-taken penalty: eating costs attack ticks, and an explicit penalty is the v2
passivity trap again. No per-tick time penalty: that rewards suicide, since dying
also stops the clock. Discounting already pays more for damage dealt sooner, and the
speed bonus only pays on a kill, so it can't make dying attractive.

v5/v6 gave a useful reading of the entropy curve (falling entropy with rising kills
is commitment; falling entropy with flat reward is collapse into a local optimum,
which was v3 exactly) but didn't break the plateau. Action noise doesn't randomly
complete a multi-minute boss kill.

v7 came from noticing that with no death penalty, the cost of dying is entirely the
discounted future reward you forfeit, so gamma sets how much the agent cares about
surviving. At gamma 0.99 the kill bonus reached the start of a 100-tick fight
discounted to 0.37; at 0.997, to 0.74. The agent fought visibly better and finished
with zero kills.

That left one diagnosis: the agent reliably got Zulrah to a third of its HP and died,
so a full kill had never appeared in any rollout. The terminal bonus was a phantom.
Not a reward bug, an exploration-depth wall.

## v8/v9: the curriculum

v8 spawns Zulrah at low HP, so a cold policy can kill it within a phase or two, and
raises the starting HP whenever the rolling kill rate at the current level clears a
threshold. The agent observes `zulrah_hp_frac`, so the policy conditions on
difficulty and extends its endgame backward as fights get longer. The boss itself is
unchanged; the curriculum only scaffolds training.

Tuning still mattered. Starting at 75 HP went nowhere (a cold policy lands about one
hit and 75 HP needs 5-6); 25 HP with lower entropy produced kills in the first
rollouts. From there it climbed 11 levels to a 375-HP start over ~5M steps, with
reliable kills from ~340. v2-v7 had managed zero kills in roughly 10M cumulative
steps.

At 375 it stalled for ~12.5k episodes, and worse than stalling: with kills out of
reach, the dense damage term is the only signal left, and it ground the policy back
into chip-and-die. v9 warm-started from the best pre-stall checkpoint (1.82M steps,
44% kills at 340) and switched to range-spawn, sampling each episode's start HP from
[frontier-100, frontier] so most fights stay winnable. That broke the 375 wall and
reached 395, the highest of any run, then collapsed the same way one level up: the
frontier advances on the band-average kill rate, which the easy fights carry, so it
overshoots what the agent can actually do. If retrying, gate advancement on kill
rate at the frontier, not over the band.

## Post-mortem

The network never came up during the project because it was never the visible
bottleneck: 35 floats → 128 → 128 → 13 action logits + value, ~20k parameters, no
memory. The closing audit found the real ceiling:

- The observation has `last_attack` (what already hit you) but nothing about the
  projectile in flight, so the agent is structurally one tick late. Survivable in
  slow phases, fatal in the fast final phase, which is exactly where the curriculum
  died.
- The `phase`/`sequence` features leak Zulrah's internal script counters, breaking
  the "let it discover the pattern" rule, and are useless anyway: fed without the
  rotation index, normalized as scalars when they're categorical, to a network that
  can't integrate them over time.

The designed-but-unrun fix keeps the no-LSTM rule: drop the leaked counters and add
a small ring buffer of the last few (surface-spot, style) dive events to the state.
The rotation literally is a sequence of those events; the recent surface positions
are what a human reads.

One measurement habit worth keeping: reward curves were excluded from cross-version
comparisons, since every version has a different reward function. Kill rate and
Zulrah's minimum HP per episode were the scoreboard.


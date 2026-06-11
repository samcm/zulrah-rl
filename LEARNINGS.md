# The learning process

This project set out to make RL concepts *watchable*: a small MLP, PPO, and a faithful Zulrah,
with every claim backed by a metric or a replay. This is the honest record of what was tried,
what failed, why it failed, and where it landed. Training logs are in `logs/`, the
cross-version history in `metrics/training_history.csv`, and the comparison chart in
`metrics/version_comparison.png`.

## The arc in one paragraph

A faithful environment was harder to build than the RL. Once the boss was real, five
reward/optimizer variants (v3–v7) each fixed a real, articulable flaw — passivity from a
dominating penalty term, suicide-safe speed incentives, premature entropy collapse, survival
mispriced by a too-short credit horizon — and every one plateaued at the same wall, because
on-policy RL can't learn from a success it never samples. A reverse-HP curriculum (v8)
manufactured that success and climbed from a 25-HP Zulrah to reliable kills at ~340/500 HP;
a range-spawn refinement (v9) pushed the frontier to a record 395 before the advancement rule
overshot true competence and the policy collapsed back into chip-and-die. The closing
diagnosis: the remaining gap to 500 is a *capability* limit of the chosen setup — a memoryless
policy that is blind to incoming attacks — not a tuning problem.

## Ground rules (set before any training)

- **The env exposes faithful controls, never strategy.** 13 discrete actions — attack, equip
  range/mage, the two protection prayers, eat, antivenom, restore prayer, four moves, drop
  prayer — and nothing that encodes *how* to fight. No scripted solver, no guard rails. The
  policy figures it out or it doesn't.
- **Markov state, memoryless policy.** If the agent needs to "remember" something, it goes in
  the state vector, not an LSTM. (This decision comes back at the very end.)
- **Suspect the reward first.** Every shaping term is a new Goodhart surface; if the agent does
  something dumb-but-high-reward, blame the reward before the algorithm.

## Part I — the environment is the hard part (M0–M2)

**The server.** The original plan used the Elvarg RSPS; it turned out canonical Elvarg has
**no Zulrah at all**. The hunt ended at a Zenyte-based server (Zyrox) with a faithful,
complete, all-4-rotation Zulrah at OSRS revision 180.

**The bridge.** No screen-scraping, no game client: a real server-side `Player` is logged in
through a Netty `EmbeddedChannel` stub and driven each game tick over a TCP control socket.
One socket = one bot = one `gymnasium.Env`. Exact lossless state every tick, deterministic
resets, cheap parallelism.

**The bug that taught the thesis.** For hours, player hits simply never landed on any NPC.
The cause: offline-dev mode left `combat_xp_rate=1`, an *invalid* enum value — every hit's XP
grant threw, and the exception aborted the hit before it was ever scheduled. The "suspect the
reward" instinct had to be suspended: it was the environment. It usually is.

**Throughput, and the bug that invalidated everything.** Real-time training ran at ~0.62
steps/s, so the server tick was cranked from 600ms to 25ms (24× real-time). An early agent
then hit a celebrated **59% kill rate** — until a closer look revealed the server's
`Entity.lock(ticks)` was *wall-clock* based with a hardcoded 600ms/tick. At 25ms ticks,
Zulrah's spawn lock outlasted the entire episode: **Zulrah never fired a single attack in any
accelerated run**. The 59% agent had never faced a real boss. The fix (scale locks by actual
tick duration) silently invalidated all prior results — which is why the version numbering
effectively restarts at v2, the first faithful run. Lesson: *speedups can change game
semantics, and a kill-rate curve can't tell you the boss is asleep — watching the replays did.*

## Part II — the reward saga, v2 → v7

| Version | Change | Result | Lesson |
|---|---|---|---|
| v2 "faithful" | Conserved-pool reward: +kill, +damage, −pool lost (HP+prayer+supplies), −death | 0 kills in >1.4M steps | The penalty dominated; aggression was net-negative |
| v3 "rebalanced" | Golden-rule re-weighting (kill 30 / dmg 0.04 / pool 0.003 / death 1.5) | **First kills** (~320k steps), plateau at ~1% | Better weights un-stall, but a penalty that rewards "don't get hit" still rewards passivity |
| v4 "kill fast" | **Two positive terms only**: dense damage + kill-conditional speed bonus | Occasional kills, same plateau | Right shape; the wall is elsewhere |
| v5 | Entropy coefficient 0.01→0.04 | First kill at 131k (~2.5× earlier), same plateau | Exploration *noise* ≠ exploration *depth* |
| v6 | Entropy hold-then-decay (0.08 for 40%, →0.005) | Flat | Good hygiene, not the bottleneck |
| v7 | γ 0.99→0.997, gae_λ 0.97 | 0 kills, but chips Zulrah to ~⅓ HP (min-HP 425→361) | Can *fight*, can't *finish* |

### v2: the conserved-pool reward learned to disengage

The v2 design was principled: converge on a *perfect* kill (no damage taken, minimal supplies)
by penalizing loss of a conserved resource pool — HP + prayer + the healing potential left in
the inventory — so the agent can't game the metric by eating right before the kill. In
practice the realized magnitudes were inverted: per episode the pool penalty was ~−0.97 while
the damage reward was ~+0.003, and per tick, landing a hit earned less than the retaliation
hit cost. **Aggression was net-negative, so the policy optimized "lose the least pool" —
i.e., it disengaged.** A textbook Goodhart: the metric said "don't get hit," the objective was
"kill the boss," and the policy chose the metric.

### The golden rule (the v2→v3 redesign)

The question asked at this point — *"is there a golden rule for this? the most important thing
is that it gets a kill, right?"* — produced the project's reward doctrine:

1. **Separate the objective from the shaping.** The objective is sparse (kill). Shaping terms
   are dense breadcrumbs, and their total per episode must stay below one unit of the
   objective, so the goal can never be out-voted.
2. **Whatever you reward densely is what the policy actually optimizes.** So the dense term
   must be the progress signal (damage to Zulrah), and any dense penalty must sit ~10× below it.
3. **Prefer potential-based shaping** (here Φ = −zulrah_hp, per Ng et al. 1999) — it provably
   can't corrupt the optimal policy, so it's safe to scale.

Priority order: kill > damage > death > efficiency — and efficiency gets annealed in *later*,
once kills are reliable. The right weights are really a schedule.

### v4: why the penalties had to go entirely

v3 still plateaued at ~1%, and the suspect was the surviving pool penalty. The v4 idea —
*"the only value function is killing Zulrah, and a scale of how fast"* — collapsed the reward
to two aligned positive terms (this is `env/reward.py`, unchanged through v9):

```
reward = 0.04 × damage_dealt_this_tick                      # dense progress breadcrumb
       + (on kill) 30 × (1 + 1.5 × fraction_of_time_left)   # terminal, bigger for faster
```

Every omission has a reason:

- **No death penalty** — dying already forfeits the kill bonus and all future damage reward.
- **No damage-taken penalty** — eating costs attack ticks, which slows the kill; and an
  explicit version is exactly the passivity incentive v2 demonstrated.
- **No per-tick time penalty** — it would reward suicide, since dying also stops the clock.
- **No separate speed term** — γ-discounting already pays more for the same damage dealt
  sooner; the kill bonus's speed multiplier is *kill-conditional*, so it can never make dying
  attractive.

### v5/v6: entropy — noise is not depth

The entropy reading rule that came out of this phase: *entropy falling while kill rate rises =
healthy commitment; entropy falling while reward stays flat = premature collapse into a local
optimum* — and the latter was exactly v3's signature. Raising the coefficient (v5) made first
kills come ~2.5× earlier; scheduling it (v6, explore hard then sharpen) was the right hygiene.
Neither broke the plateau, because no amount of per-action jitter randomly completes a
multi-phase, multi-minute boss kill. Exploration *noise* doesn't buy exploration *depth*.

### v7: γ is the price of dying

The sharpest single insight of the tuning era: **because v4 removed the death penalty,
survival is priced entirely by the discounted future reward forfeited at death — so γ
literally sets how much the agent cares about not dying.** At γ=0.99 the credit horizon
(~100 ticks) was shorter than a competent kill, and the end-of-fight payoff reached step 0
discounted to 0.99¹⁰⁰ ≈ 0.37. At γ=0.997 it's ≈ 0.74 — dying mid-fight became twice as
expensive. v7 ran its full 3.27M steps and visibly fought better (Zulrah down to ~⅓ HP,
death rate 0.98) — and finished with zero kills.

### The wall, named

After v7 the diagnosis was unavoidable: the agent reliably got Zulrah to a third of its HP and
died, so **it had never sampled a full kill — and PPO is on-policy: you cannot
gradient-descend toward an outcome that never appears in your rollouts.** The 30–75-point
terminal bonus was a phantom — present in the reward function, absent from the data. Not a
reward bug. An exploration-depth wall.

## Part III — the curriculum (v8/v9): manufacture the success, then grow it

**Design (v8).** `reset <hp>` spawns Zulrah at a chosen starting HP. Start low enough that a
cold policy kills it within a phase or two — so the terminal reward enters the data
distribution immediately — then ratchet the starting HP toward the real 500 whenever the
rolling kill rate at the current level clears a threshold. Two details mattered:

- The observation already includes `zulrah_hp_frac`, so the policy **conditions on
  difficulty** and extends its finish-the-kill behavior *backward* as fights lengthen.
- This is scaffolding during training, not a change to the task — the end state is the full,
  faithful boss. (The no-fake-Zulrah principle survives.)

**Tuning matters even here.** The first attempt (start 75 HP, high entropy) stayed at 1–3%
kills: a cold policy lands ~1 hit before dying and 75 HP needs 5–6, and high entropy churned
actions — moving or re-equipping interrupts the auto-attack, which is faithful OSRS mechanics
punishing indecision. Restarting at **25 HP** (killable in 1–2 hits) with lower entropy
jumped the cold kill signal to 8–13% immediately.

**The climb.** v8 climbed 11 levels, 25 → 375 starting HP over ~5M steps, with rolling kill
rates of 20–76% per level — from "never killed Zulrah" (v3–v7) to **reliably killing from
~340/500 HP, three-quarters of the real fight.**

**The trap.** At 375 the curriculum stalled — ~12,500 episodes at 0–7% kills against a 50%
advancement bar. Worse than stalling: grinding an unwinnable level actively *degraded* the
policy, because with kills out of reach the dense damage term is the only signal left, and it
grinds the policy back into exactly the chip-and-die behavior of v7.

**v9: warm-start + range-spawn.** The fix was two-part: warm-start from the best *pre-wall*
checkpoint (1.82M steps, 44% kills at 340 HP — checkpoint hygiene pays for itself), and
replace fixed-level spawns with **range-spawn**: each episode samples Zulrah's start HP
uniformly from `[frontier − 100, frontier]`, so most fights stay winnable and the policy can't
overfit a single unwinnable level. It broke the 375 wall on schedule — 335→350→365→380→**395,
the highest any version reached** — and then the same trap re-sprang one level higher: the
frontier advanced on the *band-average* kill rate (carried by the easy fights), overshot the
agent's true competence, and at 395 the kill rate peaked at 27% and collapsed to 0 over the
next 400k steps. Range-spawn delayed the failure mode; it didn't remove it. (If retrying:
gate advancement on kill rate *at the frontier*, not over the band.)

## Part IV — the post-mortem: the network nobody had discussed

The policy was never the headline: **35 floats → 128 → 128 → 13 action logits + a value head,
~20–25k parameters, no memory.** It was never discussed *because it was never the visible
bottleneck* — every observed blocker was the environment, the reward, or the data
distribution, and fixing those produced all of the progress. But the closing audit surfaced
two real findings, and they are the honest explanation of the ~360–380 ceiling:

1. **The observation is blind to the incoming attack.** It carries `last_attack` (what already
   hit you, and how long ago) but nothing about the projectile in flight — the agent is
   structurally one tick late. Survivable in slow phases; fatal in the fast jad-style phase,
   which is exactly where the curriculum died.
2. **The `phase`/`sequence` features were privileged-information leakage that didn't even
   work.** They peek at Zulrah's internal scripted counters (compromising the
   discover-it-yourself principle) — but were fed without the rotation index that would make
   them predictive, normalized as scalars when they're categorical, to a memoryless network
   that can't infer the rotation over time. The worst of both worlds: purity compromised
   *and* no benefit delivered.

The designed-but-not-yet-run fix stays true to the no-LSTM rule: drop the leaked counters and
add a small **ring buffer of the last few (surface-spot, style) one-hots** to the state.
Zulrah's rotation literally *is* a sequence of (position, style) dive events, and the recent
sequence of surface positions is exactly what a human reads to identify the rotation. That is
the next experiment.

## Observability: what made every diagnosis possible

| Signal | What it caught |
|---|---|
| `kill_rate` (reward-independent) | The v3–v7 plateau, regardless of what each reward claimed |
| `zulrah_min_hp_mean` (the "hill") | v7's real progress that kill rate hid |
| Reward-component breakdown | v2's pool penalty dominating = the passivity incentive |
| Policy entropy | Premature collapse (v3) vs healthy commitment |
| `explained_variance` | Critic health, watched closely after the γ change |
| `curriculum/zulrah_start_hp` + per-level kill rate | The climb, the 375 wall, the v9 collapse |
| Per-episode traces → replays | The asleep-Zulrah bug; prayer switches; the chip-and-die loop |

One discipline worth copying: `ep_rew_mean` was deliberately **excluded** from the
cross-version comparison — every version has a different reward function, so reward curves
aren't comparable. Kill rate and the min-HP hill were the honest scoreboard.

## Milestones scorecard

| Milestone | Concept | Outcome |
|---|---|---|
| M0 | the environment is the hard part | Proven repeatedly: the Elvarg dead end, the xp-rate hit bug, the tick-rate lock bug |
| M1 | the RL loop's shape | Random-agent baseline; traces and TensorBoard from day one |
| M2 | escaping the dead band | 8 bots learning *something*; what healthy PPO internals look like |
| M3 | reward shaping & Goodhart | The whole v2–v9 saga; truly achieved by v8's curriculum |
| M4 | throughput is the budget | 8→32 envs, tick 600ms→25ms, ~0.6→~175 steps/s — and the speedup itself broke game semantics once |
| M5 | overfitting | Rotations randomize every spawn (held-out eval never run); the real generalization lesson turned out to be the privileged-info leak |

## Where it landed

Curriculum RL took this setup from **zero kills in ~10M cumulative steps (v2–v7)** to
**reliable kills from ~340/500 HP and a 395 frontier (v8/v9)**. The remaining gap to the full
fight is, on the evidence, a capability limit of a memoryless policy that can't see incoming
attacks — with a designed observation fix (the dive-event ring buffer) waiting to be run.

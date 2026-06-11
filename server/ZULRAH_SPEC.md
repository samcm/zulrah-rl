# Zulrah Implementation Spec

Extracted from four open-source server-side Zulrah implementations, to be ported into
Elvarg (`server/elvarg-rsps`). This is the source-of-truth for mechanics; cite it when
implementing the Elvarg-native `ZulrahCombatMethod` + boss instance.

Sources:
- **dginovker** = `dginovker/RS-2009-317` @ master — `Server/src/main/java/plugin/npc/osrs/zulrah/` (complete; the parallel `plugin/activity/zulrah/` is an abandoned stub). 317 base — closest architecture, use as code skeleton.
- **Zenyte** = `Rims-Naps/Zyrox-Server` @ master — `src/main/java/com/zenyte/game/content/boss/zulrah/`. Most faithful; **only source with all 4 real rotations**. Use for rotation/tile/timing data.
- **Elderscape** = `FavyTeam/Elderscape_server` @ master (MIT) — `source/game/npc/impl/zulrah/`.
- **Valinor** = `broduer/Valinor` @ main — `Valinor-S/src/.../bosses/zulrah/`.

Coordinates below are canonical OSRS arena tiles (X≈2256–2277, Y≈3062–3080, plane 0).
Re-base them into the Elvarg Zulrah instance.

## A. Forms — NPC id → form → attack → counter-prayer
| Form (colour) | NPC id | Attack | Player prays |
|---|---|---|---|
| Serpentine / green = RANGED | **2042** | Ranged | Protect from **Missiles** |
| Magma / red = MELEE | **2043** | Melee (adjacent only) | Protect from **Melee** / stay ≥2 tiles |
| Tanzanite / blue = MAGIC | **2044** | Magic | Protect from **Magic** |
| Jad phase | reuses **2042** | alternates magic/range per shot | flick Magic↔Missiles |

All four references agree. NOTE: red (2043) is **melee**, avoidable by distance — none of the
servers model red as ranged. (dginovker adds +20000 to all ids: 22042/22043/22044, snakelings
22045-22047; everyone else uses 2042-2044, snakelings 2045/2046.)

## B. Rotations (Zenyte `Sequence[][][]`, all 4 — the authoritative set)
Structure: `sequences[rotation][stand][step]`. Each stand runs its attack/cloud/snakeling
steps, then `DIVE(position, nextForm)` submerges and re-emerges at next position/form. Last
stand of each rotation has no dive (loops). `rotation = random(0..3)` at spawn; re-randomised
when exhausted. Boss starts RANGED @ CENTER. The form fought during a stand = the form named
in the previous stand's DIVE.

### Rotation 1
```
S0: clouds(2269,3069 & 2272,3070), clouds(2266,3069 & 2263,3070), clouds(2273,3072 & 2273,3075), clouds(2263,3073 & 2263,3076) -> DIVE(CENTER, MELEE)
S1: Melee -> DIVE(CENTER, MAGIC)
S2: Magic x4 -> DIVE(SOUTH, RANGED)
S3: Ranged x5, snakeling(2263,3076), snakeling(2263,3073), clouds(2263,3070 & 2266,3069), clouds(2272,3069 & 2273,3072), snakeling(2273,3075), snakeling(2273,3077) -> DIVE(CENTER, MELEE)
S4: Melee -> DIVE(WEST, MAGIC)
S5: Magic x5 -> DIVE(SOUTH, RANGED)
S6: clouds(2269,3069 & 2272,3069), clouds(2263,3070 & 2266,3069), clouds(2263,3073 & 2263,3076), snakeling(2272,3071), snakeling(2273,3075), snakeling(2273,3077), snakeling(2273,3072) -> DIVE(SOUTH, MAGIC)
S7: Magic x5, snakeling(2263,3070), clouds(2266,3069 & 2269,3069), snakeling(2263,3076), clouds(2272,3069 & 2273,3072), snakeling(2263,3073) -> DIVE(WEST, RANGED)
S8: Flicking(start RANGED) [jad], clouds x4 -> DIVE(CENTER, MELEE)
S9: Melee -> DIVE(CENTER, RANGED)  [loops]
```
### Rotation 2
```
S0: clouds(2269,3069 & 2272,3070), clouds(2266,3069 & 2263,3070), clouds(2273,3072 & 2273,3075), clouds(2263,3073 & 2263,3076) -> DIVE(CENTER, MELEE)
S1: Melee -> DIVE(CENTER, MAGIC)
S2: Magic x4 -> DIVE(WEST, RANGED)
S3: clouds(2273,3072 & 2272,3069), clouds(2273,3075 & 2273,3078), clouds(2269,3069 & 2266,3069), snakeling(2266,3069), snakeling(2263,3070), snakeling(2263,3073), snakeling(2263,3076) -> DIVE(SOUTH, MAGIC)
S4: Magic x5, snakeling(2263,3076), snakeling(2263,3073), clouds(2263,3070 & 2266,3069), clouds(2272,3069 & 2273,3072), snakeling(2273,3075), snakeling(2273,3077) -> DIVE(CENTER, MELEE)
S5: Melee -> DIVE(EAST, RANGED)
S6: Ranged x5 -> DIVE(SOUTH, MAGIC)
S7: Magic x5, snakeling(2263,3070), clouds(2266,3069 & 2269,3069), snakeling(2263,3076), clouds(2272,3069 & 2273,3072), snakeling(2263,3073) -> DIVE(WEST, RANGED)
S8: Flicking(start RANGED) [jad], clouds x4 -> DIVE(CENTER, MELEE)
S9: Melee -> DIVE(CENTER, RANGED)  [loops]
```
### Rotation 3
```
S0: clouds(2269,3069 & 2272,3070), clouds(2266,3069 & 2263,3070), clouds(2273,3072 & 2273,3075), clouds(2263,3073 & 2263,3076) -> DIVE(EAST, RANGED)
S1: Ranged x5, snakeling(2273,3078), snakeling(2273,3075), snakeling(2273,3072) -> DIVE(CENTER, MELEE)
S2: clouds(2273,3078 & 2273,3075), snakeling(2273,3072), clouds(2272,3070 & 2269,3069), snakeling(2266,3069), clouds(2263,3070 & 2263,3073), snakeling(2263,3076), Melee -> DIVE(WEST, MAGIC)
S3: Magic x5 -> DIVE(SOUTH, RANGED)
S4: Ranged x5 -> DIVE(EAST, MAGIC)
S5: Magic x5 -> DIVE(CENTER, RANGED)
S6: clouds(2273,3078 & 2273,3075), clouds(2273,3072 & 2272,3069), clouds(2269,3069 & 2266,3069), snakeling(2263,3070), snakeling(2263,3076), snakeling(2263,3073) -> DIVE(WEST, RANGED)
S7: Ranged x5 -> DIVE(CENTER, MAGIC)
S8: Magic x5, clouds(2263,3076 & 2263,3073), clouds(2263,3070 & 2266,3069), snakeling(2269,3069), snakeling(2272,3069), snakeling(2273,3072) -> DIVE(EAST, RANGED)
S9: Flicking(start MAGIC) [jad] -> DIVE(CENTER, MAGIC)
S10: snakeling(2263,3076), snakeling(2263,3070), snakeling(2263,3072), snakeling(2273,3078) -> DIVE(CENTER, RANGED)  [loops]
```
### Rotation 4
```
S0: clouds(2269,3069 & 2272,3070), clouds(2266,3069 & 2263,3070), clouds(2273,3072 & 2273,3075), clouds(2263,3073 & 2263,3076) -> DIVE(EAST, MAGIC)
S1: snakeling(2272,3069), snakeling(2273,3078), snakeling(2273,3075), snakeling(2273,3072), Magic x5 -> DIVE(SOUTH, RANGED)
S2: Ranged x4, clouds(2263,3070 & 2269,3069), clouds(2266,3069 & 2272,3069) -> DIVE(WEST, MAGIC)
S3: snakeling(2263,3076), snakeling(2263,3073), snakeling(2266,3069), snakeling(2269,3069), Magic x4 -> DIVE(CENTER, MELEE)
S4: Melee, clouds(2263,3070 & 2269,3069), clouds(2266,3069 & 2272,3069) -> DIVE(EAST, RANGED)
S5: Ranged x4 -> DIVE(SOUTH, RANGED)
S6: snakeling x6 (2263,3076 / 2263,3073 / 2263,3070 / 2273,3072 / 2273,3075 / 2273,3078), clouds(2273,3075 & 2273,3078), clouds(2272,3069 & 2273,3072), clouds(2269,3069 & 2266,3069) -> DIVE(WEST, MAGIC)
S7: Magic x5, snakeling(2263,3076), snakeling(2263,3073), snakeling(2263,3070), snakeling(2266,3069) -> DIVE(CENTER, RANGED)
S8: Ranged x5 -> DIVE(CENTER, MAGIC)
S9: Magic x4, clouds(2263,3073 & 2266,3069), clouds(2263,3070 & 2263,3076), clouds(2269,3069 & 2272,3069) -> DIVE(EAST, RANGED)
S10: Flicking(start MAGIC) [jad] -> DIVE(CENTER, MAGIC)
S11: snakeling(2263,3076), snakeling(2263,3073), snakeling(2273,3075), snakeling(2273,3078)  [loops]
```
(Typo in source: rot1 S8 cloud `(2273,2075)` → `(2273,3075)`.)

dginovker/Elderscape/Valinor each encode only the classic "rotation 1". For the RL project,
M0–M4 can use a single fixed rotation; M5 randomises across all 4 above.

## C. Positions / tiles
Zulrah spawn/face positions (Zenyte `ZulrahPosition`, absolute):
```
SOUTH  : spawn (2266,3062), face (2268,3068)
WEST   : spawn (2257,3071), face (2268,3073)
CENTER : spawn (2266,3073), face (2268,3065)
EAST   : spawn (2276,3072), face (2270,3074)
```
Initial spawn = CENTER (2266,3073,0). Player start tile = **(2268,3068,0)**.

Player melee safespots (avoid red form): `(2272,3072) (2273,3072) (2264,3072) (2263,3072)`.
Rotation-1/2 ranged safespot: `(2274,3077)`.
Arena bounds: X 2256–2277, Y 3062–3080, plane 0. Zenyte instance = 7×7 chunks.

## D. Phase timing (1 tick = 600 ms)
- Attack cadence: every **3 ticks** per shot (all forms).
- Attacks per stand: magic/ranged 4–5 (per rotation data); Flicking(jad) 10 shots (8 if rotation 3).
- Melee stand ≈ 16 ticks (swings at tick 5 and 15).
- Dive/submerge ≈ 3 ticks down + ~3 ticks to re-emerge.
- Snakeling: attacks every 4 ticks, self-despawn at ~67 ticks, killed on Zulrah death.
- Venom cloud lifetime: **30 ticks** (~18 s).
Use this tick-accurate model (Zenyte+Valinor agree), NOT dginovker's random(20,30) stand timer.

## E. Venom / snakelings / clouds
- Venom cloud = game object **11700**; lifetime 30 ticks; `random(1,4)` venom/tick to player
  within 1 tile of cloud center. Cloud object placed 1 tile SW of named center tile.
- Snakelings: NPC id **2045** (sometimes 2046); small adds, path to player, max hit 15,
  attack every 4 ticks, despawn ~67 ticks, all killed on Zulrah death.

## F. Attack hit-vs-block rule (decision-relevant)
Zulrah always fires its current form's attack; the protection is enforced in the player's
hit-application: a correct overhead prayer reduces the NPC hit to **0** (full block, as OSRS).
- RANGED (2042): pray Missiles → 0, else damage (+venom 6 on hit). Max-hit basis ~41.
- MAGIC (2044): pray Magic → 0. Zenyte "magic" stand mixes in ~30% ranged shots.
- MELEE (2043): hits only if player within 1 tile and not on a safespot. On hit: ~random(41),
  stun 5 ticks, knockback, venom 6. You cannot deal melee damage TO Zulrah (capped 0).
- JAD/Flicking (2042): alternates mage/range each shot deterministically; flick prayer per shot.
Damage cap on player ≈ 50. Snakeling max hit 15.

## Port plan
1. Skeleton from dginovker's `ZulrahType` / `ZulrahSpot` / `ZulrahPattern` / `ZulrahNPC` /
   `ZulrahCombatHandler` layout (closest to a 317 base).
2. Rotation/tile/timing data from Zenyte's tables above (tick-accurate, all 4 rotations).
3. Form/prayer mapping: 2042=range/missiles, 2043=melee/distance, 2044=magic/magic, jad=flick.
4. Re-implement against Elvarg's NPC/CombatMethod/instance APIs (model on `JadCombatMethod`).

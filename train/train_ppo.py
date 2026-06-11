"""M2+: train PPO (MlpPolicy) on Zulrah via N parallel bots on one headless server.

    # run the server first: cd server/zyrox && gradlew runOfflineDev
    python train/train_ppo.py --n-envs 8 --timesteps 500000
    tensorboard --logdir runs/

Each SubprocVecEnv worker opens its own control-socket connection => its own headless bot + Zulrah instance, and
they all advance together each game tick. SB3 logs entropy / explained-variance / KL / clip; callbacks.ZulrahMetrics
adds kill rate, reward-component breakdown, episode length and how-low-Zulrah's-HP-got.
"""
import argparse
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from stable_baselines3 import PPO
from stable_baselines3.common.callbacks import CallbackList, CheckpointCallback
from stable_baselines3.common.monitor import Monitor
from stable_baselines3.common.vec_env import SubprocVecEnv, VecMonitor

from env import ZulrahEnv
from env.reward import RewardConfig
from train.callbacks import EntropyAnneal, HpCurriculum, ZulrahMetrics


def make_env(rank, host, port, max_steps, curriculum_hp=0):
    def _init():
        return Monitor(ZulrahEnv(host=host, port=port, max_steps=max_steps,
                                 reward_config=RewardConfig(), curriculum_hp=curriculum_hp))
    return _init


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--n-envs", type=int, default=8)
    ap.add_argument("--timesteps", type=int, default=500_000)
    ap.add_argument("--n-steps", type=int, default=256)
    ap.add_argument("--max-steps", type=int, default=400)
    ap.add_argument("--host", default="127.0.0.1")
    ap.add_argument("--port", type=int, default=43500)
    ap.add_argument("--logdir", default="runs")
    ap.add_argument("--name", default="ppo")
    ap.add_argument("--save", default="models/ppo_zulrah")
    ap.add_argument("--ckpt-freq", type=int, default=10_000, help="total env steps between checkpoints")
    ap.add_argument("--ent-coef", type=float, default=0.04,
                    help="entropy bonus; bumped from SB3's 0 / our old 0.01 because the kill/damage rewards are "
                         "large enough to drown a small entropy term (= premature collapse, the v3 plateau)")
    ap.add_argument("--ent-start", type=float, default=0.08,
                    help="v6: initial ent_coef the EntropyAnneal schedule holds at (overrides --ent-coef at "
                         "construction so PPO starts exploring hard)")
    ap.add_argument("--ent-end", type=float, default=0.005,
                    help="v6: ent_coef the schedule linearly decays to by progress=1.0")
    ap.add_argument("--ent-hold-frac", type=float, default=0.4,
                    help="v6: fraction of total timesteps to hold ent_start before decaying to ent_end")
    ap.add_argument("--gamma", type=float, default=0.997,
                    help="discount factor. 0.997 -> effective horizon ~333 ticks (was 0.99 / ~100). A Zulrah kill is "
                         "~100+ ticks and, since we removed the death penalty, survival is priced ONLY by discounted "
                         "future reward — so a longer horizon makes the agent value staying alive to finish the kill")
    ap.add_argument("--gae-lambda", type=float, default=0.97,
                    help="GAE lambda; nudged 0.95 -> 0.97 so advantage estimation looks further ahead (matches the longer horizon)")
    ap.add_argument("--resume", action="store_true",
                    help="continue from the newest <save>_<steps>_steps.zip checkpoint (crash recovery)")
    ap.add_argument("--init-from", default="",
                    help="warm-start: load policy weights from this checkpoint but run as a FRESH run (step count, "
                         "curriculum level and entropy schedule all reset). Use to restart from a clean pre-degradation "
                         "checkpoint without inheriting the bad one's step count.")
    ap.add_argument("--curriculum", action="store_true",
                    help="v8: reverse-HP curriculum. Spawn Zulrah at a low starting HP so the kill (and its terminal "
                         "reward) is reachable from a cold policy, then ramp the starting HP toward full as the rolling "
                         "kill rate clears a threshold — the cure for the v3-v7 ~0-kill exploration plateau")
    ap.add_argument("--curr-start-hp", type=int, default=75, help="Zulrah starting HP at curriculum level 0")
    ap.add_argument("--curr-max-hp", type=int, default=500, help="Zulrah full HP (final curriculum level)")
    ap.add_argument("--curr-step-hp", type=int, default=40, help="HP added per level-up")
    ap.add_argument("--curr-advance-threshold", type=float, default=0.55,
                    help="rolling kill rate at the current level needed to level up")
    ap.add_argument("--curr-min-episodes", type=int, default=60,
                    help="episodes required at a level before it can level up")
    args = ap.parse_args()

    init_curr_hp = args.curr_start_hp if args.curriculum else 0
    vec = SubprocVecEnv([make_env(i, args.host, args.port, args.max_steps, init_curr_hp) for i in range(args.n_envs)])
    vec = VecMonitor(vec)

    resume_ckpt = None
    if args.resume:
        import glob
        import re
        ckpts = glob.glob(f"{args.save}_*_steps.zip")
        if ckpts:
            resume_ckpt = max(ckpts, key=lambda p: int(re.search(r"_(\d+)_steps", p).group(1)))
    elif args.curriculum and os.path.exists("runs/curriculum_state.json"):
        os.remove("runs/curriculum_state.json")   # fresh run: don't inherit a previous run's curriculum level

    if resume_ckpt:
        print(f"[resume] loading {resume_ckpt}")
        model = PPO.load(resume_ckpt, env=vec, tensorboard_log=args.logdir, ent_coef=args.ent_start)
    elif args.init_from:
        print(f"[init-from] warm-starting weights from {args.init_from} (fresh run)")
        model = PPO.load(args.init_from, env=vec, tensorboard_log=args.logdir, ent_coef=args.ent_start)
    else:
        model = PPO(
            "MlpPolicy",
            vec,
            n_steps=args.n_steps,
            batch_size=min(256, args.n_steps * args.n_envs),
            n_epochs=10,
            gamma=args.gamma,
            gae_lambda=args.gae_lambda,
            ent_coef=args.ent_start,
            learning_rate=3e-4,
            policy_kwargs=dict(net_arch=[128, 128]),
            tensorboard_log=args.logdir,
            verbose=1,
        )
    callbacks = [
        ZulrahMetrics(),
        EntropyAnneal(args.ent_start, args.ent_end, args.timesteps, args.ent_hold_frac),
        CheckpointCallback(save_freq=max(args.ckpt_freq // args.n_envs, 1),
                           save_path=os.path.dirname(args.save) or "models",
                           name_prefix=os.path.basename(args.save)),
    ]
    if args.curriculum:
        callbacks.append(HpCurriculum(start_hp=args.curr_start_hp, max_hp=args.curr_max_hp, step_hp=args.curr_step_hp,
                                      advance_threshold=args.curr_advance_threshold, min_episodes=args.curr_min_episodes))
    callback = CallbackList(callbacks)
    try:
        model.learn(total_timesteps=args.timesteps, callback=callback,
                    tb_log_name=args.name, progress_bar=False,
                    reset_num_timesteps=not bool(resume_ckpt))
    finally:
        os.makedirs(os.path.dirname(args.save), exist_ok=True)
        model.save(args.save)
        vec.close()
        print(f"saved model to {args.save}")


if __name__ == "__main__":
    main()

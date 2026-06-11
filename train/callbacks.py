"""Custom TensorBoard metrics for Zulrah PPO (the observability heart of the tutorial).

SB3 already logs policy entropy, value-function explained variance, approx KL and clip fraction. This callback
adds the task-level metrics the brief calls for: kill rate, outcome breakdown, episode length, the reward-component
breakdown (the reward-hacking early-warning), and how low Zulrah's HP got (progress signal independent of reward).
"""
import json
import os
from collections import deque

import numpy as np
from stable_baselines3.common.callbacks import BaseCallback


class ZulrahMetrics(BaseCallback):
    def __init__(self, window=100, verbose=0):
        super().__init__(verbose)
        self.outcomes = deque(maxlen=window)        # "kill"/"death"/"left"/"timeout"
        self.ep_lengths = deque(maxlen=window)
        self.ep_rewards = deque(maxlen=window)
        self.zulrah_min_hp = deque(maxlen=window)   # lowest HP Zulrah reached that episode
        self.comp_sums = {}                          # running reward-component sums this rollout
        self.comp_steps = 0
        # per-env running trackers
        self._ep_min_hp = None
        self._ep_ret = None

    def _on_training_start(self):
        n = self.training_env.num_envs
        self._ep_min_hp = [500.0] * n
        self._ep_ret = [0.0] * n

    def _on_step(self):
        infos = self.locals["infos"]
        rewards = self.locals["rewards"]
        dones = self.locals["dones"]
        for i, info in enumerate(infos):
            # reward-component breakdown (averaged over the rollout)
            comp = info.get("reward_components")
            if comp:
                for k, v in comp.items():
                    self.comp_sums[k] = self.comp_sums.get(k, 0.0) + float(v)
                self.comp_steps += 1
            # track this episode's lowest Zulrah HP + return
            z = (info.get("raw") or {}).get("zulrah") or {}
            if z.get("present"):
                self._ep_min_hp[i] = min(self._ep_min_hp[i], float(z.get("hp", 500)))
            self._ep_ret[i] += float(rewards[i])

            if dones[i]:
                self.outcomes.append(info.get("outcome", "timeout"))
                self.ep_lengths.append(int(info.get("episode_steps", 0)))
                self.ep_rewards.append(self._ep_ret[i])
                self.zulrah_min_hp.append(self._ep_min_hp[i])
                self._ep_min_hp[i] = 500.0
                self._ep_ret[i] = 0.0
        return True

    def _on_rollout_end(self):
        if self.outcomes:
            n = len(self.outcomes)
            for name in ("kill", "death", "left", "timeout"):
                self.logger.record(f"zulrah/{name}_rate", sum(o == name for o in self.outcomes) / n)
            self.logger.record("zulrah/ep_reward_mean", float(np.mean(self.ep_rewards)))
            self.logger.record("zulrah/ep_length_mean", float(np.mean(self.ep_lengths)))
            self.logger.record("zulrah/zulrah_min_hp_mean", float(np.mean(self.zulrah_min_hp)))
        if self.comp_steps:
            for k, total in self.comp_sums.items():
                self.logger.record(f"reward_components/{k}", total / self.comp_steps)
            self.comp_sums, self.comp_steps = {}, 0


class HpCurriculum(BaseCallback):
    """Reverse-HP curriculum (v8): make the kill reachable, then make it hard.

    v3-v7 all plateaued at ~0 kills — the agent reliably chips Zulrah to ~1/3 HP and dies, so it NEVER experiences a
    full kill and the big terminal reward stays unreachable from a cold policy (an exploration-depth wall, not a reward
    bug). This starts Zulrah at a low HP so a kill lands within a couple of phases, then ramps the starting HP toward
    full as the rolling kill rate clears a threshold. The agent already observes zulrah_hp_frac, so the policy conditions
    on difficulty and extends its 'finish the kill' behaviour backward as the fight gets longer. The current HP is pushed
    to every SubprocVecEnv worker via env_method (set_attr would only hit the Monitor wrapper, not the inner env).
    """

    def __init__(self, start_hp=75, max_hp=500, step_hp=40, advance_threshold=0.55,
                 min_episodes=60, window=80, state_path="runs/curriculum_state.json", verbose=0):
        super().__init__(verbose)
        self.start_hp = int(start_hp)
        self.max_hp = int(max_hp)
        self.step_hp = int(step_hp)
        self.advance_threshold = float(advance_threshold)
        self.min_episodes = int(min_episodes)
        self.cur_hp = int(start_hp)
        self.recent = deque(maxlen=window)   # outcomes since the last level-up
        self._eps_at_level = 0
        self.state_path = state_path

    def _on_training_start(self):
        # Resume-safe: a --resume run (or crash recovery) continues the curriculum from the level it reached
        # instead of restarting at start_hp and re-climbing. train_ppo deletes this file on a fresh (non-resume) run.
        try:
            if self.state_path and os.path.exists(self.state_path):
                with open(self.state_path) as f:
                    st = json.load(f)
                self.cur_hp = max(self.cur_hp, int(st.get("cur_hp", self.cur_hp)))
                # _eps_at_level is NOT restored: the rolling `recent` window starts empty on resume, so the level's
                # competence must be re-measured from fresh episodes (gating on len(recent), below).
        except (OSError, ValueError):
            pass
        # Ensure every worker starts at the curriculum HP (the env constructor was seeded too, this is belt-and-braces).
        self.training_env.env_method("set_curriculum_hp", self.cur_hp)

    def _save_state(self):
        if not self.state_path:
            return
        try:
            tmp = self.state_path + ".tmp"
            with open(tmp, "w") as f:
                json.dump({"cur_hp": self.cur_hp, "eps_at_level": self._eps_at_level}, f)
            os.replace(tmp, self.state_path)
        except OSError:
            pass

    def _on_step(self):
        for info, done in zip(self.locals["infos"], self.locals["dones"]):
            if done:
                self.recent.append(info.get("outcome", "timeout"))
                self._eps_at_level += 1
        if self.cur_hp < self.max_hp and len(self.recent) >= self.min_episodes:
            kill_rate = sum(o == "kill" for o in self.recent) / len(self.recent)
            if kill_rate >= self.advance_threshold:
                self.cur_hp = min(self.max_hp, self.cur_hp + self.step_hp)
                self.training_env.env_method("set_curriculum_hp", self.cur_hp)
                self.recent.clear()
                self._eps_at_level = 0
        return True

    def _on_rollout_end(self):
        self.logger.record("curriculum/zulrah_start_hp", self.cur_hp)
        kr = (sum(o == "kill" for o in self.recent) / len(self.recent)) if self.recent else 0.0
        self.logger.record("curriculum/kill_rate_at_level", kr)
        self.logger.record("curriculum/eps_at_level", self._eps_at_level)
        self._save_state()


class EntropyAnneal(BaseCallback):
    """Hold-then-decay schedule for PPO's entropy coefficient (v6).

    SB3's ent_coef is a plain float, not schedulable like learning_rate, but PPO reads self.ent_coef fresh on every
    gradient update. So we mutate model.ent_coef between rollouts: explore hard at ent_start while the policy is still
    finding the fight, then linearly decay to ent_end so the late-training policy can sharpen instead of staying noisy.
    Resume-safe: it keys off num_timesteps, which is already advanced when loading a checkpoint.
    """

    def __init__(self, ent_start, ent_end, total_timesteps, hold_frac=0.4, verbose=0):
        super().__init__(verbose)
        self.ent_start = float(ent_start)
        self.ent_end = float(ent_end)
        self.total_timesteps = max(int(total_timesteps), 1)
        self.hold_frac = float(hold_frac)

    def _value(self, progress):
        if progress < self.hold_frac:
            return self.ent_start
        decay_span = max(1.0 - self.hold_frac, 1e-8)
        frac = min((progress - self.hold_frac) / decay_span, 1.0)
        return self.ent_start + (self.ent_end - self.ent_start) * frac

    def _apply(self):
        progress = self.num_timesteps / self.total_timesteps
        value = max(self._value(progress), 0.0)
        self.model.ent_coef = value
        self.logger.record("train/ent_coef_now", value)

    def _on_rollout_start(self):
        self._apply()

    def _on_step(self):
        return True

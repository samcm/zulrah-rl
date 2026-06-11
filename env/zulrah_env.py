"""gymnasium.Env wrapping the Zenyte control socket. One env == one socket == one headless bot."""
import gymnasium as gym
import numpy as np
from gymnasium import spaces

from . import reward as reward_mod
from . import state as state_mod
from .actions import NUM_ACTIONS
from .protocol import ControlClient, DEFAULT_HOST, DEFAULT_PORT
from .trace import EpisodeTracer


class ZulrahEnv(gym.Env):
    metadata = {"render_modes": []}

    # Curriculum spawns Zulrah at a random HP in [frontier-RANGE, frontier] each episode rather than always at the
    # frontier. Training only at a single (possibly unwinnable) frontier lets the dense damage reward train a degenerate
    # "chip and die" policy; mixing in the easier, winnable HPs keeps the kill behaviour alive and gives a smooth gradient.
    CURR_RANGE = 100
    CURR_MIN_HP = 25

    def __init__(
        self,
        host=DEFAULT_HOST,
        port=DEFAULT_PORT,
        max_steps=300,
        reward_config=None,
        trace_dir=None,
        curriculum_hp=0,
    ):
        super().__init__()
        self.host, self.port = host, port
        self.max_steps = max_steps
        self.reward_config = reward_config or reward_mod.RewardConfig()
        # Curriculum: starting HP Zulrah is set to on reset (0 = full HP). The training callback ramps this up via
        # set_curriculum_hp; the watcher/eval envs leave it at 0 so they always show the real full-HP fight.
        self.curriculum_hp = int(curriculum_hp)
        self.observation_space = spaces.Box(-1.0, 1.0, shape=(state_mod.OBS_DIM,), dtype=np.float32)
        self.action_space = spaces.Discrete(NUM_ACTIONS)

        self._client = None
        self._offset = (0, 0)
        self._prev_raw = None
        self._steps = 0
        self._episode = 0
        self._tracer = EpisodeTracer(trace_dir, enabled=trace_dir is not None) if trace_dir else None

    # -- lifecycle -------------------------------------------------------------
    def _ensure_client(self):
        if self._client is None:
            self._client = ControlClient(self.host, self.port)

    def set_curriculum_hp(self, hp):
        """Set Zulrah's per-episode starting HP. Called across SubprocVecEnv workers via env_method."""
        self.curriculum_hp = int(hp)
        return self.curriculum_hp

    def _sample_curriculum_hp(self):
        """A random HP in [frontier-RANGE, frontier]; 0 (full HP) when the curriculum is off."""
        frontier = self.curriculum_hp
        if frontier <= 0:
            return 0
        low = max(self.CURR_MIN_HP, frontier - self.CURR_RANGE)
        return int(self.np_random.integers(low, frontier + 1))

    def reset(self, *, seed=None, options=None):
        super().reset(seed=seed)
        hp = self._sample_curriculum_hp()
        try:
            self._ensure_client()
            raw = self._client.reset(hp)
        except (ConnectionError, OSError):
            self._client = None
            self._ensure_client()
            raw = self._client.reset(hp)

        self._offset = state_mod.offset_from_spawn(raw)
        self._prev_raw = raw
        self._steps = 0
        self._episode += 1
        obs = state_mod.build_observation(raw, self._offset)
        if self._tracer:
            self._tracer.begin(self._episode, self._offset)
            self._tracer.record(raw, None, 0.0, {})
        return obs, {"raw": raw, "outcome": raw.get("outcome", "ongoing")}

    def step(self, action):
        action = int(action)
        raw = self._client.step(action)
        self._steps += 1

        rew, components = reward_mod.compute(self._prev_raw, raw, self.reward_config, self._steps, self.max_steps)
        self._prev_raw = raw

        terminated = bool(raw.get("done", False))
        truncated = self._steps >= self.max_steps
        outcome = raw.get("outcome", "ongoing")

        obs = state_mod.build_observation(raw, self._offset)
        if self._tracer:
            self._tracer.record(raw, action, rew, components)
            if terminated or truncated:
                self._tracer.end(outcome if terminated else "timeout")

        info = {
            "raw": raw,
            "outcome": outcome,
            "reward_components": components,
            "episode_steps": self._steps,
        }
        return obs, float(rew), terminated, truncated, info

    def close(self):
        if self._client is not None:
            self._client.close()
            self._client = None

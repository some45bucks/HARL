from functools import partial
import gym
from gym.spaces import Box
from gym.wrappers import TimeLimit
import numpy as np

from .multiagentenv import MultiAgentEnv
from .manyagent_swimmer import ManyAgentSwimmerEnv
from .isaaclab_ant import IsaacLabAntEnv
from .obsk import get_joints_at_kdist, get_parts_and_edges, build_obs


def env_fn(env, **kwargs) -> MultiAgentEnv:  # TODO: this may be a more complex function
    # env_args = kwargs.get("env_args", {})
    return env(**kwargs)


env_REGISTRY = {}
env_REGISTRY["manyagent_swimmer"] = partial(env_fn, env=ManyAgentSwimmerEnv)
env_REGISTRY["isaac_factor_ant-v2"] = partial(env_fn, env=IsaacLabAntEnv)


# using code from https://github.com/ikostrikov/pytorch-ddpg-naf
class NormalizedActions(gym.ActionWrapper):
    def _action(self, action):
        action = (action + 1) / 2
        action *= self.action_space.high - self.action_space.low
        action += self.action_space.low
        return action

    def action(self, action_):
        return self._action(action_)

    def _reverse_action(self, action):
        action -= self.action_space.low
        action /= self.action_space.high - self.action_space.low
        action = action * 2 - 1
        return action


class MujocoMulti(MultiAgentEnv):
    def __init__(self, batch_size=None, **kwargs):
        super().__init__(batch_size, **kwargs)
        self.scenario = kwargs["env_args"]["scenario"]  # e.g. Ant-v2
        self.agent_conf = kwargs["env_args"]["agent_conf"]  # e.g. '2x3'
        self.log_info = {}
        (
            self.agent_partitions,
            self.mujoco_edges,
            self.mujoco_globals,
        ) = get_parts_and_edges(self.scenario, self.agent_conf)

        self.n_agents = len(self.agent_partitions)
        self.n_actions = max([len(l) for l in self.agent_partitions])
        self.obs_add_global_pos = kwargs["env_args"].get("obs_add_global_pos", False)

        self.agent_obsk = kwargs["env_args"].get(
            "agent_obsk", None
        )  # if None, fully observable else k>=0 implies observe nearest k agents or joints
        self.agent_obsk_agents = kwargs["env_args"].get(
            "agent_obsk_agents", False
        )  # observe full k nearest agents (True) or just single joints (False)

        if self.agent_obsk is not None:
            self.k_categories_label = kwargs["env_args"].get("k_categories")
            if self.k_categories_label is None:
                if self.scenario in ["Ant-v2", "manyagent_ant", "isaac_factor_ant-v2"]:
                    self.k_categories_label = "qpos,qvel,cfrc_ext|qpos"
                elif self.scenario in ["Humanoid-v2", "HumanoidStandup-v2"]:
                    self.k_categories_label = (
                        "qpos,qvel,cfrc_ext,cvel,cinert,qfrc_actuator|qpos"
                    )
                elif self.scenario in ["Reacher-v2"]:
                    self.k_categories_label = "qpos,qvel,fingertip_dist|qpos"
                elif self.scenario in ["coupled_half_cheetah"]:
                    self.k_categories_label = "qpos,qvel,ten_J,ten_length,ten_velocity|"
                else:
                    self.k_categories_label = "qpos,qvel|qpos"

            k_split = self.k_categories_label.split("|")
            self.k_categories = [
                k_split[k if k < len(k_split) else -1].split(",")
                for k in range(self.agent_obsk + 1)
            ]

            self.global_categories_label = kwargs["env_args"].get("global_categories")
            self.global_categories = (
                self.global_categories_label.split(",")
                if self.global_categories_label is not None
                else []
            )

        if self.agent_obsk is not None:
            self.k_dicts = [
                get_joints_at_kdist(
                    agent_id,
                    self.agent_partitions,
                    self.mujoco_edges,
                    k=self.agent_obsk,
                    kagents=False,
                )
                for agent_id in range(self.n_agents)
            ]

        # load scenario from script
        self.episode_limit = self.args.episode_limit

        self.env_version = kwargs["env_args"].get("env_version", 2)
        if self.env_version == 2:
            try:
                self.wrapped_env = NormalizedActions(gym.make(self.scenario))
            except gym.error.Error:
                self.wrapped_env = NormalizedActions(
                    TimeLimit(
                        partial(env_REGISTRY[self.scenario], **kwargs["env_args"])(),
                        max_episode_steps=self.episode_limit,
                    )
                )
        else:
            assert False, "not implemented!"
        self.timelimit_env = self.wrapped_env.env
        self.timelimit_env._max_episode_steps = self.episode_limit
        self.env = self.timelimit_env.env
        self.timelimit_env.reset()
        self.obs_size = self.get_obs_size()
        self.share_obs_size = self.get_state_size()*self.n_agents

        # COMPATIBILITY
        self.n = self.n_agents
        # self.observation_space = [Box(low=np.array([-10]*self.n_agents), high=np.array([10]*self.n_agents)) for _ in range(self.n_agents)]
        self.observation_space = [
            Box(low=-10, high=10, shape=(self.obs_size,)) for _ in range(self.n_agents)
        ]
        self.share_observation_space = [
            Box(low=-10, high=10, shape=(self.share_obs_size,))
            for _ in range(self.n_agents)
        ]

        acdims = [len(ap) for ap in self.agent_partitions]
        self.action_space = {a:

                Box(
                    self.env.action_space.low[sum(acdims[:0]) : sum(acdims[: 1])],
                    self.env.action_space.high[sum(acdims[:0]) : sum(acdims[: 1])],
                )
                for a in range(self.n_agents)

        }
        self.true_action_space = tuple(
            [
                Box(
                    self.env.action_space.low[sum(acdims[:a]) : sum(acdims[: a + 1])],
                    self.env.action_space.high[sum(acdims[:a]) : sum(acdims[: a + 1])],
                )
                for a in range(self.n_agents)
            ]
        )

        pass

    def step(self, actions):
        # need to remove dummy actions that arise due to unequal action vector sizes across agents
        flat_actions = np.concatenate(
            [
                actions[i][: self.true_action_space[i].low.shape[0]]
                for i in range(self.n_agents)
            ]
        )
        obs_n, reward_n, done_n, info_n = self.wrapped_env.step(flat_actions)
        self.steps += 1

        info = {}
        info.update(info_n)

        # if done_n:
        #     if self.steps < self.episode_limit:
        #         info["episode_limit"] = False   # the next state will be masked out
        #     else:
        #         info["episode_limit"] = True    # the next state will not be masked out
        if done_n:
            if self.steps < self.episode_limit:
                # the next state will be masked out
                info["bad_transition"] = False
            else:
                # the next state will not be masked out
                info["bad_transition"] = True

        # return reward_n, done_n, info
        rewards = [[reward_n]] * self.n_agents
        dones = [done_n] * self.n_agents
        infos = [info for _ in range(self.n_agents)]
        self.log_info = info
        return (
            self.get_obs(),
            np.concatenate([self.get_state() for _ in range(self.n_agents)], axis=1),
            rewards,
            dones,
            infos,
            self.get_avail_actions(),
        )

    def get_obs(self):
        """Returns all agent observat3ions in a list"""
        state = self.env._get_obs()
        obs_n = []
        for a in range(self.n_agents):
            agent_id_feats = np.zeros(self.n_agents, dtype=np.float32)
            agent_id_feats[a] = 1.0
            # obs_n.append(self.get_obs_agent(a))
            # obs_n.append(np.concatenate([state, self.get_obs_agent(a), agent_id_feats]))
            # obs_n.append(np.concatenate([self.get_obs_agent(a), agent_id_feats]))
            # obs_i = np.concatenate([state, agent_id_feats])
            # obs_i = (obs_i - np.mean(obs_i)) / np.std(obs_i)
            obs_n.append(state)
        return obs_n

    def get_obs_agent(self, agent_id):
        if self.agent_obsk is None:
            return self.env._get_obs()
        else:
            # return build_obs(self.env,
            #                       self.k_dicts[agent_id],
            #                       self.k_categories,
            #                       self.mujoco_globals,
            #                       self.global_categories,
            #                       vec_len=getattr(self, "obs_size", None))
            return build_obs(
                self.env,
                self.k_dicts[agent_id],
                self.k_categories,
                self.mujoco_globals,
                self.global_categories,
            )

    def get_obs_size(self):
        """Returns the shape of the observation"""
        if self.agent_obsk is None:
            return self.get_obs_agent(0).size
        else:
            return len(self.get_obs()[0])
            # return max([len(self.get_obs_agent(agent_id)) for agent_id in range(self.n_agents)])

    def get_state(self, team=None):
        # TODO: May want global states for different teams (so cannot see what the other team is communicating e.g.)
        state = self.env._get_obs()
        state_normed = (state - np.mean(state)) / np.std(state)
        share_obs = []
        for a in range(self.n_agents):
            share_obs.append(state_normed)
        return share_obs

    def get_state_size(self):
        """Returns the shape of the state"""
        return len(self.get_state()[0])

    def get_avail_actions(self):  # all actions are always available
        # return np.ones(shape=(self.n_agents, self.n_actions,))
        return None

    def get_avail_agent_actions(self, agent_id):
        """Returns the available actions for agent_id"""
        # return np.ones(shape=(self.n_actions,))
        return None

    def get_total_actions(self):
        """Returns the total number of actions an agent could ever take"""
        return (
            self.n_actions
        )  # CAREFUL! - for continuous dims, this is action space dim rather
        # return self.env.action_space.shape[0]

    def get_stats(self):
        return {}

    # TODO: Temp hack
    def get_agg_stats(self, stats):
        return {}

    def reset(self, **kwargs):
        """Returns initial observations and states"""
        self.steps = 0
        self.timelimit_env.reset()
        return self.get_obs(), np.concatenate([self.get_state() for _ in range(self.n_agents)], axis=1), self.get_avail_actions()

    def render(self, **kwargs):
        self.env.render(**kwargs)

    def close(self):
        pass

    def seed(self, seed):
        self.wrapped_env.seed(seed)

    def get_env_info(self):
        env_info = {
            "state_shape": self.get_state_size(),
            "obs_shape": self.get_obs_size(),
            "n_actions": self.get_total_actions(),
            "n_agents": self.n_agents,
            "episode_limit": self.episode_limit,
            "action_spaces": self.action_space,
            "actions_dtype": np.float32,
            "normalise_actions": False,
        }
        return env_info

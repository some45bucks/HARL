from harl.common.base_logger import BaseLogger
import time
import numpy as np

class MAMuJoCoLogger(BaseLogger):
    aver_episode_rewards = None

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        # Track global step count (incremented in per_step)
        self.step_count = 0  
        # Dictionary to store logs for each scalar key across steps
        # { "some_key": [value_step_1, value_step_2, ...], ... }
        self.other_data_log = {}
        self.total_reward = float("-inf")

    def get_task_name(self):
        return f"{self.env_args['scenario']}-{self.env_args['agent_conf']}"

    def per_step(self, other_data):

        # Increment the step counter by 1 each time per_step is called
        self.step_count += 1

        # Store the scalars from other_data
        if other_data is not None:
            for key, val in other_data.items():
                # Ensure a list for this key
                if key not in self.other_data_log:
                    self.other_data_log[key] = []
                
                # If val is a single-number array, convert to float
                # e.g. val might be np.array([3.14]) 
                if isinstance(val, np.ndarray) and val.size == 1:
                    val = val.item()  # e.g. convert np.array([3.14]) -> 3.14

                # If val is already an int/float, we just append as is
                # If it's a bigger array, you can decide how to handle it
                # (e.g., store the mean, store the entire array, etc.)
                self.other_data_log[key].append(float(val))

    def log_train(self, actor_train_infos, critic_train_info):
        """Log training information."""
        # log actor
        for agent_id in range(self.num_agents):
            for k, v in actor_train_infos[agent_id].items():
                agent_k = "agent%i/" % agent_id + k
                self.writter.add_scalar(agent_k, v, self.total_num_steps)
        # log critic
        for k, v in critic_train_info.items():
            critic_k = "critic/" + k
            self.writter.add_scalar(critic_k, v, self.total_num_steps)

    def episode_log(
        self,
        actor_train_infos,
        critic_train_info,
        actor_buffer,
        critic_buffer
    ):
        """
        Log information at the end of each episode. We also compute and print
        averages of the 'other_data' values we've collected over the episode.
        """
        # Typical environment-based logging code...
        self.total_num_steps = (
            self.episode
            * self.algo_args["train"]["episode_length"]
            * self.algo_args["train"]["n_rollout_threads"]
        )
        self.end = time.time()

        # -------------------------------------------------------------
        # 1) Compute & log averages for other_data collected this episode
        # -------------------------------------------------------------
        self.total_reward = 0
        if self.other_data_log:
            print("\n===== Averages for the episode =====")
            for key, values in self.other_data_log.items():
                # Convert all collected values to float (if needed)
                if "reward" in key.lower():
                    self.total_reward += np.sum(values)
                mean_val = np.mean(values)
                print(f"{key}: {mean_val}")
                # Example: log to tensorboard as a scalar
                self.writter.add_scalar(key, mean_val, self.total_num_steps)

            print("==============================================")
            print(
            "Env {} Task {} Algo {} Exp {} episodes {}/{} total num timesteps {}/{}, FPS {}.".format(
                self.args["env"],
                self.task_name,
                self.args["algo"],
                self.args["exp_name"],
                self.episode,
                self.episodes,
                self.total_num_steps,
                self.algo_args["train"]["num_env_steps"],
                int(self.total_num_steps / (self.end - self.start)),
            )
        )

            # Clear the collected data after logging
            self.other_data_log.clear()

            self.writter.add_scalar("Total_Reward", self.total_reward, self.total_num_steps)

            print("Total Reward is {}.".format(self.total_reward))
        

        critic_train_info["average_step_rewards"] = critic_buffer.get_mean_rewards()
        self.log_train(actor_train_infos, critic_train_info)

        print(
            "Average step reward is {}.\n".format(
                critic_train_info["average_step_rewards"]
            )
        )

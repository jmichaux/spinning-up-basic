from collections import deque
import random
import torch
from torch import optim
from tqdm import tqdm
from hyperparams import DISCOUNT, ENTROPY_WEIGHT, HIDDEN_SIZE, LEARNING_RATE, MAX_STEPS, POLYAK_FACTOR, REPLAY_SIZE, UPDATE_INTERVAL, UPDATE_START
from hyperparams import OFF_POLICY_BATCH_SIZE as BATCH_SIZE
from env import Env
from models import Critic, SoftActor, create_target_network, update_target_network
from utils import plot


env = Env()
actor = SoftActor(HIDDEN_SIZE)
critic_1 = Critic(HIDDEN_SIZE, state_action=True)
critic_2 = Critic(HIDDEN_SIZE, state_action=True)
value_critic = Critic(HIDDEN_SIZE)
target_value_critic = create_target_network(value_critic)
actor_optimiser = optim.Adam(actor.parameters(), lr=LEARNING_RATE)
critics_optimiser = optim.Adam(list(critic_1.parameters()) + list(critic_2.parameters()), lr=LEARNING_RATE)
value_critic_optimiser = optim.Adam(value_critic.parameters(), lr=LEARNING_RATE)
D = deque(maxlen=REPLAY_SIZE)


state, done, total_reward = env.reset(), False, 0
pbar = tqdm(range(1, MAX_STEPS + 1), unit_scale=1, smoothing=0)
for step in pbar:
  with torch.no_grad():
    if step < UPDATE_START:
      # To improve exploration take actions sampled from a uniform random distribution over actions at the start of training
      action = torch.tensor([[2 * random.random() - 1]])
    else:
      # Observe state s and select action a ~ μ(a|s)
      action = actor(state).sample()
    # Execute a in the environment and observe next state s', reward r, and done signal d to indicate whether s' is terminal
    next_state, reward, done = env.step(action)
    total_reward += reward
    # Store (s, a, r, s', d) in replay buffer D
    D.append({'state': state, 'action': action, 'reward': torch.tensor([reward]), 'next_state': next_state, 'done': torch.tensor([done], dtype=torch.float32)})
    state = next_state
    # If s' is terminal, reset environment state
    if done:
      pbar.set_description('Step: %i | Reward: %f' % (step, total_reward))
      plot(step, total_reward, 'sac')
      state, total_reward = env.reset(), 0

  if step > UPDATE_START and step % UPDATE_INTERVAL == 0:
    # Randomly sample a batch of transitions B = {(s, a, r, s', d)} from D
    batch = random.sample(D, BATCH_SIZE)
    batch = {k: torch.cat([d[k] for d in batch], dim=0) for k in batch[0].keys()}

    # Compute targets for Q and V functions
    y_q = batch['reward'] + DISCOUNT * (1 - batch['done']) * target_value_critic(batch['next_state'])
    policy = actor(batch['state'])
    action = policy.rsample()  # a(s) is a sample from μ(·|s) which is differentiable wrt θ via the reparameterisation trick
    weighted_sample_entropy = (ENTROPY_WEIGHT * policy.log_prob(action)).sum(dim=1)
    y_v = torch.min(critic_1(batch['state'], action.detach()), critic_2(batch['state'], action.detach())) - weighted_sample_entropy.detach()

    # Update Q-functions by one step of gradient descent
    value_loss = (critic_1(batch['state'], batch['action']) - y_q).pow(2).mean() + (critic_2(batch['state'], batch['action']) - y_q).pow(2).mean()
    critics_optimiser.zero_grad()
    value_loss.backward()
    critics_optimiser.step()

    # Update V-function by one step of gradient descent
    value_loss = (value_critic(batch['state']) - y_v).pow(2).mean()
    value_critic_optimiser.zero_grad()
    value_loss.backward()
    value_critic_optimiser.step()

    # Update policy by one step of gradient ascent
    policy_loss = -(critic_1(batch['state'], action) + weighted_sample_entropy).mean()
    actor_optimiser.zero_grad()
    policy_loss.backward()
    actor_optimiser.step()

    # Update target value network
    update_target_network(value_critic, target_value_critic, POLYAK_FACTOR)

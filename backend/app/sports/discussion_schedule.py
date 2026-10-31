"""Explicit participation for bounded synthetic sports discussions."""

from copy import deepcopy

from .agent_layer import is_sports_dossier, sports_agent_limit


def apply_sports_discussion_schedule(config: dict, dossier: str) -> dict:
    """Return a sports-only schedule; preserve personas and generic schedules.

    Every configured participant gets an OASIS turn each round. This guarantees
    selection, not a particular action or a valid vote. Existing run budgets
    and round caps still apply.
    """
    result = deepcopy(config)
    if not is_sports_dossier(dossier):
        return result
    result['sports_discussion_only'] = True
    agents = result.get('agent_configs', [])
    ids = [agent.get('agent_id') for agent in agents]
    if not agents or len(agents) > sports_agent_limit():
        raise ValueError('Sports discussion requires a nonempty, bounded agent roster')
    if any(type(agent_id) is not int for agent_id in ids) or len(set(ids)) != len(ids):
        raise ValueError('Sports discussion requires unique integer agent IDs')
    timing = result.setdefault('time_config', {})
    timing.update(agents_per_hour_min=len(agents), agents_per_hour_max=len(agents))
    for period in ('peak', 'off_peak', 'morning', 'work'):
        timing[f'{period}_activity_multiplier'] = 1.0
    for agent in agents:
        agent['active_hours'] = list(range(24))
        agent['activity_level'] = 1.0
    return result

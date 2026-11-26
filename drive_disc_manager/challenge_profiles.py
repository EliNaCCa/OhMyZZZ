"""Save a recommended lineup as one simultaneous group of character builds."""
from copy import deepcopy

from .challenge import DAMAGE_ROLES, agent_labels
from .combat_windows import window_requests
from .roster import agent_id, scanner_agent_key
from .store import digest


def lineup_profiles(profiles, mode, period, lanes, pool, wiki):
    labels = agent_labels(wiki)
    def canonical(name):
        return labels.get(agent_id(name), name)

    teams = [tuple(canonical(name) for name in lane['team']) for lane in lanes]
    actors = [name for team in teams for name in team]
    if (not teams or any(len(team) != 3 for team in teams)
            or len(set(actors)) != len(actors) or not set(actors) <= pool.keys()):
        raise ValueError('角色池或阵容已变化，请重新比较分队')
    key = digest([mode, period, [(lane['stage'], sorted(team), lane['conditions']) for lane, team in zip(lanes, teams)]])
    existing = [p for p in profiles if p.get('lineupId') == key]
    if existing:
        return profiles, existing[0]['name']

    groups = {p.get('simultaneous') for p in profiles}
    title = ' · '.join(filter(None, (mode, period)))
    group = title
    serial = 1
    while group in groups:
        serial += 1
        group = f'{title}（{serial}）'
    names = {p['name'] for p in profiles}
    additions = []
    for lane, team in zip(lanes, teams):
        for name in team:
            mates = [mate for mate in team if mate != name]
            owner = scanner_agent_key(wiki, name)
            templates = [p for p in profiles if p.get('enabled', True) and not p.get('needsTarget')
                         and canonical(p['request']['characterName']) == name
                         and (not p.get('agentKey') or p['agentKey'] == owner)
                         and {canonical(m['agent']) for m in p.get('squad', [])} == set(mates)]
            # Many rotations can refer to the same saved target and output windows.
            builds = {digest({k:v for k,v in p.items() if k not in
                             ('name','agentKey','enabled','simultaneous','lineupId','encounter')}) for p in templates}
            if len(builds) == 1:
                profile = deepcopy(templates[0])
            else:
                profile = dict(request=dict(characterName=name), enabled=False, needsTarget=True,
                               squad=[dict(agent=m, **rank_from(pool[m])) for m in mates], **rank_from(pool[name]))
            stem = f"{group} · {lane['stage']} · {name}"
            label = stem
            n = 1
            while label in names:
                n += 1
                label = f'{stem}（{n}）'
            names.add(label)
            profile.update(name=label, simultaneous=group, lineupId=key, agentKey=owner,
                           encounter=dict(stage=lane['stage'], **lane['conditions']))
            window_requests(profile)
            additions.append(profile)
    additions.sort(key=lambda p: pool[canonical(p['request']['characterName'])]['role'] not in DAMAGE_ROLES)
    return [*profiles, *additions], additions[0]['name']


def rank_from(stats):
    return {'mindscape': stats['mindscape']} if 'mindscape' in stats else {}


def bind_target(profile, target, wiki):
    from .roster import same_agent
    name = profile['request']['characterName']
    actor = target['request']['characterName']
    if not same_agent(wiki, name, actor) or not same_agent(wiki, actor, target['agent']):
        raise ValueError('请选择这位代理人的配装目标')
    if target.get('requiresClaimMapping'):
        raise ValueError('此目标还使用配装器库存的占用记录，请先在配装器核对')
    result = dict(profile, request=deepcopy(target['request']), sourceId=target.get('sourceId'),
                  agentKey=scanner_agent_key(wiki, actor), enabled=True)
    result.pop('needsTarget', None)
    window_requests(result)
    return result

"""Compare this account's teams across the stages of a single rotation."""
from itertools import combinations

from .optimizer import number


MODES = {'式舆防卫战': (3, 'S'), '危局强袭战': (2, '三星')}


def team_candidates(owned):
    return combinations(sorted(set(owned)), 3)


def agent_labels(wiki):
    from zzz_od.game_data.agent import AgentEnum
    from .roster import agent_id
    agents = [item.value for item in AgentEnum if item.value.agent_type.name != 'UNKNOWN']
    by_name = {agent_id(key): agent.agent_name for agent in agents for key in (agent.agent_id, agent.agent_name)}
    for uid, row in wiki.get('character', {}).items():
        label = row.get('CHS')
        if label:
            label = by_name.get(agent_id(label), label)
            for key in (uid, row.get('EN'), row.get('code'), label):
                if key: by_name[agent_id(key)] = label
    return by_name


def account_pool(snapshot, declared, wiki, *, known=(), labels=None):
    from zzz_od.game_data.agent import AgentEnum
    from .roster import agent_id
    from .store import observed_agents
    labels = agent_labels(wiki) if labels is None else labels
    agents = {item.value.agent_name:item.value for item in AgentEnum}
    pool = {}
    for name in sorted(observed_agents(snapshot) | set(declared) | set(known)):
        label = labels.get(agent_id(name), name)
        agent = agents.get(label)
        row = pool.setdefault(label, dict(role=agent.agent_type.value if agent else '',
                                          element=agent.dmg_type.value if agent else ''))
        row.update(declared.get(name, {}))
    if snapshot.get('networkPreview'):
        for field, key, owner, stat, maximum in [('characters','mindscape','key','mindscape',6),
                                                  ('wengines','modification','location','engineRank',5)]:
            for item in snapshot.get(field, []):
                if not isinstance(item, dict) or not isinstance(item.get(owner), str):
                    continue
                label = labels.get(agent_id(item[owner]), item[owner])
                value = item.get(key)
                if label in pool and type(value) is int and int(field == 'wengines') <= value <= maximum:
                    pool[label][stat] = value
    return pool


def encounter_shortlist(pool, stages, needed, familiar=(), budget=25000):
    """Rank by explicit stage conditions until a score or clear time is available."""
    if not 1 <= needed <= len(stages) <= 3:
        raise ValueError('请核对本期关卡数量')
    # A party may use more than one damage dealer. A main matching the enemy's
    # weakness and a character able to use the selected buff are separate facts.
    damage = {'强攻', '异常', '命破', '锋御'}
    familiar = [frozenset(team) for team in familiar]
    parties = []
    bits = {name:1 << i for i, name in enumerate(sorted(pool))}
    for team in team_candidates(pool):
        elements = {pool[a]['element'] for a in team if pool[a]['role'] in damage}
        if elements:
            roles = {pool[a]['role'] for a in team}
            investment = sum(pool[a].get('mindscape') == 6 and pool[a].get('engineRank') == 5 for a in team)
            links = max((len(ref.intersection(team)) for ref in familiar), default=0)
            parties.append((team, sum(bits[a] for a in team), elements, roles, investment, max(0,links-1)))
    options = []
    for stage in stages:
        rows = []
        for team, mask, elements, roles, investment, links in parties:
            if not stage.get('weak') and not stage.get('resist') and not stage.get('buffRole'):
                continue
            weak = stage.get('weak') in elements
            resisted = bool(stage.get('resist')) and elements == {stage['resist']}
            buff = stage.get('buffRole') in roles
            quality = int(weak) + int(buff) - int(resisted)
            # Among equal fits prefer covering more lanes with distinct carries.
            # Triple carries are still available if the player's roster requires it.
            surplus = sum(pool[a]['role'] in damage for a in team) - 1
            balance = int('击破' in roles) + int('支援' in roles)
            rows.append((quality, -surplus, team, mask, weak, buff, resisted, balance, investment, links))
        options.append(sorted(rows, key=lambda row:(-row[0],-row[1],-row[7],-row[8],-row[9],row[2])))
    best = None
    selected = []
    clipped = False
    for indexes in combinations(range(len(stages)), needed):
        if any(not options[i] for i in indexes):
            continue
        tops = [options[i][0] for i in indexes]
        def rank(rows):
            return (tuple(sorted(row[0] for row in rows)), sum(row[1] for row in rows),
                    sum(row[7] for row in rows), sum(row[8] for row in rows), sum(row[9] for row in rows))
        def visit(rows, mask):
            nonlocal best, selected, budget, clipped
            depth = len(rows)
            if best is not None and rank(rows + tops[depth:]) <= best:
                return
            if depth == needed:
                best, selected = rank(rows), list(zip(indexes, rows))
                return
            for row in options[indexes[depth]]:
                if budget <= 0:
                    clipped = True
                    return
                budget -= 1
                if mask & row[3]:
                    continue
                visit([*rows,row], mask | row[3])
                if rank(rows + tops[depth:]) == best:
                    return
        visit([], 0)
    return [dict(stage=i, team=row[2], weak=row[4], buff=row[5], resisted=row[6]) for i,row in selected], clipped


def reward_lineup(rotation, owned):
    """Compare per-encounter results in game units. Never reuse a score across buffs or bosses."""
    default_needed, rating = MODES[rotation['mode']]
    needed = rotation.get('needed', default_needed)
    stages = rotation['stages']
    if type(needed) is not int or not 1 <= needed <= len(stages) <= 3:
        raise ValueError('请核对本期关卡数量')
    if not rotation.get('period') or any(not s.get('name') for s in stages) or len({s['name'] for s in stages}) != len(stages):
        raise ValueError('需要期数和各路关卡名称')
    owned = set(owned)
    choices = []
    for stage in stages:
        target = number(stage['target'])
        if target <= 0 or stage['unit'] not in ('分', '秒'):
            raise ValueError('请填写本关的目标分数或通关时限')
        rows = []
        for record in stage.get('results', []):
            team = frozenset(record['team'])
            if len(team) != 3 or len(record['team']) != 3 or not team <= owned:
                continue
            value = number(record['value'])
            if value < 0 or (value == 0 and stage['unit'] == '秒'):
                raise ValueError('请核对关卡成绩')
            progress = target / value if stage['unit'] == '秒' else value / target
            resources = frozenset(('agent', agent) for agent in team)
            resources |= frozenset(('equipment', item) for item in record.get('gear', []))
            rows.append((resources, progress, record))
        choices.append(rows)

    best_key = best = None
    for indexes in combinations(range(len(stages)), needed):
        def visit(offset, used, picked):
            nonlocal best_key, best
            if offset == needed:
                scores = sorted(row[1] for row in picked)
                # Reward comes first. Next improve the weakest required clear's margin.
                key = (sum(score >= 1 for score in scores), *scores)
                if best_key is None or key > best_key:
                    best_key = key
                    best = [dict(stage=stages[i]['name'], team=row[2]['team'], value=row[2]['value'],
                                 unit=stages[i]['unit'], target=stages[i]['target'], passed=row[1]>=1)
                            for i, row in zip(indexes, picked, strict=True)]
                return
            for row in choices[indexes[offset]]:
                if used.isdisjoint(row[0]):
                    visit(offset+1, used | row[0], [*picked, row])
        visit(0, frozenset(), [])
    return dict(needed=needed, rating=rating, cleared=best_key[0] if best_key else 0, lineup=best or [])

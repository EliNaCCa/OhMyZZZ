"""Produce a read-only plan from one pinned inventory, configuration and rule revision."""
from collections import Counter, defaultdict
from .optimizer import as_disc, candidate_pool
from .allocation import assign_group
from .store import digest, fingerprint, inventory
from .subscriptions import effective


def quota(value):
    if value is not None and (type(value) is not int or value < 0):
        raise ValueError('备用数量应为非负整数或 null（不限）')
    return value


def reserves(rows, cfg):
    groups = defaultdict(list)
    for row in rows:
        if not row['reasons']:
            groups[(row['set'], row['slot'], row['main'])].append(row)
    for group, members in groups.items():
        criteria = dict(zip(('set','slot','main'),group))
        # Most specific matching override wins, later entries break exact ties.
        limits = {'通用':(-1,quota(cfg['reserve']),['crit_','crit_dmg_','atk_'])}
        for rule in cfg['reserve_rules']:
            selectors = {k:str(rule[k]) for k in criteria if k in rule and rule[k] not in ('','*')}
            if any(v!=criteria[k] for k,v in selectors.items()):
                continue
            purpose = rule.get('purpose') or '通用'
            rank = len(selectors)
            if purpose not in limits or limits[purpose][0] <= rank:
                limits[purpose] = (rank,quota(rule['count']), rule.get('stats',['crit_','crit_dmg_','atk_']))
        for purpose, (_,count,stats) in limits.items():
            def score(row):
                subs = row['rolls']
                return (sum(subs.get(k,0) for k in stats), (15-row['level'])//3, row['fp'])
            for row in sorted(members, key=score, reverse=True)[:count]:
                row['reasons'].append('备用 · '+purpose)


def plan(z,cfg,rules,names,fetch_candidates=candidate_pool):
    ds, counts = inventory(z)
    fields = rules.get('fields',{})
    by_fp = {fingerprint(d):d for d in ds}
    inputs = {fp:as_disc(d,names) for fp,d in by_fp.items() if counts[fp]==1 and not d.get('trash') and d.get('markState')!='unknown'}
    enabled = [p for p in cfg['profiles'] if p.get('enabled',True)]
    assignments, groups, issues = {}, defaultdict(list), []
    used_by = defaultdict(list)
    if len({p['name'] for p in enabled}) != len(enabled):
        raise ValueError('配队方案名称重复')
    for p in enabled:
        name = p['name']
        # Unnamed (mutually exclusive) profiles cannot collide with a named group.
        group = ('group',p['simultaneous']) if p.get('simultaneous') else ('solo',name)
        current = {f for f,d in by_fp.items() if d.get('location') and d['location']==p.get('agentKey')}
        groups[group].append((p,current))
    pool = [disc for disc in inputs.values() if disc and not disc['discarded']]
    for (_,group_name), members in groups.items():
        try:
            for profile,result in assign_group(members,pool,cfg['optimizer_url'],fetch_candidates):
                name = profile['name']
                assignments[name] = result
                for fp in result['ids']:
                    used_by[fp].append('配队 · '+name)
        except Exception as exc:
            issues.append(group_name+' · '+str(exc))
            for profile,_ in members:
                assignments[profile['name']] = {'error':str(exc)}
    rows = []
    for d in ds:
        fp = fingerprint(d)
        why = list(used_by[fp])
        if d.get('location'):
            why.append('已装备 · '+d['location'])
        if d.get('lock') is True or fp in cfg['pins']:
            why.append('手动保护')
        if cfg['protect15'] and d['level']==15:
            why.append('已满级')
        row = dict(fp=fp, id=d['id'], set=d['setKey'], slot=str(d['slotKey']), main=d['mainStatKey'],
                   level=d['level'], location=d.get('location',''), rolls={s['key']:1+s['upgrades'] for s in d['substats']}, reasons=why, review=[])
        row['marked'] = d.get('trash') is True
        if d.get('markState')=='unknown':
            row['review'].append('现场弃置状态待核')
        if counts[fp]>1 or d.get('setKey')=='Unknown' or d['rarity']!='S' or not 3<=len(row['rolls'])<=4:
            row['review'].append('身份、品级或副词条待核')
        rows.append(row)
    reserves([r for r in rows if not r['review'] and not r['marked']],cfg)
    for row in rows:
        key = 'matrix/'+row['set']+'/'+row['slot']
        if row['marked']:
            row.update(decision='已弃置',reasons=['游戏已标记弃置'],review=[])
            continue
        main = row['main']
        allowed = effective(fields.get(key),cfg['overrides'].get(key))
        known = fields.get(key+'/known',[])
        row['field'] = key
        if main in (allowed or []):
            row['reasons'].append('套装主词条规则 · '+key)
        for agent in cfg['roster']:
            if row['set'] not in agent.get('sets',[]):
                continue
            field = 'guide/'+agent['name']+'/slot'+row['slot']
            values = effective(fields.get(field), cfg['overrides'].get(field)) or []
            # A missing/ambiguous guide cell may not downgrade a candidate to trash.
            if not values or not agent.get('mainMap'):
                row['review'].append('角色用途待核 · '+agent['name'])
            elif any(main in agent['mainMap'].get(v,[]) for v in values):
                row['reasons'].append('角色攻略 · '+agent['name'])
            elif any(v not in agent['mainMap'] for v in values):
                row['review'].append('角色词条映射待核 · '+agent['name'])
        if main not in known and not row['reasons']:
            row['review'].append('缺少明确规则')
        if issues:
            row['review'].append('有启用配队尚未完成计算')
        row['decision'] = '保留' if row['reasons'] else ('待复核' if row['review'] else '弃置候选')
    return dict(inventory=digest(z), rules=rules.get('version'), configuration=digest(cfg), assignments=assignments,
                issues=issues, counts=dict(Counter(r['decision'] for r in rows)), discs=rows)

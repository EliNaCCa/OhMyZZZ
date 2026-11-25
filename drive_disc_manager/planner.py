"""Produce a read-only plan from one pinned inventory, configuration and rule revision."""
from collections import Counter, defaultdict
from .optimizer import as_disc, candidate_pool
from .allocation import assign_group
from .store import digest, fingerprint, inventory, roll_base
from .subscriptions import effective
from .reserve_policy import apply_reserves
from .roster import compile_roster, explain, roster_catalog


def plan(z,cfg,rules,names,fetch_candidates=candidate_pool,*,wiki=None,known_owned=()):
    ds, counts = inventory(z)
    base = roll_base(z)
    fields = rules.get('fields',{})
    catalog = roster_catalog(z, fields, cfg, wiki or {}, known_owned)
    by_set, unresolved = compile_roster(catalog, fields, cfg['overrides'])
    by_fp = {fingerprint(d):d for d in ds}
    inputs = {fp:as_disc(d,names,base) for fp,d in by_fp.items() if counts[fp]==1 and not d.get('trash') and d.get('markState')!='unknown'}
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
                   level=d['level'], location=d.get('location',''), rolls={s['key']:1+s['upgrades']-base for s in d['substats']}, reasons=why, recommendations=[], explanation=[], review=[])
        row['marked'] = d.get('trash') is True
        if d.get('markState')=='unknown':
            row['review'].append('现场弃置状态待核')
        if counts[fp]>1 or d.get('setKey')=='Unknown' or d['rarity']!='S' or not 3<=len(row['rolls'])<=4:
            row['review'].append('身份、品级或副词条待核')
        rows.append(row)
    for row in rows:
        key = 'matrix/'+row['set']+'/'+row['slot']
        if row['marked']:
            if counts[row['fp']] > 1:
                row.update(decision='待复核',reasons=['游戏已标记弃置，实物属性重复'])
            else:
                row.update(decision='已弃置',reasons=['游戏已标记弃置'],review=[])
            continue
        main = row['main']
        fixed = {'1':'hp','2':'atk','3':'def'}.get(row['slot']) == main
        verified_fixed = fixed and ('matrix/'+row['set']+'/4') in fields
        if verified_fixed:
            row['recommendations'].append('已知套装固定主属性')
        allowed = effective(fields.get(key),cfg['overrides'].get(key))
        known = fields.get(key+'/known',[])
        row['field'] = key
        if main in (allowed or []):
            row['recommendations'].append('套装主词条规则 · '+key)
        explain(row, by_set, unresolved)
        for agent in cfg['roster']:
            if row['set'] not in agent.get('sets',[]):
                continue
            if verified_fixed:
                row['recommendations'].append('角色套装 · '+agent['name'])
                continue
            field = 'guide/'+agent['name']+'/slot'+row['slot']
            values = effective(fields.get(field), cfg['overrides'].get(field)) or []
            # A missing/ambiguous guide cell may not downgrade a candidate to trash.
            if not values or not agent.get('mainMap'):
                row['review'].append('角色用途待核 · '+agent['name'])
            elif any(main in agent['mainMap'].get(v,[]) for v in values):
                row['recommendations'].append('角色攻略 · '+agent['name'])
            elif any(v not in agent['mainMap'] for v in values):
                row['review'].append('角色词条映射待核 · '+agent['name'])
        if main not in known and not (row['recommendations'] or row['reasons']):
            row['review'].append('缺少明确规则')
        if issues:
            row['review'].append('有启用配队尚未完成计算')
    coverage = apply_reserves(rows, cfg)
    for row in rows:
        if row['marked']:
            continue
        row['decision'] = '保留' if row['reasons'] else ('待复核' if row['review'] else '弃置候选')
    return dict(inventory=digest(z), rules=rules.get('version'), configuration=digest(cfg), assignments=assignments,
                issues=issues, roster=[a for a in catalog if a['selected']], reserveCoverage=coverage,
                counts=dict(Counter(r['decision'] for r in rows)), discs=rows)

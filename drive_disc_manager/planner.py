"""Produce a read-only plan from one pinned inventory, configuration and rule revision."""
from collections import Counter, defaultdict
from .optimizer import as_disc, candidate_pool, STATS
from .allocation import assign_group
from .store import digest, equipment_keys, fingerprint, inventory, roll_base
from .subscriptions import effective
from .reserve_policy import apply_reserves, development
from .roster import compile_roster, explain, roster_catalog
from .stat_reading import checked_values


def plan(z,cfg,rules,names,fetch_candidates=candidate_pool,*,wiki=None,known_owned=()):
    ds, _ = inventory(z)
    reverse = equipment_keys(names)
    def set_key(disc):
        raw = disc['setKey']
        return raw if raw in names else reverse.get(raw.strip(), raw)

    # The scanner can export a Chinese title when its dictionary lacks a new set.
    # Keep the raw title in physical fingerprints, so pins and history stay attached.
    optimizer_names = {d['setKey']:names[set_key(d)] for d in ds if set_key(d) in names}
    canonical_fps = {fingerprint(d):fingerprint(dict(d, setKey=set_key(d))) for d in ds}
    identities = Counter(canonical_fps[fingerprint(d)] for d in ds)
    base = roll_base(z)
    fields = rules.get('fields',{})
    catalog = roster_catalog(z, fields, cfg, wiki or {}, known_owned)
    by_set, unresolved = compile_roster(catalog, fields, cfg['overrides'])
    by_fp = {fingerprint(d):d for d in ds}
    inputs = {fp:as_disc(d,optimizer_names,base) for fp,d in by_fp.items()
              if identities[canonical_fps[fp]] == 1 and not d.get('trash') and d.get('markState')!='unknown'}
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
        if d.get('lock') is True:
            why.append('游戏内已锁定')
        if fp in cfg['pins']:
            why.append('手动保护')
        if cfg['protect15'] and d['level']==15:
            why.append('已满级')
        row = dict(fp=fp, id=d['id'], set=set_key(d), slot=str(d['slotKey']), main=d['mainStatKey'],
                   level=d['level'], location=d.get('location',''), rolls={s['key']:1+s['upgrades']-base for s in d['substats']}, reasons=why, recommendations=[], explanation=[], review=[])
        row['marked'] = d.get('trash') is True
        row['observedValues'] = checked_values(d, base)
        if 'observedValues' in d and not row['observedValues']:
            row['review'].append('本次扫描未核实完整的主副词条数值')
        if d.get('markState')=='unknown':
            row['review'].append('现场弃置状态待核')
        if d.get('lockState')=='unknown':
            row['review'].append('现场锁定状态待核')
        row['ambiguous'] = identities[canonical_fps[fp]] > 1
        if row['ambiguous'] or d.get('setKey')=='Unknown' or d['rarity']!='S' or not 3<=len(row['rolls'])<=4:
            row['review'].append('身份、品级或副词条待核')
        unknown = [stat for stat in row['rolls'] if stat not in STATS or STATS[stat][1] is None]
        if unknown:
            row['review'].append('无法识别的副词条 · '+'、'.join(unknown))
        if row['main'] not in STATS or STATS[row['main']][2] is None:
            row['review'].append('无法识别的主词条 · '+row['main'])
        if row['main'] in row['rolls']:
            row['review'].append('主副词条重复')
        if d['rarity']=='S':
            growth = development(row)
            if growth is None:
                row['review'].append('强化档数与等级不符')
            else:
                row['development'] = growth
                row['explanation'].append(f"初始 {growth['initial']} 词条 · 剩余 {growth['remaining']} 次强化机会")
                if cfg.get('protectNaturalCrit', True) and growth['initial']==4 and {'crit_', 'crit_dmg_'} <= row['rolls'].keys():
                    row['reasons'].append('原生四词条双暴保护')
        rows.append(row)
    for row in rows:
        key = 'matrix/'+row['set']+'/'+row['slot']
        if row['marked']:
            if row['ambiguous']:
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
            if row['set'] not in {reverse.get(s, s) for s in agent.get('sets',[])}:
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
    quotas = [dict(rule, set=reverse.get(rule['set'], rule['set'])) if 'set' in rule else rule
              for rule in cfg['reserve_rules']]
    coverage = apply_reserves(rows, dict(cfg, reserve_rules=quotas))
    for row in rows:
        if row['marked']:
            continue
        row['decision'] = '保留' if row['reasons'] else ('待复核' if row['review'] else '弃置候选')
    return dict(inventory=digest(z), rules=rules.get('version'), configuration=digest(cfg), assignments=assignments,
                issues=issues, roster=[a for a in catalog if a['selected']], reserveCoverage=coverage,
                counts=dict(Counter(r['decision'] for r in rows)), discs=rows)

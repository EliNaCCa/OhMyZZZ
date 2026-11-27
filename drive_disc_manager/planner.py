"""Produce a read-only plan from one pinned inventory, configuration and rule revision."""
from collections import Counter, defaultdict
from .optimizer import as_disc, candidate_pool, STATS
from .allocation import assign_group, group_conflict, reusable_assignments
from .drive_disc_manager_const import PLUGIN_VERSION
from .store import digest, equipment_keys, fingerprint, inventory, is_pinned, pin_keys, roll_base
from .subscriptions import effective, current_conflicts, SLOT_MAIN
from .reserve_policy import apply_reserves, assess_growth, normalized_rule
from .roster import compile_roster, explain, parse_main, preview_bindings, roster_catalog, scanner_agent_key
from .stat_reading import checked_values


def assess_rules(row, cfg, fields, reverse, by_set, unresolved, excluded):
    key = 'matrix/'+row['set']+'/'+row['slot']
    main = row['main']
    fixed = {'1':'hp','2':'atk','3':'def'}.get(row['slot']) == main
    verified_fixed = fixed and ('matrix/'+row['set']+'/4') in fields
    if verified_fixed:
        row['recommendations'].append('已知套装固定主属性')
        stat = {'1':'生命值', '2':'攻击力', '3':'防御力'}[row['slot']]
        row['ruleUses'].append(('matrix/'+row['set']+'/4', '', f'表里有这套盘，{row["slot"]} 号位固定为{stat}'))
    override = cfg['overrides'].get(key) or {}
    allowed = effective(fields.get(key),override)
    known = fields.get(key+'/known',[])
    excluded_locally = (key in fields and override.get('mode') == 'only'
                        and row['slot'] in ('4','5','6') and main in SLOT_MAIN[row['slot']] and main not in (allowed or ()))
    row['field'] = key
    if main in (allowed or []):
        row['recommendations'].append('套装主词条规则 · '+key)
    if row['slot'] in '456' or key in fields or key in cfg['overrides']:
        if excluded_locally:
            outcome = '个人「只接受」规则排除此主属性'
            row['explanation'].append(outcome)
        elif main in (allowed or []):
            outcome = '主属性符合'
        else:
            outcome = '主属性不符' if main in known else '该主属性缺少明确规则'
        row['ruleUses'].append((key, '', outcome))
    explain(row, by_set, unresolved, excluded)
    for agent in cfg['roster']:
        if row['set'] not in {reverse.get(s, s) for s in agent.get('sets',[])}:
            continue
        if verified_fixed:
            row['recommendations'].append('角色套装 · '+agent['name'])
            continue
        field = 'guide/'+agent['name']+'/slot'+row['slot']
        local = cfg['overrides'].get(field) or {}
        values = effective(fields.get(field), local) or []
        mappings = agent.get('mainMap') or {}
        direct, unknown = parse_main([value for value in values if value not in mappings])
        mapped = {stat for value in values if value in mappings for stat in mappings[value]}
        if main in direct or main in mapped:
            row['recommendations'].append('角色攻略 · '+agent['name'])
        elif not values and local.get('mode') != 'only':
            row['review'].append('角色用途待核 · '+agent['name'])
        elif unknown and any(value not in mappings for value in values):
            row['review'].append('角色词条映射待核 · '+agent['name'])
        row['ruleUses'].append((field, agent['name'], '个人套装与主属性映射'))
    if main not in known and not excluded_locally and not (row['recommendations'] or row['reasons']):
        row['review'].append('缺少明确规则')


def plan(z,cfg,rules,names,fetch_candidates=candidate_pool,*,wiki=None,known_owned=(),history=None,previous_preview=None,comparison=None,game_discards=()):
    ds, _ = inventory(z, preview_only=True)
    stats_only = z.get('networkPreview') is True and z.get('source') == 'zzz-drive-exporter'
    if game_discards:
        if not stats_only or not set(game_discards) <= {d['id'] for d in ds}:
            raise ValueError('弃置盘与本次国服导出不符')
        ds = [dict(d, trash=True, markState='marked') if d['id'] in game_discards else d for d in ds]
    showcase = z.get('showcase') or {}
    shown = showcase.get('owners', {}) if stats_only and showcase.get('uid') == cfg.get('showcaseUid') else {}
    if shown:
        ds = [dict(d, location=shown[d['id']], ownerState='worn', ownerSource='showcase')
              if d['id'] in shown and not d.get('location') else d for d in ds]
    reader_owners = {}
    if z.get('networkPreview') is True and wiki:
        for raw in {d['location'] for d in ds if d.get('location')}:
            key = scanner_agent_key(wiki, raw)
            if key and key != raw:
                reader_owners[raw] = key
        ds = [dict(d, location=reader_owners[d['location']]) if d.get('location') in reader_owners else d for d in ds]
    from .marks import marker_for, owns_spare, receipts_by_uid, scanned_identities
    history = history or {}
    uid_receipts = receipts_by_uid(history) if stats_only else {}
    keys, counts = scanned_identities(ds, names)
    reverse = equipment_keys(names)
    def set_key(disc):
        return keys[disc['setKey']]

    # The scanner can export a Chinese title when its dictionary lacks a new set.
    # Live actions and marker history still use the raw scanner fingerprint.
    optimizer_names = {d['setKey']:names[set_key(d)] for d in ds if set_key(d) in names}
    base = roll_base(z)
    fields = rules.get('fields',{})
    catalog = roster_catalog(z, fields, cfg, wiki or {}, known_owned)
    by_set, unresolved, excluded = compile_roster(catalog, fields, cfg['overrides'], reverse)
    if stats_only:
        slots = Counter((d['location'], d['slotKey']) for d in ds if d.get('location') and d.get('ownerSource') != 'showcase')
        if any(count > 1 for count in slots.values()):
            raise ValueError('同一代理人的一个号位有两张盘，请核对导出文件')
        ds = [dict(d, location='', ownerState='unknown', ownerSource='')
              if d.get('ownerSource') == 'showcase' and (d['location'], d['slotKey']) in slots else d for d in ds]
    last_worn = comparison['lastWorn'] if stats_only and comparison else {}
    owner_labels = {agent.get('code') or (agent.get('EN') or '').replace(' ', ''): agent['CHS']
                    for agent in (wiki or {}).get('character', {}).values() if agent.get('CHS')}
    by_piece = {}
    inputs = {}
    for d in ds:
        fp = fingerprint(d)
        piece = d['id'] if stats_only else fp
        by_piece[piece] = d
        usable = counts[fp] == 1 and d.get('markState') != 'unknown' and d.get('ownerState') != 'unknown'
        if not d.get('trash') and (stats_only or usable):
            candidate = as_disc(d, optimizer_names, base)
            if candidate and stats_only:
                candidate['id'] = piece
            inputs[piece] = candidate
    enabled = [p for p in cfg['profiles'] if p.get('enabled',True)]
    assignments, groups, issues = {}, defaultdict(list), []
    used_by = defaultdict(list)
    if len({p['name'] for p in enabled}) != len(enabled):
        raise ValueError('配队方案名称重复')
    bindings = preview_bindings(enabled, wiki or {})
    binding_errors = {}
    labels = None
    planned_actors = set()
    baselines = {}
    for p in enabled:
        name = p['name']
        owner = bindings[name]
        if p.get('agentKey') and p['agentKey'] != owner:
            binding_errors[name] = f'「{name}」的角色绑定与当前扫描器字典不一致，请更新所选配队'
        if p.get('simultaneous'):
            # The game roster may already know a character while the icon scanner lags.
            # Identify the build for planning only. Physical ownership still needs the scanner key.
            from .challenge import agent_labels
            from .roster import agent_id
            if not owner and labels is None:
                try:
                    labels = agent_labels(wiki or {})
                except ModuleNotFoundError as exc:
                    if exc.name != 'zzz_od':
                        raise
                    labels = {}
            actor = owner or labels.get(agent_id(p['request'].get('characterName', '')))
            if actor and not owner:
                actor = scanner_agent_key(wiki or {}, actor) or actor
            if not actor:
                binding_errors[name] = f'「{name}」的角色名无法识别，请核对配装目标'
            elif (p['simultaneous'], actor) in planned_actors:
                binding_errors[name] = f'同一出场组里有两套{actor}的配装'
            planned_actors.add((p['simultaneous'], actor))
        # Unnamed (mutually exclusive) profiles cannot collide with a named group.
        group = ('group',p['simultaneous']) if p.get('simultaneous') else ('solo',name)
        current = {piece for piece, d in by_piece.items() if owner and d.get('location') == owner}
        if stats_only:
            # Use one observation for all six slots; never fill its gaps from older scans.
            direct = {piece for piece in current if by_piece[piece].get('ownerSource') != 'showcase'}
            if direct:
                source, current = 'export', direct
            elif current:
                source = 'showcase'
            else:
                source = 'scan'
                current = {piece for piece, actor in last_worn.items() if owner and actor == owner and not by_piece[piece].get('location')}
            if len(current) != 6 or {str(by_piece[piece]['slotKey']) for piece in current} != set('123456'):
                current = set()
            baselines[name] = source if current else 'unknown'
        groups[group].append((dict(p, agentKey=owner), current))
    pool = [disc for disc in inputs.values() if disc and not disc['discarded']]
    inventory_hash = digest(z)
    allocation_key = digest([PLUGIN_VERSION, 'full-crit', inventory_hash, enabled, bindings, optimizer_names, reader_owners, last_worn, shown, cfg['optimizer_url']]) if enabled else None
    if allocation_key and game_discards:
        allocation_key = digest([allocation_key, sorted(game_discards)])
    saved = reusable_assignments(previous_preview, allocation_key, groups, pool)
    for (_,group_name), members in groups.items():
        try:
            errors = [binding_errors[p['name']] for p, _ in members if p['name'] in binding_errors]
            if errors:
                raise ValueError(' / '.join(errors))
            conflict = group_conflict(p for p, _ in members)
            if conflict:
                raise ValueError(conflict)
            results = ([(p, saved[p['name']]) for p, _ in members] if saved is not None else
                       assign_group(members, pool, cfg['optimizer_url'], fetch_candidates))
            for profile,result in results:
                name = profile['name']
                assignments[name] = dict(result, baselineSource=baselines[name]) if stats_only else result
                for fp in result['ids']:
                    used_by[fp].append('配队 · '+name)
        except Exception as exc:
            issues.append(group_name+' · '+str(exc))
            for profile,_ in members:
                assignments[profile['name']] = {'error':str(exc)}
    rows = []
    pins = set(cfg['pins'])
    for d in ds:
        fp = fingerprint(d)
        piece = d['id'] if stats_only else fp
        why = list(used_by[piece])
        if d.get('location'):
            label = owner_labels.get(d['location'], d['location'])
            why.append(('角色展柜 · ' if d.get('ownerSource') == 'showcase' else '已装备 · ') + label)
        values = checked_values(d, base)
        uid = d['id'] if stats_only else d.get('exportUid')
        entry = marker_for(history, fp, uid)
        receipt = uid_receipts.get(uid, {}) if stats_only else entry
        if stats_only and receipt:
            entry = receipt  # Preserve the player's later manual unlock or mark reversal across translations.
        has_spare_receipt = (counts[fp] == 1 and d.get('lockState') == 'locked' and d.get('lock') is True
                             and owns_spare(receipt, values, uid=uid))
        # The CN export can establish a lock's origin for ranking. Only game
        # controls can authorize its release, through a separate inspection plan.
        managed = has_spare_receipt and (stats_only or (d.get('markState') == 'unmarked' and d.get('trash') is False))
        if d.get('lock') is True and not managed:
            why.append('游戏内已锁定')
        manual_unlock = entry.get('state') == 'manual-unlock'
        if manual_unlock:
            why.append('手动解锁后保留')
        if entry.get('state') == 'manual-keep':
            why.append('手动撤销弃置后保留')
        if cfg['protect15'] and d['level']==15:
            why.append('满级盘（+15）')
        row = dict(fp=fp, id=d['id'], set=set_key(d), slot=str(d['slotKey']), main=d['mainStatKey'],
                   level=d['level'], location=d.get('location',''), rolls={s['key']:1+s['upgrades']-base for s in d['substats']}, reasons=why, recommendations=[], explanation=[], review=[], ruleUses=[])
        row['pinKeys'] = pin_keys(d, names, reverse, fp)
        if stats_only or z.get('source') == 'OhMyZZZ CN':
            row['exportUid'] = d['id']
            row['pinKeys'].insert(0, 'cn-uid/' + d['id'])
        if stats_only:
            row['buildId'] = piece
            if d.get('location'):
                row['ownerSource'] = d.get('ownerSource') or 'export'
            elif piece in last_worn:
                row['lastScanOwner'] = last_worn[piece]
                row['lastScanOwnerLabel'] = owner_labels.get(last_worn[piece], last_worn[piece])
        if d.get('location'):
            row['ownerLabel'] = owner_labels.get(d['location'], d['location'])
        if is_pinned(row, pins):
            why.append('手动保护')
        row['marked'] = d.get('trash') is True
        if 'ownerState' in d:
            row['ownerState'] = d['ownerState']
            if d['ownerState'] == 'unknown' and not stats_only:
                row['review'].append('穿戴者头像待复核')
        row['observedValues'] = values
        row['scannedLock'] = d.get('lockState', 'unknown')
        row['managedSpareLock'] = managed
        row['manualUnlock'] = manual_unlock
        row['manualKeep'] = entry.get('state') == 'manual-keep'
        if entry.get('state') in ('released', 'pending'):
            row['review'].append('上轮标记操作等待现场对账')
        if managed:
            row['explanation'].append('锁定来源 · OhMyZZZ 备用名额')
        if 'observedValues' in d and not row['observedValues']:
            row['review'].append('本次扫描未核实完整的主副词条数值')
        if enabled and not row['marked'] and d['rarity'] == 'S' and piece in inputs and inputs[piece] is None:
            if d['setKey'] not in optimizer_names:
                row['review'].append('配装器缺少套装译名 · '+d['setKey'])
            elif d.get('mainStatValue') is None and d['level'] < 15 and not values:
                row['review'].append('配装器缺少主属性数值，请重新扫描')
            else:
                row['review'].append('主副词条无法用于配装搜索')
        if not stats_only and d.get('markState')=='unknown':
            row['review'].append('现场弃置状态待核')
        if not stats_only and d.get('lockState')=='unknown':
            row['review'].append('现场锁定状态待核')
        row['ambiguous'] = counts[fp] > 1
        if row['ambiguous'] and 'scanCell' in d:
            row['explanation'].append(f'本轮有 {counts[fp]} 张同属性实物盘，整组跳过换装和标记')
        if (row['ambiguous'] and not stats_only) or d.get('setKey')=='Unknown' or d['rarity']!='S' or not 3<=len(row['rolls'])<=4:
            row['review'].append('身份、品级或副词条待核')
        unknown = [stat for stat in row['rolls'] if stat not in STATS or STATS[stat][1] is None]
        if unknown:
            row['review'].append('无法识别的副词条 · '+'、'.join(unknown))
        if row['main'] not in STATS or STATS[row['main']][2] is None:
            row['review'].append('无法识别的主词条 · '+row['main'])
        elif row['main'] not in SLOT_MAIN[row['slot']]:
            row['review'].append(f"{row['slot']} 号位不能出现此主属性 · {row['main']}")
        if row['main'] in row['rolls']:
            row['review'].append('主副词条重复')
        if d['rarity']=='S':
            assess_growth(row, cfg)
        rows.append(row)
    for row in rows:
        if row['marked']:
            if row['ambiguous']:
                row.update(decision='待复核',reasons=['游戏已标记弃置，实物属性重复'])
            else:
                row.update(decision='已弃置',reasons=['游戏已标记弃置'],review=[])
            continue
        checks = len(row['review'])
        assess_rules(row, cfg, fields, reverse, by_set, unresolved, excluded)
        rule_checks = row['review'][checks:]
        if rule_checks:
            row['ruleReview'] = rule_checks
            if any(reason.startswith('配队 · ') for reason in row['reasons']):
                row['explanation'].extend(rule_checks)
                del row['review'][checks:]
        if issues:
            row['review'].append('有启用配队尚未完成计算')
    quotas = [normalized_rule(rule, reverse) for rule in cfg['reserve_rules']]
    coverage = apply_reserves(rows, dict(cfg, reserve_rules=quotas))
    for row in rows:
        if row['marked']:
            continue
        row['decision'] = '保留' if row['reasons'] else ('待复核' if row['review'] else '弃置候选')
        if stats_only and row['decision'] == '弃置候选':
            row['checkForDiscard'] = True
            row['decision'] = '待复核'
            row['explanation'].append('超出留盘名额，穿戴和标记待游戏内核对')
        if row['managedSpareLock'] and row['decision'] == '弃置候选':
            row['explanation'].append('新保留盘锁定后，解除本插件的旧备用锁')
    from .disc_trace import rule_book
    evidence = rule_book(rows, rules, cfg['overrides'], current_conflicts(rules, cfg['overrides']))
    teams = {p['name']:dict(agent=(p.get('request') or {}).get('characterName') or p.get('agentKey'),
                            group=p.get('simultaneous') or '') for p in enabled}
    return dict(managerVersion=PLUGIN_VERSION, fullCritPriority=True, networkPreview=z.get('networkPreview') is True, readerOwners=reader_owners,
                inventory=inventory_hash, rules=rules.get('version'), configuration=digest(cfg), assignments=assignments,
                previewBindings=bindings, allocationKey=allocation_key, reusedAssignments=saved is not None,
                traceRules=evidence, traceTeams=teams,
                issues=issues, roster=[a for a in catalog if a['selected']], reserveCoverage=coverage,
                counts=dict(Counter(r['decision'] for r in rows)), discs=rows)

"""Compare the CN export with this account's last complete game scan."""
from collections import Counter, defaultdict

from .stat_reading import checked_values
from .store import digest, equipment_key, equipment_keys, inventory, roll_base


def attribute_key(disc, base, names, reverse):
    return (equipment_key(disc['setKey'], names, reverse), str(disc['slotKey']),
            disc['mainStatKey'], disc['level'], disc['rarity'],
            tuple(sorted((s['key'], 1 + s['upgrades'] - base) for s in disc['substats'])))


def verify_cn_inventory(exported, game, readings, names):
    """Keep the export's physical IDs. The scan supplies only observable controls and stats."""
    from .cn_export import SOURCE
    if exported.get('source') != SOURCE or exported.get('networkPreview') is not True:
        raise ValueError('请选择本账号的国服仓库导出文件')
    discs, _ = inventory(exported, preview_only=True)
    audit = game.get('audit') or {}
    if (game.get('source') != 'Zenless Optimizer' or game.get('networkPreview') or audit.get('partial')
            or type(audit.get('game_total')) is not int or audit['game_total'] != len(discs)):
        raise ValueError(f"游戏仓库 {audit.get('game_total', '未知')} 张，国服导出 {len(discs)} 张，请刷新导出文件")
    reverse = equipment_keys(names)
    groups = defaultdict(list)
    for disc in discs:
        groups[attribute_key(disc, roll_base(exported), names, reverse)].append(disc)
    evidence = defaultdict(list)
    for reading in readings:
        key = attribute_key(reading, roll_base(game), names, reverse)
        if key not in groups:
            raise ValueError('游戏里出现了导出文件里没有的盘，请刷新导出文件')
        evidence[key].append(reading)
    missing = sum(len(group) for key, group in groups.items() if key not in evidence)
    if missing:
        raise ValueError(f'还有 {missing} 张盘的属性未在游戏内对上，请刷新导出文件或重读游戏仓库')
    results = []
    for key, group in groups.items():
        observations = evidence[key]
        # A card can appear on overlapping pages. It can supply controls for one
        # UID only when the inventory itself says this attribute line is unique.
        for disc in group:
            row = dict(disc, exportUid=disc['id'], exportSet=disc['setKey'], location='', ownerState='unknown', lockState='unknown',
                       markState='unknown', observedGrade=None, observedValues=None)
            if len(group) == 1:
                def controls(d):
                    return (d.get('ownerState'), d.get('location', ''), d.get('lockState'), d.get('markState'))
                sample = observations[0]
                values = checked_values(sample, roll_base(game))
                state, wearer, locked, marked = controls(sample)
                owner_known = (state == 'worn' and bool(wearer)) or (state == 'bag' and not wearer)
                if (values and owner_known
                        and locked in ('locked', 'unlocked') and marked in ('marked', 'unmarked')
                        and all(checked_values(d, roll_base(game)) == values and controls(d) == controls(sample)
                                   and d.get('lock') is (locked == 'locked') and d.get('trash') is (marked == 'marked')
                                   and d.get('observedGrade') == disc['rarity'] for d in observations)
                        and (disc['rarity'] != 'S' or values == checked_values(disc, roll_base(exported)))):
                    row.update(setKey=sample['setKey'], observedValues=values, observedGrade=disc['rarity'])
                    for field in ('ownerState', 'location', 'markState', 'lockState', 'lock', 'trash'):
                        row[field] = sample[field]
                    if 'mainStatValue' in sample:
                        row['mainStatValue'] = sample['mainStatValue']
            results.append(row)
    snapshot = dict(format='ZOD', version=1, dbVersion=2, source='OhMyZZZ CN', discs=results,
                    characters=game.get('characters', []), wengines=game.get('wengines', []),
                    exportInventory=digest(exported), exportFile=exported.get('exportFile'), exportWrittenNs=exported.get('exportWrittenNs'),
                    audit=dict(game_total=audit['game_total'], exported=len(results), delta=0,
                        scannerExported=audit.get('exported'), uidCount=len(discs),
                        verifiedControls=sum(d['markState'] != 'unknown' for d in results)))
    inventory(snapshot)
    return snapshot


def execution_plan(planned, inspected, snapshot, *, fielded=()):
    """Translate the UID plan for the game UI. Game observations can add protection."""
    if (not planned.get('networkPreview') or planned['inventory'] != snapshot.get('exportInventory')
            or inspected['inventory'] != digest(snapshot) or inspected.get('networkPreview')
            or planned['rules'] != inspected['rules'] or planned['configuration'] != inspected['configuration']):
        raise ValueError('国服库存或配装规则已变化，请重新计算')
    targets = {r['exportUid']:r for r in planned['discs']}
    rows = {r['exportUid']:r for r in inspected['discs']}
    if targets.keys() != rows.keys():
        raise ValueError('国服驱动盘编号已变化，请刷新仓库')
    for uid, row in rows.items():
        wanted = targets[uid]
        if wanted['decision'] == '保留' and not row['marked']:
            reasons = [reason for reason in wanted['reasons'] if reason != '游戏内已锁定' or row.get('scannedLock') == 'locked']
            row['reasons'] = list(dict.fromkeys([*reasons, *row['reasons']]))
            if not row['reasons']:
                row['decision'] = '待复核'
                row['review'].append('游戏内已解除锁定')
                continue
            row['decision'] = '保留'
        elif row['decision'] == '弃置候选' and not wanted.get('checkForDiscard'):
            row['decision'] = '待复核'
        if row['marked'] and any(reason.startswith('配队 · ') for reason in wanted['reasons']):
            raise ValueError('配队用盘在游戏内已标记弃置，请刷新配装')
    assignments = {}
    for name, result in planned['assignments'].items():
        pieces = [rows[uid] for uid in result.get('ids', [])]
        if name in fielded and any(r['ambiguous'] or r['marked'] or not r['observedValues'] or r.get('ownerState') not in ('bag','worn')
                                   or r['review'] for r in pieces):
            raise ValueError(f'「{name}」的方案用盘还缺穿戴或实物核对')
        assignments[name] = dict(result, ids=[r['fp'] for r in pieces])
    inspected.update(assignments=assignments, traceTeams=planned['traceTeams'], previewBindings=planned['previewBindings'],
                     reserveCoverage=planned['reserveCoverage'], counts=dict(Counter(r['decision'] for r in rows.values())),
                     issues=list(dict.fromkeys([*planned['issues'], *inspected['issues']])))
    return inspected


def compare_game_scan(exported, scanned, names):
    game_discs, _ = inventory(scanned)
    reverse = equipment_keys(names)

    def group(discs, base):
        groups = defaultdict(list)
        for disc in discs:
            key = attribute_key(disc, base, names, reverse)
            groups[key].append(disc)
        return groups

    observed = group(game_discs, roll_base(scanned))
    imported = group(exported['discs'], roll_base(exported))
    owner_slots = Counter((d.get('location'), str(d['slotKey'])) for d in game_discs if d.get('ownerState') == 'worn')
    matched = measured = 0
    last_worn = {}
    # An identical stat line can occur on multiple physical pieces. Count the
    # common copies, but attribute an old wearer only for a one-to-one match.
    for key in observed.keys() & imported.keys():
        old, new = observed[key], imported[key]
        matched += min(len(old), len(new))
        if len(old) != 1 or len(new) != 1:
            continue
        values = checked_values(old[0], roll_base(scanned))
        if values is None or values != checked_values(new[0], roll_base(exported)):
            continue
        measured += 1
        owner = old[0].get('location')
        if old[0].get('ownerState') == 'worn' and owner and owner_slots[(owner, str(old[0]['slotKey']))] == 1:
            last_worn[new[0]['id']] = owner
    return dict(gameTotal=len(game_discs), exportTotal=len(exported['discs']), matched=matched,
                measured=measured, lastWorn=last_worn)

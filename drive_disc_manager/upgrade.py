"""Try one tier at a time and reconcile the selected disc before spending again."""
from collections import Counter
from contextlib import closing

from .optimizer import exported_s_main
from .inventory_comparison import match_export
from .reserve_policy import development, matching_rules, normalized_rule, upgrade_advice
from .stat_reading import checked_values, format_stats
from .potential import verdict
from .store import config, digest, equipment_keys, fingerprint, read, save


RECEIPT = 'upgrade-result.json'


def same_family(d, before):
    return all(d.get(k) == before.get(k) for k in ('setKey', 'mainStatKey', 'rarity')) and str(d['slotKey']) == str(before['slotKey'])


def rolls(d):
    return {s['key']: s['upgrades'] for s in d['substats']}


def advanced(before, after):
    level = before['level']
    target = (level // 3 + 1) * 3
    if (before['rarity'] != 'S' or not same_family(after, before) or target > 15 or after['level'] != target
            or not checked_values(after, 1) or not checked_values(before, 1)):
        return False
    old, new = rolls(before), rolls(after)
    if (len(new) != 4 or not old.keys() <= new.keys() or not development(level, old)
            or any(new[k] < old[k] for k in old) or sum(new.values()) != sum(old.values()) + 1):
        return False
    return after['observedValues']['main'][after['mainStatKey']] == exported_s_main(after['mainStatKey'], target)


def eligible(row):
    return (row['decision'] == '保留' and row.get('upgrade') and not row['marked'] and not row['ambiguous']
            and not row['review'] and row.get('ownerState') == 'bag' and not row.get('location')
            and row.get('observedValues') and not row.get('manualUnlock') and not row.get('manualKeep')
            and (row.get('scannedLock') != 'locked' or row.get('managedSpareLock'))
            and not any(r in ('手动保护', '已保存配装用盘') or r.startswith('配队 · ') for r in row['reasons']))


def bag_disc(d):
    return (d.get('markState') == 'unmarked' and d.get('ownerState') == 'bag' and not d.get('location')
            and d.get('lockState') in ('locked', 'unlocked') and d['rarity'] == 'S' and checked_values(d, 1))


def revised_stats(row, after):
    stats = rolls(after)
    return dict(row, level=after['level'], rolls=stats, development=development(after['level'], stats),
                roleRolls={label: sum(stats.get(k, 0) for k in keys) for label, keys in row.get('roleStats', {}).items()})


def after_advice(row, after, cfg, names):
    revised = revised_stats(row, after)
    reverse = equipment_keys(names)
    limits, _ = matching_rules(revised, dict(cfg, reserve_rules=[normalized_rule(r, reverse) for r in cfg['reserve_rules']]))
    return upgrade_advice(revised, limits)


def finalize(root, receipt, after):
    before = receipt['before']
    if not advanced(before, after) or not bag_disc(after) or after['lockState'] != before['lockState']:
        raise ValueError('还没核对到这张盘强化后的词条')
    # If the native dismantler follows this task, the kept piece is still in the bag under a new fingerprint.
    batch = read(root/'dismantle-preview.json', {})
    old_fp, new_fp = fingerprint(before), fingerprint(after)
    if receipt.get('batch') and batch.get('batch') == receipt['batch']:
        moves = batch.setdefault('upgradeMoves', {})
        counts = batch['beforeCounts']
        if old_fp in moves:
            if moves[old_fp] != new_fp:
                raise ValueError('这档强化的记录与实物不同')
        else:
            legacy_move = batch.get('upgradeAfter') == new_fp and old_fp not in counts
            if not legacy_move:
                if counts.get(old_fp) != 1 or any(e['fp'] == old_fp for e in batch['entries']):
                    raise ValueError('强化盘与本轮拆解清单不符')
                del counts[old_fp]
                counts[new_fp] = counts.get(new_fp, 0) + 1
            if 'beforeUids' in batch:
                from .salvage_audit import uid_item
                item = batch['beforeUids'][receipt['uid']]
                if item != uid_item(dict(before, exportSet=item['set'])):
                    raise ValueError('强化盘编号与拆解记录不同')
                batch['beforeUids'][receipt['uid']] = uid_item(dict(after, exportSet=item['set']))
            moves[old_fp] = new_fp
            save(root/'dismantle-preview.json', batch)
    history = read(root/'markers.json', {})
    from .marks import owns_spare
    entry = history.get(old_fp, {})
    if (owns_spare(entry, before['observedValues'], uid=receipt.get('uid')) and new_fp not in history
            and new_fp not in receipt['previousMatches']):
        history[new_fp] = dict(history.pop(old_fp), spareLock=dict(entry['spareLock'], values=after['observedValues']))
        save(root/'markers.json', history)
    advice = after_advice(receipt['row'], after, receipt['limits'], receipt['names'])
    reason = verdict(after['level'], rolls(after), receipt['row'].get('upgradeFloor', ()))
    if (after['level'] == 15 and receipt['limits'].get('protect15', True)) or new_fp in receipt['previousMatches']:
        reason = ''
    if (receipt['limits'].get('protectNaturalCrit', True) and development(after['level'], rolls(after))['initial'] == 4
            and {'crit_', 'crit_dmg_'} <= rolls(after).keys()):
        reason = ''
    if reason:
        advice = None
    label = receipt['label']
    decision = '建议弃置，' + reason if reason else f"建议再试到 +{advice['target']}" if advice else '留作备用'
    old_subs = before['observedValues']['subs']
    new_subs = after['observedValues']['subs']
    stat = next(k for k, v in new_subs.items() if old_subs.get(k) != v)
    if stat in old_subs:
        old = format_stats({stat:old_subs[stat]})
        change = f"{old} → {new_subs[stat]:g}" + ('%' if stat.endswith('_') else '')
    else:
        change = '新增' + format_stats({stat:new_subs[stat]})
    note = f"{label} +{before['level']} → +{after['level']} · {change} · {decision}"
    save(root/RECEIPT, dict(receipt, status='verified', after=after, nextUpgrade=advice, outgrown=reason, note=note))
    return note


def recover(root, receipt, snapshot):
    from .store import inventory
    inventory(snapshot)  # A partial view cannot settle an interrupted spend.
    before = receipt['before']
    uid = receipt.get('uid')
    pool = [d for d in snapshot['discs'] if same_family(d, before) and (not uid or d.get('exportUid') == uid)]
    if snapshot['audit'].get('incremental'):
        fresh = set(snapshot['audit']['verifiedUids'])
        pool = [d for d in pool if d.get('exportUid') in fresh]
    after = [d for d in pool if advanced(before, d) and (uid or fingerprint(d) not in receipt['previousMatches'])]
    same = [d for d in pool if fingerprint(d) == fingerprint(before) and d.get('observedValues') == before['observedValues']]
    if len(after) == 1 and not same:
        return finalize(root, receipt, after[0])
    if len(same) == 1 and not after and bag_disc(same[0]) and same[0]['lockState'] == before['lockState']:
        save(root/RECEIPT, dict(receipt, status='unchanged', note='这张盘仍是原等级，本次已核对'))
        return '已核对上次试强化，盘仍是原等级'
    raise ValueError('上次试强化还没核对到实物，请刷新游戏仓库')


def pending_trial(root):
    receipt = read(root/RECEIPT, {})
    return receipt if receipt.get('status') == 'pending' else None


def execute(root, ui, row, before, snapshot, cfg, names, batch=None):
    target = row['upgrade']['target']
    if (not eligible(row) or not bag_disc(before) or before['level'] >= 15 or fingerprint(before) != row['fp']
            or before['observedValues'] != row['observedValues'] or target != 3 * (before['level'] // 3 + 1)):
        raise ValueError('这张盘的状态已变化，请重新整理')
    ui.prepare(before, target)
    current, goal = ui.preview()
    if (current, goal) != (before['level'], target):
        raise ValueError(f'本次只试到 +{target}，游戏预览为 +{goal}')
    label = f"{names.get(row['set'], row['set'])} {row['slot']}号盘"
    receipt = dict(status='pending', before=before, target=target, uid=row.get('exportUid'), batch=batch, managedBefore=row.get('managedSpareLock', False),
                   row=row, label=label, limits={k:cfg[k] for k in ('reserve','reserve_rules','protect15','protectNaturalCrit')}, names=names,
                   previousMatches=[fingerprint(d) for d in snapshot['discs'] if advanced(before, d)])
    save(root/RECEIPT, receipt)
    ui.confirm(current, goal)
    return finalize(root, receipt, ui.read_after())


def permit(row, *, export=False):
    # CN exports omit wearers. The game reading separately establishes that the disc is in the bag.
    if export and row.get('exportUid') and row.get('ownerState') == 'unknown' and not row.get('location'):
        row = dict(row, ownerState='bag')
    return eligible(row)


def apply_reading(row, receipt, cfg, names):
    after = receipt['after']
    updated = revised_stats(row, after)
    updated.update(fp=fingerprint(after), observedValues=after['observedValues'], scannedLock=after['lockState'],
                   managedSpareLock=receipt.get('managedBefore', False), marked=receipt.get('marked', False))
    updated['upgrade'] = after_advice(row, after, cfg, names) if receipt.get('nextUpgrade') else None
    if receipt.get('outgrown'):
        updated.update(decision='已弃置' if updated['marked'] else '弃置候选', upgrade=None)
    if fingerprint(after) in receipt['previousMatches']:
        updated.update(ambiguous=True, upgrade=None)
    row.update(updated)


def select_disc(scanner, total, guard, targets, root):
    from .disc_probe import read_selected
    from .equip_targets import warehouse_observations
    from .marks import owns_spare
    history = read(root/'markers.json', {})
    with closing(warehouse_observations(scanner, total, guard, by_acquisition=True)) as observations:
        for sample, _ in observations:
            row = targets.get(fingerprint(sample))
            if not row or not bag_disc(sample):
                continue
            before = read_selected(scanner, total, guard)
            if (not bag_disc(before) or fingerprint(before) != row['fp'] or before['observedValues'] != row['observedValues']
                    or before['lockState'] != sample['lockState']):
                continue
            managed = owns_spare(history.get(row['fp'], {}), before['observedValues'], uid=row.get('exportUid'))
            if (before['lockState'] == 'locked' and not managed) or (before['lockState'] == 'unlocked' and row.get('scannedLock') == 'locked'):
                continue
            return dict(row, managedSpareLock=managed), before
    return None


def run_trials(app, scanner, report, snapshot, cfg, names, *, guard_export=None, network_plan=None):
    from .potential import benchmarks
    from .store import snapshot_path
    from .upgrade_ui import UpgradeUI

    if report.get('networkPreview') or report.get('issues') or digest(snapshot) != report['inventory']:
        raise ValueError('试强化需要本次游戏库存')
    limit = cfg['upgradeSteps']
    if type(limit) is not int or not 1 <= limit <= 99:
        raise ValueError('每轮强化可设 1 到 99 档')
    def guard():
        if (app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running
                or not app.ctx.controller.game_win.is_win_active or digest(config(app.root)) != report['configuration']):
            raise ValueError('试强化时账号、设置或游戏窗口已变化')
        if guard_export:
            guard_export()

    guard()
    receipt = pending_trial(app.root)
    if receipt:
        return recover(app.root, receipt, snapshot)
    # Stable in this run, even when an OCR fingerprint changes at every tier.
    discs = {d['id']:d for d in snapshot['discs']}
    rows = {r['id']:dict(r) for r in report['discs']}
    peers = remote_rows = None
    if network_plan is not None:
        net = read(snapshot_path(app.root, cfg))
        remote_rows, peers = match_export(snapshot, report, net, network_plan, names)
    batch = read(app.root/'dismantle-preview.json', {})
    batch_id = None
    excluded = set()
    if batch.get('inventory') == report['inventory'] and batch.get('account') == app.account:
        batch_id = batch['batch']
        excluded = {e['fp'] for e in batch['entries']}
    touched = Counter()
    notes = []
    for _ in range(limit):
        guard()
        floors = benchmarks(rows.values(), cfg, names)
        remote_floors = benchmarks(remote_rows.values(), cfg, names) if peers is not None else {}
        counts = Counter(r['fp'] for r in rows.values())
        candidates = []
        for ident, row in rows.items():
            if row['fp'] in excluded or counts[row['fp']] != 1 or not eligible(row):
                continue
            floor = floors.get(row['fp'], []) if 'upgradeFloor' in row else []
            if peers is not None:
                peer = peers.get(ident)
                if not peer or not permit(peer, export=True):
                    continue
                remote = remote_floors.get(peer['fp'], []) if 'upgradeFloor' in peer else []
                floor = [*floor, *remote] if floor and remote else []
            if not verdict(row['level'], row['rolls'], floor):
                candidates.append(dict(row, upgradeFloor=floor))
        if not candidates:
            break
        def priority(row):
            return touched[row['id']], len(row['rolls']) != 3
        first = min(map(priority, candidates))
        targets = {r['fp']:r for r in candidates if priority(r) == first}
        selected = select_disc(scanner, len(discs), guard, targets, app.root)
        if selected is None:
            if not notes:
                return '没有找到本轮可试强化的盘'
            break
        picked, before = selected
        ident = picked['id']
        view = dict(snapshot, discs=list(discs.values()))
        ui = UpgradeUI(scanner, guard, len(discs), names)
        execute(app.root, ui, picked, before, view, cfg, names, batch_id)
        receipt = read(app.root/RECEIPT)
        if cfg['applyMarkers'] and receipt.get('outgrown'):
            mark_outgrown(app, scanner, dict(report, discs=list(rows.values())), rows[ident], receipt, guard, batch_id)
            receipt = read(app.root/RECEIPT)
        after = receipt['after']
        retained = {k:discs[ident][k] for k in ('id', 'exportUid', 'exportSet') if k in discs[ident]}
        discs[ident] = dict(after, **retained)
        if receipt.get('marked'):
            discs[ident].update(trash=True, markState='marked', lock=False, lockState='unlocked')
        apply_reading(rows[ident], receipt, cfg, names)
        if peers is not None:
            apply_reading(peers[ident], receipt, cfg, names)
        touched[ident] += 1
        notes.append(receipt['note'])
    if len(notes) > 1:
        return f'已试强化 {len(touched)} 张盘，共 {len(notes)} 档 · ' + notes[-1]
    return notes[0] if notes else '没有适合试强化的盘'


def mark_outgrown(app, scanner, report, row, receipt, guard, batch_id):
    note = receipt['note']
    # Update the one selected piece and use the existing marker's fresh detail checks.
    from .live import Marker
    from .marks import owns_spare
    from .salvage_audit import disc_shape
    after = receipt['after']
    fp = fingerprint(after)
    entry = read(app.root/'markers.json', {}).get(fp, {})
    managed = owns_spare(entry, after['observedValues'], uid=row.get('exportUid'))
    if after['lockState'] == 'locked' and not managed:
        return note
    revised = dict(row, fp=fp, level=after['level'], rolls=rolls(after), observedValues=after['observedValues'],
                   reasons=[], review=[], decision='弃置候选', managedSpareLock=managed, outgrown=True)
    updated = dict(report, discs=[revised if r is row else r for r in report['discs']])
    marker = Marker(app, scanner, updated)
    marker.guard_export = guard
    marker.batch = batch_id
    detail = marker.detail()
    if detail != (fp, after['lockState'], 'unmarked', False):
        raise ValueError('待弃置的强化盘与当前选中盘不符')
    archived = read(app.root/'dismantle-preview.json', {})
    if batch_id and archived.get('batch') == batch_id and archived['selector'] == '全选已弃置':
        # Journal only as a candidate until the live marker confirms it.
        info = dict(fp=fp, set=after['setKey'], slot=str(after['slotKey']), main=after['mainStatKey'],
                    level=after['level'], beforeMultiplicity=1, shape=disc_shape(after))
        if receipt.get('uid'):
            info['uid'] = receipt['uid']
        archived.setdefault('markCandidates', {})[fp] = info
        save(app.root/'dismantle-preview.json', archived)
    if managed:
        marker.handle_disc('release', detail, revised)
    detail = marker.detail()
    if (detail != (fp, 'unlocked', 'unmarked', False) or not marker.handle_disc('mark', detail, revised)
            or marker.detail() != (fp, 'unlocked', 'marked', False)):
        raise ValueError('请下次整理再核对这张盘的弃置状态')
    note = note.replace('建议弃置', '已标记弃置', 1)
    save(app.root/RECEIPT, dict(receipt, marked=True, note=note))
    return note

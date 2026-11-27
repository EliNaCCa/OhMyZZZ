"""Try one tier on a kept disc. A confirmation in flight is reconciled before another spend."""
from collections import Counter
from contextlib import closing

from .optimizer import exported_s_main
from .reserve_policy import development, matching_rules, normalized_rule, upgrade_advice
from .stat_reading import checked_values
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
            and row.get('observedValues') and not row.get('manualUnlock') and not row.get('manualKeep'))


def bag_disc(d):
    return (d.get('markState') == 'unmarked' and d.get('ownerState') == 'bag' and not d.get('location')
            and d.get('lockState') in ('locked', 'unlocked') and d['rarity'] == 'S' and checked_values(d, 1))


def after_advice(row, after, cfg, names):
    stats = rolls(after)
    revised = dict(row, level=after['level'], rolls=stats, development=development(after['level'], stats),
                   roleRolls={label: sum(stats.get(k, 0) for k in keys) for label, keys in row.get('roleStats', {}).items()})
    reverse = equipment_keys(names)
    limits, _ = matching_rules(revised, dict(cfg, reserve_rules=[normalized_rule(r, reverse) for r in cfg['reserve_rules']]))
    return upgrade_advice(revised, limits)


def finalize(root, receipt, after):
    before = receipt['before']
    if not advanced(before, after) or not bag_disc(after) or after['lockState'] != before['lockState']:
        raise ValueError('还没核对到这张盘强化后的词条')
    # If the native dismantler follows this task, the kept piece is still in the bag under a new fingerprint.
    batch = read(root/'dismantle-preview.json', {})
    if receipt.get('batch') and batch.get('batch') == receipt['batch'] and 'upgradeAfter' not in batch:
        old_fp, new_fp = fingerprint(before), fingerprint(after)
        counts = batch['beforeCounts']
        if counts.get(old_fp) != 1 or any(e['fp'] == old_fp for e in batch['entries']):
            raise ValueError('强化盘与本轮拆解清单不符')
        del counts[old_fp]
        counts[new_fp] = counts.get(new_fp, 0) + 1
        batch['upgradeAfter'] = new_fp
        save(root/'dismantle-preview.json', batch)
    history = read(root/'markers.json', {})
    from .marks import owns_spare
    old_fp, new_fp = fingerprint(before), fingerprint(after)
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
    note = f"{label} +{before['level']} → +{after['level']} · " + decision
    save(root/RECEIPT, dict(receipt, status='verified', after=after, nextUpgrade=advice, outgrown=reason, note=note))
    return note


def recover(root, receipt, snapshot):
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
    receipt = dict(status='pending', before=before, target=target, uid=row.get('exportUid'), batch=batch,
                   row=row, label=label, limits={k:cfg[k] for k in ('reserve','reserve_rules','protect15','protectNaturalCrit')}, names=names,
                   previousMatches=[fingerprint(d) for d in snapshot['discs'] if advanced(before, d)])
    save(root/RECEIPT, receipt)
    ui.confirm(current, goal)
    return finalize(root, receipt, ui.read_after())


def try_one(app, scanner, report, snapshot, cfg, names, *, guard_export=None, network_plan=None):
    from .disc_probe import read_selected
    from .equip_targets import warehouse_observations
    from .upgrade_ui import UpgradeUI
    from .inventory_comparison import attribute_key

    if report.get('networkPreview') or report.get('issues') or digest(snapshot) != report['inventory']:
        raise ValueError('试强化需要本次游戏库存')
    def guard():
        if (app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running
                or not app.ctx.controller.game_win.is_win_active or digest(config(app.root)) != report['configuration']):
            raise ValueError('试强化时账号、设置或游戏窗口已变化')
        if guard_export:
            guard_export()

    receipt = read(app.root/RECEIPT, {})
    guard()
    if receipt.get('status') == 'pending':
        return recover(app.root, receipt, snapshot)
    targets = {r['fp']:r for r in report['discs'] if eligible(r)}
    targets = {fp:r for fp,r in targets.items() if r.get('exportUid') or not
               any(reason in ('手动保护', '已保存配装用盘') for reason in r['reasons'])}
    if network_plan is not None:
        # International exports have no durable IDs. Require a unique matching keep recommendation in both reads.
        from .store import snapshot_path
        net = read(snapshot_path(app.root, cfg))
        if digest(net) != network_plan['inventory']:
            raise ValueError('网络库存已更新，请重新整理')
        reverse = equipment_keys(names)
        def key(d): return attribute_key(d, 1, names, reverse)
        counts = Counter(key(d) for d in net['discs'])
        by_id = {d['id']:d for d in net['discs']}
        allowed = {key(by_id[r['id']]):r for r in network_plan['discs'] if eligible(r) and counts[key(by_id[r['id']])] == 1}
        matches = {fingerprint(d):allowed[key(d)] for d in snapshot['discs'] if key(d) in allowed}
        targets = {fp:r for fp,r in targets.items() if fp in matches}
    if not targets:
        return '没有适合试强化的盘'
    batch = read(app.root/'dismantle-preview.json', {})
    if batch.get('inventory') == report['inventory'] and batch.get('account') == app.account:
        native_targets = {e['fp'] for e in batch['entries']}
        targets = {fp:r for fp,r in targets.items() if fp not in native_targets}
        batch_id = batch['batch']
    else:
        batch_id = None
    if not targets:
        return '没有适合试强化的盘'
    total = len(snapshot['discs'])
    with closing(warehouse_observations(scanner, total, guard, by_acquisition=True)) as observations:
        for sample, _ in observations:
            row = targets.get(fingerprint(sample))
            if row and bag_disc(sample):
                before = read_selected(scanner, total, guard)
                if bag_disc(before) and fingerprint(before) == row['fp'] and before['observedValues'] == row['observedValues']:
                    break
        else:
            return '没有找到本轮可试强化的盘'
    picked = row
    if network_plan is not None:
        # Both inventories must agree that this tier cannot compete before marking it.
        remote_floor = matches[row['fp']].get('upgradeFloor')
        floor = row.get('upgradeFloor')
        picked = dict(row, upgradeFloor=[*floor, *remote_floor] if floor and remote_floor else [])
    note = execute(app.root, UpgradeUI(scanner, guard, total, names), picked, before, snapshot, cfg, names, batch_id)
    receipt = read(app.root/RECEIPT)
    if not (cfg['applyMarkers'] and receipt.get('outgrown')):
        return note
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
    if detail != (fp, 'unlocked', 'unmarked', False) or not marker.handle_disc('mark', detail, revised):
        raise ValueError('请下次整理再核对这张盘的弃置状态')
    note = note.replace('建议弃置', '已标记弃置', 1)
    save(app.root/RECEIPT, dict(receipt, marked=True, note=note))
    return note

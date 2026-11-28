"""Daily CN marking. The export names the targets, the warehouse verifies every action."""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from time import time_ns

from .inventory_comparison import attribute_key, bind_observations, execution_plan
from .store import InventoryUnavailable, digest, equipment_keys, fingerprint, read, save


FILE = 'mark-checkpoint.json'


def intent(row):
    # A newly assigned team or a change in a spare quota can affect an old UID.
    reasons = set(row['reasons'])
    if row['decision'] == '保留' and len(reasons) > 1:
        reasons.discard('游戏内已锁定')  # Applying our own lock does not change the keep decision.
    return digest([row['decision'], sorted(reasons), sorted(row['review']),
                   row.get('checkForDiscard', False), row['decision'] != '保留' and row.get('managedSpareLock'),
                   row.get('manualUnlock'), row.get('manualKeep')])


def check_file(exported):
    from .cn_export import same_export
    path = Path(exported['exportFile'])
    if not same_export(path, path.stat(), exported) or exported['exportWrittenNs'] > time_ns():
        raise ValueError('国服仓库导出已变化，请重新整理')


def checkpoint(root, exported, planned, view, report, marker):
    check_file(exported)
    if view.get('exportInventory') != digest(exported) or planned['inventory'] != view['exportInventory']:
        raise ValueError('国服仓库与本次整理记录不匹配')
    rows = {r['exportUid']:r for r in report['discs']}
    goals = {r['exportUid']:intent(r) for r in planned['discs']}
    facts = {}
    from .loadout import fielded_profiles
    from .store import config
    cfg = config(root)
    if digest(cfg) != report['configuration']:
        raise ValueError('留盘设置已变化，请重新整理')
    fielded = fielded_profiles(cfg, cfg['fieldProfile']) if not planned.get('triageOnly') and cfg.get('fieldProfile') else []
    equipped = {uid for p in fielded for uid in planned['assignments'][p['name']]['ids']}
    wearers = {planned['previewBindings'][p['name']] for p in fielded}
    for item in view['discs']:
        uid = item['id']
        fp = rows[uid]['fp']
        disc = dict(item)
        if not rows[uid]['ambiguous']:
            if fp in marker.confirmed_locks:
                disc['lock'] = marker.confirmed_locks[fp]
                disc['lockState'] = 'locked' if disc['lock'] else 'unlocked'
            if marker.observed.get(fp) in ('marked', 'unmarked'):
                disc['markState'] = marker.observed[fp]
                disc['trash'] = disc['markState'] == 'marked'
        after = rows[uid]
        if marker.observed.get(fp) == 'marked' and not after['ambiguous']:
            after = dict(after, decision='已弃置', reasons=['游戏已标记弃置'], review=[], managedSpareLock=False)
        facts[uid] = dict(disc=disc, action=intent(after), goal=goals[uid])
        if uid in equipped or (disc.get('location') and disc['location'] in wearers):
            facts[uid]['recheckWearer'] = True
    for disc in exported['discs']:
        facts[disc['id']]['attributes'] = digest([fingerprint(disc), disc.get('location'), disc.get('observedValues')])
    record = dict(version=1, account=root.name, file=exported['exportFile'],
                  stamp=exported['exportWrittenNs'], finished=time_ns(), facts=facts)
    if view['audit'].get('incremental'):
        record['checkedThisRun'] = len(view['audit']['verifiedUids'])
    save(root/FILE, record)


def needs_first_read(disc, row, native):
    from .salvage_audit import GRADE_FILTERS
    if native and (native.dismantle_abandon or disc['rarity'] in GRADE_FILTERS.get(native.dismantle_level, {'S', 'A', 'B'})):
        return True
    if row.get('review') or row.get('ambiguous'):
        return False
    if disc['rarity'] == 'S' and row['level'] == 15 and row.get('ownedUses'):
        return True  # Even a locked incumbent may set the floor for a growing piece.
    return (row.get('checkForDiscard') or row.get('upgrade') or
            (row['decision'] == '保留' and (not disc['lock'] or row.get('managedSpareLock'))))


def worklist(exported, planned, previous, names, native, *, first_pass=False):
    reverse = equipment_keys(names)
    groups = defaultdict(list)
    dirty = set()
    plans = {r['exportUid']:r for r in planned['discs']}
    for disc in exported['discs']:
        uid = disc['id']
        groups[attribute_key(disc, 1, names, reverse)].append(uid)
        old = previous.get(uid, {})
        if first_pass or old.get('disc', {}).get('markState') == 'unknown':
            if needs_first_read(disc, plans[uid], native):
                dirty.add(uid)
            continue
        attributes = digest([fingerprint(disc), disc.get('location'), disc.get('observedValues')])
        if (old.get('attributes') != attributes or old.get('recheckWearer') or old.get('goal') != intent(plans[uid])
                or old['disc'].get('lock') != disc['lock']):
            dirty.add(uid)
    return groups, dirty


def find_readings(exported, names, groups, wanted, observations, *, order=None, record_first_order=False):
    """Order affects speed only. Stop as soon as every requested unique item matches."""
    from .equip_targets import reading_identity
    reverse = equipment_keys(names)
    catalogue = {d['id']:d for d in exported['discs']}
    waiting = {key:ids[0] for key, ids in groups.items() if len(ids) == 1 and ids[0] in wanted}
    order_targets = {key for key,ids in groups.items() if len(ids) == 1 and catalogue[ids[0]]['rarity'] == 'S'} if record_first_order else set()
    found = {}
    if not waiting and not order_targets:
        return found
    for sample, repeat in observations:
        key = attribute_key(sample, 1, names, reverse)
        if key not in groups:
            raise InventoryUnavailable('游戏里有国服导出未收录的盘，请刷新仓库导出')
        if order is not None and len(groups[key]) == 1:
            uid = groups[key][0]
            if (sample.get('observedGrade') == catalogue[uid]['rarity'] and sample.get('observedValues') and
                    sample['observedValues'] == catalogue[uid].get('observedValues')):
                order.see(sample, uid)
                order_targets.discard(key)
        if key not in waiting:
            if not waiting and not order_targets:
                return found
            continue
        uid = waiting[key]
        row = bind_observations(catalogue[uid], [sample], 1, 1)
        if row['markState'] == 'unknown' or reading_identity(repeat()) != reading_identity(sample):
            continue
        if row['lock'] != catalogue[uid]['lock']:
            raise ValueError('游戏里的锁定状态与国服导出不同，请刷新仓库导出')
        found[uid] = (row, sample)
        del waiting[key]
        if not waiting and not order_targets:
            return found
    if not waiting:
        return found
    raise ValueError(f'还有 {len(waiting)} 张盘没在仓库里对上，请刷新国服导出后再整理')


def daily(app, scanner, exported, planned, pinned, wiki, field):
    from .dismantle import handoff, note_existing_marks
    from .equip_targets import equip_cn_targets, warehouse_observations
    from .live import Marker
    from .marks import reconcile_scan
    from .planner import plan
    from .service import preview, reassign_discards
    from .upgrade import pending_trial, recover
    from .salvage_audit import native_run, needs_review, observe_native, reconcile
    from one_dragon.utils import os_utils
    from .store import config, equipment_names, known_agents
    from .task_order import following_native
    from .warehouse import enter_warehouse
    from time import time

    baseline = read(app.root/FILE, {})
    first_pass = (baseline.get('version') != 1 or baseline.get('file') != exported['exportFile']
                  or baseline.get('account') != app.root.name or not baseline.get('facts'))
    started = time()
    observe_native(app.root, app.account, native_run(Path(os_utils.get_work_dir()), app.account), started)
    prior_batch = read(app.root/'dismantle-result.json', {})
    if needs_review(prior_batch):
        if first_pass:
            return None
        if prior_batch.get('afterInventory') and not prior_batch.get('firstUidObservation'):
            raise ValueError('请在「库存记录」核对上次拆解')
        if not prior_batch.get('afterInventory'):
            raise InventoryUnavailable('等待拆解后的国服库存更新')
    if any(e.get('state') in ('pending', 'released') for e in read(app.root/'markers.json', {}).values()):
        return None
    check_file(exported)
    if not first_pass and exported['exportWrittenNs'] <= baseline['finished']:
        raise InventoryUnavailable('国服仓库导出还没有更新')
    cfg, rules, _warnings = pinned
    dictionary = read(wiki, {})
    names = equipment_names(dictionary) | cfg.get('aliases', {})
    upcoming = following_native(app.ctx, app.account, app.group)
    native = upcoming[0] if upcoming else None
    previous = {} if first_pass else baseline['facts']
    groups, dirty = worklist(exported, planned, previous, names, native, first_pass=first_pass)
    pending = pending_trial(app.root)
    if pending:
        if first_pass or pending.get('uid') not in {d['id'] for d in exported['discs']}:
            return None
        dirty.add(pending['uid'])
    assigned = {uid for result in planned['assignments'].values() for uid in result.get('ids', ())}
    dirty.update(uid for uid in assigned if previous.get(uid, {}).get('disc', {}).get('markState') != 'unmarked')
    copies = {uid for ids in groups.values() if len(ids) > 1 for uid in ids}
    if prior_batch.get('firstUidObservation') and needs_review(prior_batch):
        survivors = {e['uid'] for e in prior_batch['entries']} & {d['id'] for d in exported['discs']}
        if survivors & copies:
            raise ValueError('上批同属性盘仍在仓库，请在「库存记录」核对')
        dirty.update(survivors)
    total = len(exported['discs'])

    def guard():
        if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
            raise ValueError('账号已切换或任务已停止')
        if not app.ctx.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')
        check_file(exported)

    found = {}

    def inspect(wanted, *, record_first_order=False):
        from .disc_order import PageRanks, remember_ranks
        order = PageRanks(total)
        with closing(warehouse_observations(scanner, total, guard, by_acquisition=True)) as observations:
            found.update(find_readings(exported, names, groups, wanted, observations, order=order, record_first_order=record_first_order))
        guard()
        remember_ranks(app.root, exported, order.ranks())

    def decision_view():
        discs = []
        for disc in exported['discs']:
            uid = disc['id']
            if uid in found:
                discs.append(found[uid][0])
            elif uid in copies or previous.get(uid, {}).get('disc', {}).get('markState', 'unknown') == 'unknown':
                discs.append(bind_observations(disc, (), 1, 1))
            else:
                discs.append(previous[uid]['disc'])
        # This working copy is never published as inventory.json or a full game scan.
        return dict(format='ZOD', version=1, dbVersion=2, source='OhMyZZZ CN',
                    discs=discs, characters=exported['characters'], wengines=exported['wengines'],
                    exportInventory=digest(exported), exportFile=exported['exportFile'], exportWrittenNs=exported['exportWrittenNs'],
                    audit=dict(game_total=total, exported=total, delta=0, incremental=True, verifiedUids=sorted(found)))

    from .loadout import fielded_profiles
    fielded = [p['name'] for p in fielded_profiles(cfg, field)] if field else []
    for name in fielded:
        dirty.update(planned['assignments'][name]['ids'])
    guard()
    if not dirty - copies:
        scanner._ensure_screen_areas()
        if enter_warehouse(scanner, guard) != total:
            raise InventoryUnavailable('仓库盘数有变化，请刷新国服仓库导出')
    inspect(dirty, record_first_order=first_pass)
    if needs_review(prior_batch) and prior_batch.get('firstUidObservation'):
        reconcile(app.root, decision_view())
        if needs_review(read(app.root/'dismantle-result.json')):
            raise ValueError('请在「库存记录」核对上次拆解')
    recovered = None
    if pending:
        recovered = recover(app.root, pending, decision_view())
        observe_native(app.root, app.account, native_run(Path(os_utils.get_work_dir()), app.account), started)
        reconcile(app.root, exported)
        if needs_review(read(app.root/'dismantle-result.json', {})):
            return app.round_fail('请先到「设置 → 库存记录」核对上次的盘')
        planned = preview(app.root, wiki, snapshot=pinned, expected_inventory=planned['inventory'],
                          triage_only=bool(planned.get('triageOnly')))
    while True:
        view = decision_view()
        reconcile_scan(app.root, view, names, verified_uids=found)
        revised = reassign_discards(app.root, wiki, pinned, planned, view)
        if revised is not planned:
            new_assigned = {uid for result in revised['assignments'].values() for uid in result['ids']}
            planned = revised
            replacements = new_assigned - assigned - found.keys() - copies
            assigned = new_assigned
            if replacements:
                inspect(replacements)
                continue
        checked = plan(view, dict(cfg, profiles=[], reserve=0, reserve_rules=[], trimRecommended=True), rules, names,
                       wiki=dictionary, known_owned=known_agents(app.root), history=read(app.root/'markers.json', {}))
        checked['configuration'] = digest(cfg)
        report = execution_plan(planned, checked, view, cfg=cfg, names=names, fielded=fielded)
        if report['issues']:
            raise ValueError('配装待复核 · '+' / '.join(report['issues']))
        # Live wearer/mark observations can change the spare ranking for an old disc.
        extra = {r['exportUid'] for r in report['discs'] if r['exportUid'] in previous
                 and previous[r['exportUid']]['disc']['markState'] != 'unknown'
                 and intent(r) != previous[r['exportUid']]['action']}
        # Before giving up an old spare verify all replacement spares in its group, even old ones.
        retiring = {(r['set'], r['slot'], r['main']) for r in report['discs']
                    if cfg.get('rotateSpareLocks') and r['managedSpareLock'] and r['decision'] == '弃置候选'}
        extra.update(r['exportUid'] for r in report['discs'] if (r['set'], r['slot'], r['main']) in retiring
                     and any(reason.startswith('备用 · ') for reason in r['reasons']))
        extra -= found.keys() | copies
        if not extra:
            break
        inspect(extra)

    guard()
    if digest(config(app.root)) != report['configuration']:
        raise ValueError('留盘设置已变化，请重新整理')
    note_existing_marks(app, view, started)
    marker = Marker(app, scanner, report, readings=[sample for _, sample in found.values()])
    marker.scope = set(found)
    marker.guard_export = lambda: check_file(exported)
    marker.batch = handoff(app, report, snapshot=view)
    # An interrupted batch must not reuse pre-click controls on a retry.
    save(app.root/FILE, {})
    observed = marker.run()
    if marker.batch:
        handoff(app, report, observed, batch=marker.batch, snapshot=view, confirmed_locks=marker.confirmed_locks)
    guard()
    if field:
        result = equip_cn_targets(app, scanner, cfg, planned, exported, field, names, wiki)
        # The successful next export can refresh the selected agents' wearers.
        # A failed or partially applied loadout leaves the checkpoint invalid.
        if result.is_success:
            checkpoint(app.root, exported, planned, view, report, marker)
        return result
    checkpoint(app.root, exported, planned, view, report, marker)
    if first_pass:
        message = f'首次整理完成 · 国服库存 {total} 张，游戏核对 {len(found)} 张'
    else:
        message = f'增量整理完成 · 已核对 {len(found)} 张盘' if found else '驱动盘没有变化'
    if recovered:
        message += ' / ' + recovered
    elif cfg.get('autoUpgrade'):
        from .upgrade import run_trials
        message += ' / ' + run_trials(app, scanner, report, view, cfg, names, network_plan=planned, guard_export=marker.guard_export)
    return app.round_success(message)

"""Daily CN marking. The export names the targets, the warehouse verifies every action."""
from collections import defaultdict
from contextlib import closing
from pathlib import Path
from time import time_ns

from .inventory_comparison import attribute_key, bind_observations, execution_plan
from .store import digest, equipment_keys, fingerprint, read, save


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
    save(root/FILE, dict(version=1, account=root.name, file=exported['exportFile'],
                         stamp=exported['exportWrittenNs'], finished=time_ns(), facts=facts))


def worklist(exported, planned, previous, names, native):
    reverse = equipment_keys(names)
    groups = defaultdict(list)
    dirty = set()
    goals = {r['exportUid']:intent(r) for r in planned['discs']}
    for disc in exported['discs']:
        uid = disc['id']
        groups[attribute_key(disc, 1, names, reverse)].append(uid)
        old = previous.get(uid, {})
        attributes = digest([fingerprint(disc), disc.get('location'), disc.get('observedValues')])
        if (old.get('attributes') != attributes or old.get('recheckWearer') or old.get('goal') != goals[uid]
                or old['disc'].get('lock') != disc['lock']
                or (native and native.dismantle_abandon and not disc['lock'])):
            dirty.add(uid)
    # When one copy leaves or is enhanced the remaining UID can become actionable.
    for group in groups.values():
        if len(group) == 1:
            uid = group[0]
            if previous.get(uid, {}).get('disc', {}).get('markState') == 'unknown':
                dirty.add(uid)
    return groups, dirty


def find_readings(exported, names, groups, wanted, observations):
    """Order affects speed only. Stop as soon as every requested unique item matches."""
    from .equip_targets import reading_identity
    reverse = equipment_keys(names)
    catalogue = {d['id']:d for d in exported['discs']}
    waiting = {key:ids[0] for key, ids in groups.items() if len(ids) == 1 and ids[0] in wanted}
    found = {}
    if not waiting:
        return found
    for sample, repeat in observations:
        key = attribute_key(sample, 1, names, reverse)
        if key not in groups:
            raise ValueError('游戏里有国服导出未收录的盘，请刷新仓库导出')
        if key not in waiting:
            continue
        uid = waiting[key]
        row = bind_observations(catalogue[uid], [sample], 1, 1)
        if row['markState'] == 'unknown' or reading_identity(repeat()) != reading_identity(sample):
            continue
        if row['lock'] != catalogue[uid]['lock']:
            raise ValueError('游戏里的锁定状态与国服导出不同，请刷新仓库导出')
        found[uid] = (row, sample)
        del waiting[key]
        if not waiting:
            return found
    raise ValueError(f'还有 {len(waiting)} 张盘没在仓库里对上，请刷新国服导出后再整理')


def daily(app, scanner, exported, planned, pinned, wiki, field):
    from .dismantle import handoff, note_existing_marks
    from .equip_targets import equip_cn_targets, warehouse_observations
    from .live import Marker
    from .marks import reconcile_scan
    from .planner import plan
    from .service import reassign_discards
    from .salvage_audit import native_run, needs_review, observe_native
    from one_dragon.utils import os_utils
    from .store import config, equipment_names, known_agents
    from .task_order import following_native
    from .warehouse import enter_warehouse
    from time import time

    baseline = read(app.root/FILE, {})
    if (baseline.get('version') != 1 or baseline.get('file') != exported['exportFile']
            or baseline.get('account') != app.root.name or not baseline.get('facts')):
        return None   # The normal full scan seeds the first checkpoint.
    started = time()
    observe_native(app.root, app.account, native_run(Path(os_utils.get_work_dir()), app.account), started)
    if needs_review(read(app.root/'dismantle-result.json', {})):
        return None   # The pending receipt may require a fresh full observation.
    if any(e.get('state') in ('pending', 'released') for e in read(app.root/'markers.json', {}).values()):
        return None
    check_file(exported)
    if exported['exportWrittenNs'] <= baseline['finished']:
        raise ValueError('请刷新国服仓库导出，下次整理会接着扫新增盘')
    cfg, rules, _warnings = pinned
    dictionary = read(wiki, {})
    names = equipment_names(dictionary) | cfg.get('aliases', {})
    upcoming = following_native(app.ctx, app.account, app.group)
    native = upcoming[0] if upcoming else None
    previous = baseline['facts']
    groups, dirty = worklist(exported, planned, previous, names, native)
    assigned = {uid for result in planned['assignments'].values() for uid in result.get('ids', ())}
    dirty.update(uid for uid in assigned if previous.get(uid, {}).get('disc', {}).get('markState') == 'marked')
    copies = {uid for ids in groups.values() if len(ids) > 1 for uid in ids}
    if native and native.dismantle_abandon and any(d['id'] in copies and not d['lock'] for d in exported['discs']):
        return None   # Native may select a lookalike for which no UI control can be bound to a UID.
    total = len(exported['discs'])

    def guard():
        if app.ctx.current_instance_idx != app.account or not app.ctx.run_context.is_context_running:
            raise ValueError('账号已切换或任务已停止')
        if not app.ctx.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')
        check_file(exported)

    found = {}

    def inspect(wanted):
        with closing(warehouse_observations(scanner, total, guard, by_acquisition=True)) as observations:
            found.update(find_readings(exported, names, groups, wanted, observations))

    def decision_view():
        discs = []
        for disc in exported['discs']:
            uid = disc['id']
            if uid in found:
                discs.append(found[uid][0])
            elif uid in copies:
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
            raise ValueError('仓库盘数有变化，请刷新国服仓库导出')
    inspect(dirty)
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
        extra = {r['exportUid'] for r in report['discs'] if intent(r) != previous.get(r['exportUid'], {}).get('action')}
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
        handoff(app, report, observed, batch=marker.batch, snapshot=view)
    guard()
    if field:
        result = equip_cn_targets(app, scanner, cfg, planned, exported, field, names, wiki)
        # The successful next export can refresh the selected agents' wearers.
        # A failed or partially applied loadout leaves the checkpoint invalid.
        if result.is_success:
            checkpoint(app.root, exported, planned, view, report, marker)
        return result
    checkpoint(app.root, exported, planned, view, report, marker)
    return app.round_success(f'增量整理完成 · 已核对 {len(found)} 张盘' if found else '驱动盘没有变化')

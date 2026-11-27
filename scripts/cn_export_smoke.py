"""Import a few ZOOD records from the ordinary OneDragon page (offscreen)."""
import argparse
from copy import deepcopy
import os
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from time import monotonic, sleep, time
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, sample, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home / 'src')]
    from PySide6.QtWidgets import QApplication, QFileDialog, QInputDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager.cn_export import refresh
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, inventory, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    account = temp / 'accounts' / '1'
    file = account / 'exports' / 'zzz-drives-zood-v1.json'
    original = read(sample)['discs'][:5]
    records = []
    for i, observed in enumerate(original):
        # Recorded OCR attributes, test-only IDs and exporter spelling.
        raw = {k: observed[k] for k in ('level', 'rarity', 'slotKey', 'mainStatKey', 'substats')}
        assert observed['setKey'] == '拂晓行纪'
        records.append(dict(raw, id=str(1001+i), setKey='TheSkyAblaze', location='', lock=True, trash=False))
    exported = dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=records)
    save(file, exported)
    save(account / 'config.json', dict(config(account), auto_sync=False))
    legacy = dict(networkPreview=True, characters=[dict(key='Anton', mindscape=4)])
    save(account / 'network-inventory.json', legacy)
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _: account.parent,
                          run_context=SimpleNamespace(is_context_stop=True, is_context_running=True))
    widget = ManagerInterface(ctx)
    widget.on_interface_shown()
    widget.reader.setCurrentIndex(widget.reader.findData('cn_export'))
    assert widget.scan_button.text() == '读取仓库'
    assert '读取仓库' in widget.calculate_buttons[0].toolTip()
    with patch.object(QFileDialog, 'getOpenFileName', side_effect=AssertionError('unexpected file chooser')):
        widget.scan_button.click()
    end = monotonic() + 12
    while widget.preview_job is not None and monotonic() < end:
        app.processEvents()
        sleep(.01)
    assert len(widget.rows) == len(original) == 5, widget.preview_note.text()
    snap_path = account / 'cn-inventory.json'
    snapshot = read(snap_path)
    assert '读取仓库' == widget.scan_button.text()
    for record, source in zip(snapshot['discs'], original):
        assert record['observedValues'] == source['observedValues']
        assert record['ownerState'] == record['markState'] == 'unknown'
    spare_rotation(temp/'reserve'/'1', exported, original)
    try:
        inventory(snapshot)
    except ValueError:
        pass
    else:
        raise AssertionError('stats-only file treated as a verified game scan')
    manager = SimpleNamespace(ctx=ctx, root=account, account=1, group='one_dragon', round_success=lambda msg: msg,
                              round_fail=lambda msg: (_ for _ in ()).throw(AssertionError(msg)))
    assert '预览' in ManagerApp.manage_inventory(manager)
    assert read(snap_path) == snapshot and not (account / 'inventory.json').exists()
    assert read(account / 'network-inventory.json') == legacy
    # The OCR sample and the CN-format input are separate files in this temporary account.
    from drive_disc_manager.service import preview
    scanned = dict(format='ZOD', version=1, dbVersion=2, source='Zenless Optimizer',
                   discs=[dict(d, id=f'ocr-{i}') for i, d in enumerate(original)], characters=[], wengines=[],
                   audit=dict(game_total=5, exported=5, delta=0))
    save(account / 'inventory.json', dict(scanned, audit=['unreadable']))
    alone = preview(account, refresh=False)
    assert [d['exportUid'] for d in alone['discs']] == [d['id'] for d in records]
    assert 'gameComparison' not in alone and all('lastScanOwner' not in d for d in alone['discs'])
    save(account / 'inventory.json', scanned)
    widget.reload()
    assert widget.preview_stale
    assert '预览' in ManagerApp.manage_inventory(manager)
    widget.reload()
    assert not widget.preview_stale and '套装和词条对上 5 张' in widget.scan_comparison.text()
    row = widget.rows[0]
    assert row['ownerState'] == 'unknown' and row['lastScanOwner'] == original[0]['location'] == 'Anton'
    assert widget.report['gameComparison']['measured'] == 5
    from drive_disc_manager.cn_export import normalize
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import equipment_names
    pair = [records[0], dict(records[0], id='9999')]
    seen = []
    def observe_pool(url, profile, pool, worn):
        assert not worn
        seen.extend(d['id'] for d in pool)
        return []  # A pair of 1-slot pieces cannot form a six-slot build.
    fixture = normalize(dict(exported, discs=pair))
    from drive_disc_manager.inventory_comparison import compare_game_scan
    duplicate = compare_game_scan(fixture, scanned, equipment_names({}))
    assert duplicate['matched'] == 1 and not duplicate['lastWorn']
    cfg = dict(config(account), profiles=[dict(name='样盘', request=dict(characterName='安东'))])
    plan(fixture, cfg, {}, equipment_names({}), observe_pool)
    assert seen == ['1001', '9999']
    from drive_disc_manager.reserve_policy import quota_impact
    unlocked = normalize(dict(exported, discs=[dict(raw, lock=False) for raw in pair]))
    options = dict(config(account), reserve=1, profiles=[], protect15=False, trimRecommended=True)
    rules = dict(fields={'matrix/TheSkyAblaze/4':['crit_']})
    keep = plan(unlocked, options, rules, equipment_names({}))
    assert keep['counts'] == {'保留': 1, '待复核': 1}, keep['counts']
    copies = quota_impact(keep, {}, dict(options, reserve=2), {})
    assert copies['spares'] == copies['after'] == 2 and copies['newCandidates'] == copies['newGameChecks'] == 0
    reduced = quota_impact(keep, {}, dict(options, reserve=0), {})
    assert reduced['newGameChecks'] == 1 and reduced['after'] == reduced['newCandidates'] == 0
    from drive_disc_manager.store import toggle_pin, is_pinned, import_inventory
    one_pin = toggle_pin(keep['discs'][0], [])
    assert one_pin == ['cn-uid/1001'] and not is_pinned(keep['discs'][1], one_pin)
    options = dict(options, pins=one_pin, reserve=0, inventorySource='cn_export')
    split = normalize(dict(exported, discs=[dict(d, lock=False) for d in [*pair, records[1]]]))
    # Two export UIDs share one OCR signature. The physical count is three even
    # when the scanner folds the two equal ones into a single on-screen record.
    game = dict(scanned, discs=[dict(d, observedGrade=d['rarity'], lock=d['lockState']=='locked',
                                     trash=d['markState']=='marked') for d in scanned['discs'][:2]],
                audit=dict(game_total=3, exported=2, delta=-1))
    small = temp/'cn-execution'/'1'
    save(small/'config.json', options); save(small/'cn-inventory.json', split); save(small/'rules.json', rules)
    save(small/'scan.json', game)
    import_inventory(small, small/'scan.json', readings=game['discs'], cn_export=split, names=equipment_names({}))
    bridged = read(small/'inventory.json'); assert len(bridged['discs']) == 3
    assert sum(d['ownerState']=='unknown' for d in bridged['discs']) == 2
    cn_report = preview(small, refresh=False)
    actions = preview(small, refresh=False, verified_game=True, cn_plan=cn_report)
    by_uid = {r['exportUid']:r for r in actions['discs']}
    assert '手动保护' in by_uid['1001']['reasons'] and '手动保护' not in by_uid['9999']['reasons']
    assert by_uid['1001']['ambiguous'] and by_uid['9999']['ambiguous']
    assert by_uid['1002']['observedValues'] == original[1]['observedValues']
    assert by_uid['1002']['decision'] == ('保留' if original[1]['lockState']=='locked' else '弃置候选')
    save(small/'scan.json', dict(game, audit=dict(game_total=4, exported=2, delta=-2)))
    try:
        import_inventory(small, small/'scan.json', readings=game['discs'], cn_export=split, names=equipment_names({}))
    except ValueError as error:
        assert '国服导出 3 张' in str(error)
    else:
        raise AssertionError('used an out-of-date export')
    assert read(small/'inventory.json') == bridged
    # A physical UID survives enhancement. A newly dropped copy with the same
    # stats as a departed piece is a different UID, even with equal totals.
    from drive_disc_manager.inventory_comparison import verify_cn_inventory
    from drive_disc_manager.salvage_audit import prepare_batch
    from drive_disc_manager.store import digest
    changed = deepcopy(exported)
    changed['discs'][0]['level'] = 12
    changed['discs'][0]['substats'][2]['upgrades'] -= 1
    stamp = int(time()) - 30
    batch_file = small / 'export.json'
    def write_export(rows, when):
        save(batch_file, dict(exported, discs=[dict(d, lock=False) for d in rows]))
        os.utime(batch_file, (when, when))

    write_export(changed['discs'][:3], stamp-2)
    refresh(small, lambda: None, path=batch_file)
    before = read(small/'cn-inventory.json')
    # Mock game controls, keeping each normalized item's real roll values.
    controls = [dict(d, observedGrade='S', ownerState='bag', lockState='unlocked',
                     markState='marked' if d['id']=='1002' else 'unmarked', trash=d['id']=='1002') for d in before['discs']]
    paired = verify_cn_inventory(before, game, controls, equipment_names({}))
    batch = prepare_batch(paired, dict(inventory=digest(paired), configuration='test'), {}, 'B', before_marking=True)
    assert batch['unresolvedGrades'] == batch['unreadMarks'] == 0 and len(batch['entries']) == 1
    batch.update(account=1, started=stamp)
    save(small/'dismantle-preview.json', batch)
    native = temp/'config/01/app_run_record/drive_disc_dismantle.yml'
    native.parent.mkdir(parents=True, exist_ok=True)
    native.write_text(f'run_time_float: {stamp+1}\nrun_status: 1\n', encoding='utf-8')
    revised = [records[0], records[2], dict(records[1], id='2002')]
    write_export(revised, stamp)
    refresh(small, lambda: None)
    pending = read(small/'dismantle-result.json')
    assert pending['status'] == 'pending' and 'afterInventory' not in pending
    from drive_disc_manager.salvage_page import latest_batch
    widget.salvage_page.present(small, {}, latest_batch(small))
    assert widget.salvage_page.title.text() == '等待国服库存更新'
    assert widget.salvage_page.grid.item(0, 4).text() == '此前已弃置'
    # Same bytes, newer export. The native record is observed by ordinary refresh.
    write_export(revised, stamp+2)
    refresh(small, lambda: None)
    audit = read(small/'dismantle-result.json')
    assert audit['status']=='reconciled' and audit['newDiscs']==1 and audit['entries'][0]['uid']=='1002'
    assert {(d['uid'], d['after']) for d in audit['stockChanges']}=={('1001','强化 +12 → +15'),('2002','新入库')}
    refresh(small, lambda: None)
    assert read(small/'dismantle-result.json') == audit
    save(small/'dismantle-preview.json', dict(batch, batch='later', started=stamp+5))
    widget.salvage_page.present(small, {}, latest_batch(small))
    assert widget.salvage_page.title.text() == '本轮拆解预览'
    assert '1002' in widget.salvage_page.grid.item(0, 0).toolTip()
    # A lookalike replacing an unmarked disc must be reported as an unexpected loss.
    other = temp/'alternate'/'1'
    save(other/'cn-inventory.json', before)
    save(other/'dismantle-result.json', pending)
    write_export([records[0], records[1], dict(records[2], id='2003')], stamp+3)
    refresh(other, lambda: None)
    review = read(other/'dismantle-result.json')
    assert review['status'] == 'needs_review' and review['observations'] == {'仍在仓库':1}
    assert [(d['uid'], d['after']) for d in review['stockChanges'] if d['issue']] == [('1003', '清单外少盘')]
    widget.salvage_page.present(other, {}, review)
    assert widget.salvage_page.grid.rowCount() == 2 and '1003' in widget.salvage_page.grid.item(1, 0).toolTip()
    # Replay one simulated enhancement on one test ID. The real game was not changed.
    save(file, changed)
    assert refresh(account, lambda: None) == '属性变化 1 张'
    report = preview(account, refresh=False)
    assert report['gameComparison']['matched'] == 4 and 'lastScanOwner' not in report['discs'][0]
    save(file, exported)
    assert refresh(account, lambda: None) == '属性变化 1 张'
    assert refresh(account, lambda: None) == '驱动盘没有变化'
    good = snap_path.read_bytes()
    changed['discs'][0]['id'] = changed['discs'][1]['id']
    save(file, changed)
    try:
        refresh(account, lambda: None)
    except ValueError:
        pass
    else:
        raise AssertionError('accepted duplicate item ID')
    assert snap_path.read_bytes() == good
    # Turn on the optional showcase from the normal settings page. Only the
    # attributes came from the real scan; the UID and association are fixtures.
    save(file, exported)
    with patch.object(QInputDialog, 'getText', return_value=('123456789', True)):
        widget.showcase_button.click()
    assert config(account)['showcaseUid'] == '123456789'
    from drive_disc_manager.showcase import PROPERTIES
    prop = {name: number for number, name in PROPERTIES.items()}
    disc = records[0]
    item = dict(Uid=int(disc['id']), Id=34041, Level=disc['level'], MainStatList=[dict(PropertyId=11103)],
                RandomPropertyList=[dict(PropertyId=prop[s['key']], PropertyLevel=s['upgrades']) for s in disc['substats']])
    avatar = dict(Id=1141, EquippedList=[dict(Slot=1, Equipment=item)])
    response = dict(uid='123456789', PlayerInfo=dict(ShowcaseDetail=dict(AvatarList=[avatar])))
    with patch('drive_disc_manager.showcase.fetch', return_value=response) as remote:
        assert '预览' in ManagerApp.manage_inventory(manager)
        remote.assert_called_once_with('123456789')
    widget.reload()
    assert widget.rows[0]['ownerSource'] == 'showcase' and widget.rows[0]['location'] == '1141'
    assert 'lastScanOwner' not in widget.rows[0] and '对上 1 张' in widget.warehouse_summary.text()
    widget.close()


def spare_rotation(root, exported, original):
    """Use three archived stat lines and simulated UID-bound locks in a temporary account."""
    from drive_disc_manager.cn_export import normalize
    from drive_disc_manager.inventory_comparison import verify_cn_inventory
    from drive_disc_manager.live import Marker
    from drive_disc_manager.marks import reconcile_scan
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import defaults, equipment_names, fingerprint, save

    cfg = dict(defaults(), inventorySource='cn_export', trimRecommended=True, reserve=1, rotateSpareLocks=True,
               protect15=False, protectNaturalCrit=False, includeOwned=False)
    save(root/'config.json', cfg)
    save(root/'rules.json', dict(version='fixture', fields={'matrix/TheSkyAblaze/4':['crit_']}))
    raws = [dict(d, lock=i != 1) for i, d in enumerate(exported['discs'][:3])]
    current = normalize(dict(exported, discs=raws))
    save(root/'cn-inventory.json', current)
    manual, fresh, old = [r['id'] for r in raws]
    observed = [dict(d, location='', ownerState='bag', observedGrade='S', lock=raw['lock'],
                     lockState='locked' if raw['lock'] else 'unlocked', markState='unmarked', trash=False)
                for d, raw in zip(original, raws)]
    audit = dict(source='Zenless Optimizer', audit=dict(game_total=3, exported=3))
    names = equipment_names({})
    fp = fingerprint(observed[2])
    assert fp != fingerprint(current['discs'][2])  # Same disc, CN title in the game and EN title in ZOOD.
    receipt = dict(uid=old, state='locked', spareLock=dict(origin='verified-spare-lock-v1',
                                                        values=observed[2]['observedValues']))

    def select(report):
        return {r['exportUid']:r for r in report['discs']}

    save(root/'markers.json', {fp:receipt})
    planned = preview(root, refresh=False)
    rows = select(planned)
    assert rows[old].get('checkForDiscard') and rows[old]['decision'] == '待复核'
    assert rows[fresh]['reasons'] == ['备用 · 通用'] and rows[manual]['reasons'] == ['游戏内已锁定']
    def inspect(readings):
        game = verify_cn_inventory(current, audit, readings, names)
        save(root/'inventory.json', game)
        reconcile_scan(root, game, names)
        return select(preview(root, refresh=False, verified_game=True, cn_plan=planned))
    live = inspect(observed)
    assert live[old]['managedSpareLock'] and live[old]['decision'] == '弃置候选'
    assert live[fresh]['decision'] == live[manual]['decision'] == '保留'
    for unavailable in (dict(ownerState='worn', location='Anton'), dict(markState='marked', trash=True)):
        actual = inspect([observed[0], dict(observed[1], **unavailable), observed[2]])
        assert actual[old]['decision'] == '保留' and '备用 · 通用' in actual[old]['reasons'], (
            unavailable, [(r['reasons'], r['review'], r['decision']) for r in actual.values()])
        assert actual[fresh]['decision'] == ('已弃置' if unavailable.get('trash') else '保留')
        assert '备用 · 通用' not in actual[fresh]['reasons']
        save(root/'markers.json', {fp:receipt})
    worn = inspect([*observed[:2], dict(observed[2], ownerState='worn', location='Anton')])
    assert worn[old]['decision'] == '保留' and worn[old]['location'] == 'Anton'
    unlocked = inspect([*observed[:2], dict(observed[2], lock=False, lockState='unlocked')])
    assert unlocked[old]['manualUnlock'] and select(preview(root, refresh=False))[old]['manualUnlock']
    for invalid in ({fp:dict(receipt, uid='9')}, {fp:dict(receipt, uid=None)},
                    {fp:dict(receipt, spareLock=dict(receipt['spareLock'], values=observed[0]['observedValues']))},
                    {fp:receipt, 'older':receipt}):
        save(root/'markers.json', invalid)
        assert select(preview(root, refresh=False))[old]['reasons'] == ['游戏内已锁定']
    save(root/'markers.json', {fp:receipt})
    save(root/'config.json', dict(cfg, rotateSpareLocks=False))
    fixed = select(preview(root, refresh=False))
    assert fixed[old]['decision'] == '保留' and fixed[fresh].get('checkForDiscard')
    try:
        Marker(None, None, planned)
    except ValueError as exc:
        assert '游戏画面' in str(exc)
    else:
        raise AssertionError('network preview entered the game marker')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    parser.add_argument('--sample', required=True, type=Path)
    args = parser.parse_args()
    with TemporaryDirectory() as folder:
        smoke(args.home.resolve(), args.sample.resolve(), Path(folder))
    print('PASS: five OCR reference discs, ZOOD import UI, scheduled refresh, ID-based enhancement and duplicate rejection')

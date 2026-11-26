"""Drive the embedded scan button into the real manager with one local fixture disc."""
import argparse
import os
import sys
from contextlib import ExitStack, nullcontext
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace as Obj
from unittest.mock import patch


def smoke(home, tmp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication
    from one_dragon.utils import os_utils
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.manual_equip import SCAN_GROUP
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(tmp))
    root = tmp/'accounts'/'1'
    cfg = dict(config(root), run_scanner=True, applyMarkers=True, fieldProfile='日常出场')
    save(root/'config.json', cfg)
    disc = dict(id='1', setKey='SwingJazz', slotKey='4', mainStatKey='def_', rarity='S', level=0,
                substats=[dict(key=s, upgrades=0) for s in ('pen', 'hp', 'atk')],
                lockState='unlocked', markState='unmarked', lock=False, trash=False)
    snapshot = dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=[disc],
                    audit=dict(game_total=1, exported=1, delta=0))
    scanner = Obj(config=Obj(scan_drive_disk=True), ocr_worker=Obj(scanned_discs=[disc]), _open_export=None)
    run = Obj(is_context_stop=True, is_context_running=False, is_app_registered=lambda _:True,
              get_config=lambda *_:scanner.config, get_application=lambda *_:scanner)
    ctx = Obj(current_instance_idx=1, get_plugin_data_dir=lambda _:root.parent, ready_for_application=True,
              run_context=run, controller=Obj(game_win=Obj(active=lambda:None)))
    manager = Obj(group=SCAN_GROUP, ctx=ctx, account=1, root=root, round_success=lambda msg:msg,
                  round_fail=lambda msg:(_ for _ in ()).throw(AssertionError(msg)))
    started, release = Event(), Event()
    def checked(stub, guard):
        assert stub is scanner
        guard()
        started.set()
        assert release.wait(5)
        save(tmp/'.debug/inventory_exports/inventory_demo.json', snapshot)
        return Obj(success=True)
    def launch(app_id, account, group):
        assert group == SCAN_GROUP and account == 1
        run.is_context_stop, run.is_context_running = False, True
        try:
            assert '已扫描 1 张' in ManagerApp.manage_inventory(manager)
        finally:
            run.is_context_stop, run.is_context_running = True, False
        return Obj(app_id=app_id, instance_idx=account, group_id=group, finish_reason=Obj(value='COMPLETED'))
    run.run_application = launch
    widget = ManagerInterface(ctx)
    # A past unresolved batch must still allow the read-only scan required to review it.
    from drive_disc_manager.store import digest
    outstanding = dict(batch='single-fixture', status='needs_review', afterInventory=digest(snapshot),
                       independentNative=dict(account=1, updated=1, status=3))
    save(root/'dismantle-result.json', outstanding)
    widget.on_interface_shown()
    with ExitStack() as stack:
        for name, value in [('drive_disc_manager.disc_probe.interactive_slot', lambda:nullcontext((3, lambda:None))),
                            ('drive_disc_manager.disc_probe.game_guard', lambda *_:(lambda:None, 1)),
                            ('drive_disc_manager.scan_marks.capture_marks', lambda *_:nullcontext()),
                            ('drive_disc_manager.manager_app.checked_scan', checked)]:
            stack.enter_context(patch(name, value))
        forbidden = [stack.enter_context(patch(name, side_effect=AssertionError(name))) for name in (
            'drive_disc_manager.loadout.apply_loadout', 'drive_disc_manager.dismantle.handoff', 'drive_disc_manager.live.Marker')]
        widget.scan_button.click()
        assert started.wait(3) and not widget.scan_button.isEnabled()
        release.set()
        deadline = monotonic()+5
        while widget.preview_job is not None and monotonic() < deadline:
            app.processEvents()
            sleep(.01)
        assert widget.preview_job is None and widget.scan_button.isEnabled(), widget.preview_note.text()
        assert widget.preview_note.text() == '' and len(widget.rows) == 1
        for action in forbidden:
            action.assert_not_called()
    assert read(root/'inventory.json') == snapshot and config(root) == cfg
    # Reuse the same one-disc warehouse via the daily entry. No new mark can
    # reach the game until the displayed batch has been reviewed.
    manager.group = 'one_dragon'
    manager.round_fail = lambda reason:('failed', reason)
    run.is_context_stop, run.is_context_running = False, True
    scanner.execute = lambda: (save(tmp/'.debug/inventory_exports/inventory_daily.json', snapshot) or Obj(success=True))
    save(root/'config.json', dict(cfg, auto_sync=False, fieldProfile=''))
    with patch('drive_disc_manager.scan_marks.capture_marks', lambda *_:nullcontext()), \
         patch('drive_disc_manager.live.Marker') as marker:
        status, reason = ManagerApp.manage_inventory(manager)
        assert status == 'failed' and '库存记录' in reason
        marker.assert_not_called()
    assert read(root/'dismantle-result.json') == outstanding
    # An interrupted one-disc read supplies observations even without a native export.
    # A stale partial in the shared export directory must never supply this account's rows.
    original = (root/'inventory.json').read_bytes()
    save(tmp/'.debug/inventory_exports/inventory_partial_old.json', dict(snapshot, discs=[dict(disc, level=8)]))
    scanner._disc_game_total = 3
    scanner.ocr_worker.scanned_agents = [{'key':'Soldier11'}]
    def interrupted():
        raise ValueError('人工结束')
    scanner.execute = interrupted
    with patch('drive_disc_manager.scan_marks.capture_marks', lambda *_:nullcontext()):
        assert ManagerApp.manage_inventory(manager) == ('failed', '人工结束')
        record = read(root/'scan-observations.json')
        assert record['total'] == 3 and record['rows'] == [disc]
        assert 'Soldier11' in read(root/'owned-agents.json')
        def switched():
            ctx.current_instance_idx = 2
            return Obj(success=False, status='扫描结束')
        scanner.execute = switched
        assert ManagerApp.manage_inventory(manager) == ('failed','当前账号已改变')
        assert record == read(root/'scan-observations.json') and (root/'inventory.json').read_bytes() == original
    ctx.current_instance_idx = 1
    scanner.execute = lambda: (save(tmp/'.debug/inventory_exports/inventory_daily.json', snapshot) or Obj(success=True))
    from drive_disc_manager.salvage_audit import close_reviewed, needs_review
    close_reviewed(root, outstanding['batch'], outstanding['afterInventory'])
    assert not needs_review(read(root/'dismantle-result.json'))
    # Fresh account, ordinary task-list entry. Use cached sample rules to stay offline.
    from time import time
    fresh = root.parent / '2'
    manager.account = ctx.current_instance_idx = 2
    manager.root = fresh
    save(fresh/'rules.json', dict(updated=time(), fields={}, version='fixture'))
    assert not (fresh/'config.json').exists()
    with patch('drive_disc_manager.scan_marks.capture_marks', lambda *_:nullcontext()), \
         patch('drive_disc_manager.live.Marker') as marker, \
         patch('drive_disc_manager.loadout.apply_loadout') as equip, \
         patch('drive_disc_manager.dismantle.handoff') as dismantle:
        assert '预览' in ManagerApp.manage_inventory(manager)
        assert len(read(fresh/'preview.json')['discs']) == 1
        for action in (marker, equip, dismantle):
            action.assert_not_called()
    # A failure in the fielded team's equipment step must leave the completed marks on disk.
    from drive_disc_manager.store import fingerprint
    fp = fingerprint(disc)
    save(fresh/'config.json', dict(config(fresh), auto_sync=False, applyMarkers=True, fieldProfile='出场'))
    native = Obj(dismantle_level='A及以下', dismantle_abandon=True)
    group = Obj(app_list=[Obj(app_id='drive_disc_dismantle', enabled=True)])
    ctx.app_group_manager = Obj(get_one_dragon_group_config=lambda account:group)
    run.get_config = lambda app_id,*_:native if app_id == 'drive_disc_dismantle' else scanner.config
    def stop_at_equip(*args, **kwargs):
        receipt = read(fresh/'dismantle-preview.json')
        assert receipt['account'] == 2 and receipt['batch'] == marker.return_value.batch
        assert 'markCandidates' not in receipt and [e['fp'] for e in receipt['entries']] == [fp]
        raise ValueError('模拟换装中断')
    def observed_mark():
        initial = read(fresh/'dismantle-preview.json')
        assert 'markCandidates' in initial and not initial['entries']
        return {fp:'marked'}
    with (patch('drive_disc_manager.scan_marks.capture_marks', lambda *_:nullcontext()),
          patch('drive_disc_manager.live.Marker') as marker, patch('drive_disc_manager.loadout.verify_bindings'),
          patch('drive_disc_manager.equip_ui.EquipUI'), patch('drive_disc_manager.loadout.apply_loadout', stop_at_equip)):
        marker.return_value.run.side_effect = observed_mark
        assert ManagerApp.manage_inventory(manager) == ('failed','模拟换装中断')
    widget.close()


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(parser.parse_args().home.resolve(), Path(tmp))
    print('PASS embedded read-only scan: one fixture disc, saved preview with daily equipment and markers enabled')

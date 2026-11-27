"""Replay saved drops through the daily reader and its embedded result view."""
import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch


def smoke(home, sample):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication
    from one_dragon.utils import os_utils
    from drive_disc_manager.manager_app import ManagerApp
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, read, save

    qt = QApplication([])
    os_utils.set_work_dir(str(home))
    observed = read(sample)
    discs = observed['discs'][:3]
    assert discs and observed['status'] == 'verified'
    if len(observed['discs']) >= 4:
        replay_recent_window(observed)
    with TemporaryDirectory() as directory:
        root = Path(directory)/'1'
        cfg = dict(config(root), inventorySource='cn_export', auto_sync=False, applyMarkers=True)
        save(root/'config.json', cfg)
        src = sample.parent
        for file in ('rules.json', 'owned-agents.json'):
            if (src/file).is_file():
                save(root/file, read(src/file))
        # Replay one real same-slot peer from the saved scan, not the entire bag.
        past = read(src/'scan-observations.json')
        peer = next(d for d in past['rows'] if d['setKey'] == discs[0]['setKey'] and
                    d['slotKey'] == discs[0]['slotKey'] and d['mainStatKey'] == discs[0]['mainStatKey'] and
                    (d.get('observedValues') or {}).get('subs') == {'crit_dmg_':4.8,'pen':9,'hp_':3})
        save(root/'scan-observations.json', dict(at=past['at'], total=past['total'], rows=[peer]))
        scanner = NS(config=NS(scan_drive_disk=True), _ensure_screen_areas=lambda: None)
        ctx = NS(current_instance_idx=1, get_plugin_data_dir=lambda _: root.parent,
                 controller=NS(game_win=NS(is_win_active=True)),
                 run_context=NS(is_context_running=True, is_context_stop=False,
                                is_app_registered=lambda _:True, get_config=lambda *_:scanner.config,
                                get_application=lambda *_:scanner))
        class Manager:
            manage_inventory = ManagerApp.manage_inventory
            recent_when_waiting = ManagerApp.recent_when_waiting
            round_fail = staticmethod(str)
            round_success = staticmethod(lambda _: (_ for _ in ()).throw(AssertionError('partial recorded as complete')))
        manager = Manager()
        manager.root, manager.account, manager.ctx, manager.group = root, 1, ctx, 'one_dragon'
        with (patch('drive_disc_manager.recent_discs.leave_disc_detail'),
              patch('drive_disc_manager.warehouse.enter_warehouse', return_value=observed['warehouseCount']),
              patch('drive_disc_manager.recent_discs.read_front_row', return_value=discs) as front):
            result = manager.manage_inventory()
            assert f'已记录当前筛选下最近的 {len(discs)} 张盘' in result, result
            assert front.call_count == 1 and front.call_args.args[-1] == 3
            assert front.call_args.kwargs['previous']['warehouseCount'] == past['total']
            original = read(root/'recent-discs.json')
            assert original['discs'] == discs and all(r['suggestion'] for r in original['rows'])
            assert '有效 2 档，这张 1 档' in original['rows'][0]['reference']
            assert all('basis' not in row for row in original['rows'])
            save(root/'config.json', dict(cfg, inventorySource='network'))
            with patch('drive_disc_manager.network_reader.socket.create_connection', side_effect=ConnectionRefusedError()):
                assert '已记录当前筛选下最近的' in manager.manage_inventory()
            saved = read(root/'recent-discs.json')
            assert front.call_args.kwargs['previous'] == original
            assert saved['rows'] == original['rows'] and front.call_count == 2
            with patch('drive_disc_manager.network_reader.capture', side_effect=ValueError('账号不符')):
                assert manager.manage_inventory() == '账号不符' and front.call_count == 2
            assert read(root/'recent-discs.json') == saved
            for f in ('inventory.json', 'cn-inventory.json', 'network-inventory.json', 'markers.json', 'upgrade-result.json'):
                assert not (root/f).exists(), f

            # First CN export, no checkpoint. Replay only the grid entry and the three observed drops.
            from contextlib import nullcontext
            from unittest.mock import Mock
            import numpy as np
            from one_dragon.base.operation.operation_base import OperationResult
            sample_disc = {k:discs[0][k] for k in ('slotKey', 'level', 'rarity', 'setKey', 'mainStatKey', 'substats')}
            sample_disc.update(id='101', location='', lock=False, trash=False)
            # One simulated +15 spare with six proficiency rolls. The recorded +0
            # can reach only five in that use, including all remaining tiers.
            cfg.update(reserve=0, reserve_rules=[dict(purpose='异常精通', count=1, stats=['anomProf'])])
            sample_disc.update(level=15, substats=[dict(key=k, upgrades=v) for k,v in
                               [('anomProf',6), ('def',1), ('hp',1), ('crit_',1)]])
            save(root/'config.json', cfg)
            save(root/'exports/zzz-drives-zood-v1.json', dict(format='ZOOD', version=1, source='zzz-drive-exporter', discs=[sample_disc]))
            total = observed['warehouseCount']
            ctx.ocr = NS(run_ocr=Mock(return_value={f'驱动仓库 [ {total} / {total + 1} ]':[NS(confidence=.99)]}))
            scanner.ctx = ctx
            grid = NS(execute=Mock(return_value=OperationResult(False, '正常进入逐盘核对')))
            scanner.drive_disk_scanner = grid
            scanner.execute = lambda: grid.execute()
            scanner._open_export = lambda _: None
            scanner.screenshot = lambda: np.zeros((1080,1920,3), dtype=np.uint8)
            with (patch('drive_disc_manager.scan_marks.capture_marks', return_value=nullcontext(None)),
                  patch('drive_disc_manager.scan_observations.save_interrupted_scan')):
                mismatch = manager.manage_inventory()
                assert f'游戏仓库有 {total} 张，国服导出有 1 张' in mismatch and front.call_count == 3, mismatch
                assert grid.execute.call_count == 0 and not (root/'inventory.json').exists()
                from datetime import datetime
                cn = read(root/'cn-inventory.json')
                stamp = datetime.fromtimestamp(cn['exportWrittenNs'] / 1e9).strftime('%m-%d %H:%M')
                compared = read(root/'recent-discs.json')['rows']
                assert all(r.get('basis') == '国服导出 ' + stamp and 'reference' not in r for r in compared)
                assert compared[0]['suggestion'] == '跳过试强化' and '最多 5 档' in compared[0]['advice'], compared[0]
                before = (root/'recent-discs.json').read_bytes()
                with patch('drive_disc_manager.upgrade.pending_trial', return_value={'uid':'101'}):
                    assert '已记录' not in manager.manage_inventory()
                assert front.call_count == 3 and (root/'recent-discs.json').read_bytes() == before
                ctx.ocr.run_ocr.return_value = {'驱动仓库 [ 1 / 2000 ]':[NS(confidence=.99)]}
                assert manager.manage_inventory() == '正常进入逐盘核对'
                assert grid.execute.call_count == 1 and not (root/'inventory.json').exists()
        ctx.run_context.is_context_running = False
        ctx.run_context.is_context_stop = True
        page = ManagerInterface(ctx)
        page.on_interface_shown()
        assert page.recent_result.isEnabled()
        page.recent_result.trigger()
        assert '参考国服导出' in page.probe_note.text()
        table = page.recent_table
        assert table.rowCount() == len(compared) and not table.isHidden()
        assert table.horizontalHeaderItem(1).text() == '读取时的建议'
        assert all(r['name'] in table.item(i, 0).text() and r['suggestion'] == table.item(i, 1).text()
                   for i, r in enumerate(compared))
        page.show_recent(original)
        assert original['rows'][0]['reference'] in table.item(0, 1).toolTip()
        three_rows_height = table.height()
        # Open the real DEF% disc, adjust a personal rule, and return to the same drop.
        from drive_disc_manager.store import known_agents
        save(root/'owned-agents.json', sorted(known_agents(src)))
        save(root/'config.json', cfg)
        save(root/'recent-discs.json', original)
        page.on_interface_shown()
        page.recent_result.trigger()
        table.itemActivated.emit(table.item(2, 0))
        assert page.tabs.currentWidget() is page.detail and '月光骑士颂' in page.detail.title.text()
        assert ('不建议试强化' in page.detail.title.text() and '耀嘉音推荐攻击力百分比' in page.detail.summary.toPlainText()), (page.detail.title.text(), page.detail.summary.toPlainText())
        assert any(key == 'build/Astra Yao/slot5' for key, _, _ in page.detail.uses)
        with patch.object(page, 'edit_quota', return_value=None) as edit:
            page.detail.quota.click()
            edit.assert_called_once_with(dict(set='MoonlightLullaby', slot='5', main='def_'), '通用')
        with patch.object(page, 'refresh_saved_preview', side_effect=page.reload):
            page.detail.pin.click()
        assert config(root)['pins'] and '手动保留' in page.detail.title.text()
        page.detail.back.emit()
        assert table.currentRow() == 2 and not table.isHidden() and table.rowCount() == 3
        table.itemActivated.emit(table.item(2, 0))
        save(root/'recent-discs.json', dict(original, discs=discs[:1], rows=original['rows'][:1]))
        page.reload()
        assert page.tabs.currentWidget() is page.navigation.pages['仓库预览'] and table.isHidden()
        print('PASS recent disc opens main-stat sources and spare settings, refreshes after pin, restores selection and handles replaced readings')
        if len(observed['discs']) >= 4:
            from drive_disc_manager.manager_app import wiki_path
            from drive_disc_manager.recent_discs import describe
            four = dict(observed, discs=observed['discs'][:4])
            real_rows = describe(src, four, read(wiki_path(), {}))
            page.show_recent(dict(four, rows=real_rows))
            assert table.rowCount() == 4 and table.height() == three_rows_height
            last = real_rows[-1]
            assert table.item(3, 0).text() == f"{last['name']} {last['slot']} 号 +{last['level']}"
            assert [table.item(3, c).text() for c in (1, 2, 3)] == [last['suggestion'], last['main'], last['subs']]
        ctx.current_instance_idx = 2
        page.on_interface_shown()
        assert not page.recent_result.isEnabled() and not page.probe_note.text()
        assert table.isHidden() and table.rowCount() == 0
        print('PASS missing/offline reader, stale CN count before grid, pending trial, matching count enters grid, saved rows in UI')
        print([(r['name'],r['slot'],r['suggestion']) for r in saved['rows']])


def replay_recent_window(record):
    from contextlib import nullcontext
    from unittest.mock import Mock
    import numpy as np
    from one_dragon.base.geometry.point import Point
    from drive_disc_manager.recent_discs import read_front_row

    four = record['discs'][:4]
    # Across a row break, include a repeated drop and a pair resembling the
    # old leading pair earlier than the known net increase.
    drops = [*four, *four, four[2], four[2]]
    cards = [*drops, *four[:2]]
    total = record['warehouseCount']
    previous = dict(warehouseCount=total-len(drops), discs=four[:2])
    rows = [[Point(167+c*135,319+r*174) for c in range(9)] for r in range(2)]
    points = [p for line in rows for p in line][:len(cards)]
    grid = Mock()
    grid._detect_points.return_value = points
    grid._resolve_grid.return_value = rows, ''
    scanner = NS(ctx=NS(ocr=None), screenshot=lambda:np.zeros((1080,1920,3), dtype=np.uint8), drive_disk_scanner=grid)
    checked = []
    def read_card(scanner, total, guard, *, target):
        idx = len(checked)
        assert (target.x,target.y) == (points[idx].x, points[idx].y-44)
        checked.append(cards[idx])
        return cards[idx]
    with (patch('drive_disc_manager.recent_discs.guarded_grid', return_value=nullcontext(grid)),
          patch('drive_disc_manager.recent_discs.newest_first', return_value=True),
          patch('drive_disc_manager.recent_discs.warehouse_header', return_value=('驱动仓库',total)),
          patch('drive_disc_manager.recent_discs.scroll_thumb', return_value=(1,4,5,22)),
          patch('drive_disc_manager.recent_discs.read_selected', read_card),
          patch('drive_disc_manager.recent_discs.sleep')):
        assert read_front_row(scanner,total,lambda:None,3,previous=previous) == drops
        assert checked == cards
        # Same net total after a disposal. Four new positions precede the old pair.
        previous['warehouseCount'] = total
        previous['discs'] = [four[1], four[0]]
        cards = [*four, *previous['discs']]
        checked.clear()
        assert read_front_row(scanner,total,lambda:None,3,previous=previous) == four
        assert checked == cards
    print('PASS ten new positions across two rows, duplicate rolls, four drops with unchanged warehouse count')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('home', type=Path)
    parser.add_argument('sample', type=Path)
    args = parser.parse_args()
    smoke(args.home.resolve(), args.sample.resolve())

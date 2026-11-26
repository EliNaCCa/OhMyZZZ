"""Exercise a background preview completion with two discs and one saved observation."""
import argparse
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def smoke(home, tmp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication
    from one_dragon.utils import os_utils
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import save

    app = QApplication([])
    os_utils.set_work_dir(str(tmp))
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:tmp/'accounts')
    widget = ManagerInterface(ctx)
    widget.on_interface_shown()
    nav = widget.navigation
    assert list(nav.pivot.items) == ['仓库', '配队', '规则', '设置']
    assert not widget.empty.isHidden() and widget.table.isHidden()
    nav.pivot.items['规则'].click()
    nav.selectors['规则'].items['备用规则'].click()
    assert widget.tabs.currentWidget() is widget.reserve_presets
    nav.pivot.items['设置'].click()
    assert widget.tabs.currentWidget() is nav.pages['运行设置'] and 'afterEarly' not in widget.flags
    widget.reader.setCurrentIndex(widget.reader.findData('network'))
    assert widget.cfg['inventorySource']=='network' and not widget.reader_open.isHidden()
    widget.limits['weeklyLimit'].setValue(3)
    assert widget.cfg['weeklyLimit']==3 and widget.scan_button.text()=='读取仓库'
    widget.reader.setCurrentIndex(widget.reader.findData('ocr'))
    assert widget.cfg['inventorySource']=='ocr' and widget.reader_open.isHidden()
    nav.pivot.items['仓库'].click()
    root = widget.root
    a = dict(id='a', fp='first', set='SwingJazz', slot='4', main='atk_', level=0,
             decision='保留', reasons=['备用 · 通用'], review=[], marked=False)
    b = dict(a, id='b', fp='second', level=3)

    def finish(rows):
        save(root/'preview.json', dict(discs=rows, counts={}))
        widget.preview_job = root
        widget.preview_finished.emit(root, '')
        app.processEvents()
        assert widget.preview_job is None

    alias_row = dict(a, pinKeys=['stable', a['fp']])
    finish([alias_row])
    widget.table.selectRow(0)
    widget.pin_button.trigger()
    assert widget.cfg['pins'] == ['stable']
    widget.detail_button.click()
    assert widget.detail.pin.text() == '取消个人保护'
    widget.detail.pin.click()
    assert widget.cfg['pins'] == []
    widget.detail.back.emit()

    finish([a, b])
    widget.inventory_filter.kind.setCurrentText('额外备用')
    widget.table.selectRow(1)
    widget.detail_button.click()
    widget.detail.tabs.setCurrentIndex(1)
    updated = dict(b, id='rescanned', reasons=[], decision='弃置候选')
    finish([updated, a])
    assert widget.tabs.currentWidget() is widget.detail, 'background job closed the open disc'
    assert widget.detail.tabs.currentIndex() == 1 and '弃置候选' in widget.detail.title.text()
    assert widget.detail_selection[1]['id'] == 'rescanned' and widget.table.isRowHidden(0)
    widget.detail.back.emit()
    assert widget.inventory_filter.selected_identity() == ('a', 'first')
    finish([updated, a])
    assert widget.tabs.currentIndex() == 0

    widget.inventory_filter.kind.setCurrentIndex(0)
    widget.table.selectRow(0)
    widget.detail_button.click()
    finish([a, dict(updated, ambiguous=True), dict(updated, id='copy', ambiguous=True)])
    assert widget.tabs.currentIndex() == 0 and widget.tabs.currentWidget() is nav.pages['仓库预览']
    widget.table.selectRow(1)
    widget.detail_button.click()
    finish([a, updated])  # A surviving lookalike cannot identify the original physical copy.
    assert widget.tabs.currentIndex() == 0
    widget.table.selectRow(1)
    widget.detail_button.click()
    finish([a])
    assert widget.tabs.currentIndex() == 0

    observation = dict(status='verified', started=1, finished=2, disc=dict(fingerprint=a['fp'],
        setKey='SwingJazz', slotKey='4', level=0, rarity='S', markState='unmarked', lockState='unlocked',
        observedValues=dict(main={'atk_':7.5}, subs={'hp':112, 'atk':19, 'pen':9})))
    save(root/'single-disc.json', observation)
    widget.reload()
    widget.open_disc_detail(probe=True)
    widget.detail.tabs.setCurrentIndex(1)
    finish([a])  # Inventory and probe may share the exact same fingerprint.
    assert widget.tabs.currentWidget() is widget.detail and '单盘实测' in widget.detail.version.text()
    assert widget.detail.tabs.currentIndex() == 1
    save(root/'single-disc.json', dict(observation, started=3, finished=4))
    widget.reload()
    assert widget.tabs.currentIndex() == 0
    widget.detail_button.click()
    save(tmp/'accounts/2/preview.json', dict(discs=[a], counts={}))
    ctx.current_instance_idx = 2
    widget.on_interface_shown()
    assert widget.tabs.currentIndex() == 0 and widget.root.name == '2'
    # Switch between the player's account and an empty one from the embedded record page.
    from drive_disc_manager.store import read
    receipt = dict(batch='one', status='needs_review', beforeTotal=2, afterTotal=1, afterInventory='saved',
                   independentNative=dict(updated=12, status=1), entries=[dict(set='TheSkyAblaze', slot='1',
                   main='hp', level=15, source='游戏原有弃置', after='已不在库存', beforeMultiplicity=1)],
                   stockChanges=[dict(set='TheSkyAblaze', slot='2', main='atk', level=0, kind='missing',
                                      after='清单外少盘', count=1)])
    save(root/'dismantle-result.json', receipt)
    ctx.current_instance_idx = 1
    widget.on_interface_shown()
    nav.open('库存记录'); panel = widget.salvage_page
    assert widget.tabs.currentWidget() is panel and panel.grid.rowCount() == 1
    assert panel.grid.item(0, 0).text() == '拂晓行纪' and panel.grid.item(0, 4).text() == '清单外少盘'
    panel.issues.setChecked(False)
    assert panel.grid.rowCount() == 2 and not panel.close_batch.isHidden()
    panel.close_batch.click()
    assert read(root/'dismantle-result.json')['status'] == 'reviewed' and panel.close_batch.isHidden()
    ctx.current_instance_idx = 2
    widget.on_interface_shown(); nav.open('库存记录')
    assert panel.grid.rowCount() == 0 and panel.title.text() == '还没有库存记录'
    check_equip_review(widget, tmp, ctx)
    widget.close()


def check_equip_review(widget, tmp, ctx):
    from PySide6.QtWidgets import QDialog, QTableWidget
    from unittest.mock import patch
    from drive_disc_manager.store import config, digest, read, save

    root = widget.root
    name = '艾莲测试队'
    profile = dict(name=name, agentKey='Ellen', request=dict(characterName='艾莲'))
    cfg = dict(config(root), inventorySource='network', profiles=[profile])
    save(root/'config.json', cfg)
    save(tmp/'plugin_data/wiki_data/zzz_translation.json', dict(character={
        '1':dict(EN='Ellen', CHS='艾莲', code='Ellen'), '2':dict(EN='Nicole', CHS='妮可', code='Nicole')}))
    request = dict(name=name, account=2, configuration=digest(cfg), rules=None, selection='fresh')
    before = [dict(profile=name, agent='Ellen', slot=n, selectionFp=str(n), location='', ownerState='bag', set='TheSkyAblaze',
                   level=0, rarity='S', observedValues=dict(main={'atk_':7.5}, subs={'crit_':2.4})) for n in range(1,7)]
    after = [dict(t, location='Nicole', ownerState='worn') if t['slot']==4 else t for t in before]
    review = dict(request=request, before=before, after=after)
    ctx.run_context = SimpleNamespace(is_context_stop=True)
    widget.navigation.open('我的配队')
    widget.finish_equip(str(root), name, '', review)
    assert widget.apply_button.text() == '查看换装变化' and widget.apply_button.isEnabled()
    def inspect(dialog):
        diff, lineup = dialog.findChildren(QTableWidget)
        assert [diff.item(0,c).text() for c in range(4)] == [name+' · 4 号位','装备者','背包','妮可']
        assert diff.rowCount() == 1 and lineup.rowCount() == 6 and lineup.item(3,5).text() == '妮可'
        return 0
    with patch.object(QDialog, 'exec', inspect), patch('drive_disc_manager.manual_equip.run_confirmed') as run:
        widget.apply_current()
        run.assert_not_called()
    assert widget.equip_review is None and read(root/'config.json') == cfg
    widget.finish_equip(str(root), name, '', review)
    from PySide6.QtWidgets import QApplication
    from threading import Event
    done = Event()
    with patch.object(QDialog, 'exec', return_value=1), patch('drive_disc_manager.manual_equip.run_confirmed', side_effect=lambda *_:done.set()) as run:
        widget.apply_current()
        assert done.wait(3)
        run.assert_called_once_with(ctx, request)
    QApplication.processEvents()
    widget.finish_equip(str(root), name, '', review)
    ctx.current_instance_idx = 1
    widget.on_interface_shown()
    assert widget.equip_review is None and widget.apply_button.text() == '一键换装'


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(parser.parse_args().home.resolve(), Path(tmp))
    print('PASS disc detail keeps the selected view and handles changed inventory, observation and account')

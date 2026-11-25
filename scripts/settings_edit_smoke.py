"""One rule and one quota edited in a temporary OneDragon account, without a game."""
import argparse
import json
import os
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Event
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(home, tmp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from PySide6.QtWidgets import QApplication, QInputDialog
    from one_dragon.utils import os_utils
    from drive_disc_manager import subscriptions
    from drive_disc_manager.quota_editor import QuotaEditor
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, digest, save

    app = QApplication([])
    os_utils.set_work_dir(str(home))
    account = tmp/'1'
    key = 'matrix/SwingJazz/5'
    save(account/'rules.json', dict(fields={key:['atk_']}, version='fixture'))
    context = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:tmp)
    widget = ManagerInterface(context)
    widget.on_interface_shown()
    assert widget.auto_sync.isChecked() and '暂无记录' in widget.rule_stamp.text()
    started, proceed = Event(), Event()

    def source():
        started.set()
        assert proceed.wait(5)
        return {key:['def_']}, {'Set+Main':'42'}, {key:['Set+Main']}

    def finish():
        proceed.set()
        deadline = monotonic()+5
        while widget.rule_updates and monotonic() < deadline:
            app.processEvents()
            sleep(.005)
        assert not widget.rule_updates

    with patch.object(subscriptions, 'sources', side_effect=source) as fetch:
        widget.update_rules()
        assert started.wait(1) and not widget.sync_button.isEnabled()
        widget.update_rules()
        assert fetch.call_count == 1
        widget.rule_search.setText('5号')
        assert widget.visible_keys == [key]
        widget.editor.setPlainText('暂存的配置')
        finish()
        assert widget.rules['fields'][key] == ['def_'] and widget.editor.toPlainText() == '暂存的配置'
        assert widget.rules['version'][:8] in widget.rule_stamp.text() and '暂无记录' not in widget.rule_stamp.text()

    def switch(*args):
        context.current_instance_idx = 2
        widget.on_interface_shown()
        return '继承上游', True

    with patch.object(QInputDialog, 'getItem', side_effect=switch):
        try:
            widget.edit_rule()
        except ValueError as exc:
            assert '账号已切换' in str(exc)
        else:
            raise AssertionError('rule dialog saved after account switch')
    assert not (account/'config.json').exists() and not (tmp/'2/config.json').exists()
    assert '暂存的配置' != widget.editor.toPlainText()

    context.current_instance_idx = 1
    run = context.run_context = SimpleNamespace(is_app_registered=lambda key: key == 'inventory_scan')
    option = SimpleNamespace(scan_drive_disk=False)
    def scanner_config(key, idx, group_id):
        assert (key,idx,group_id) == ('inventory_scan',1,'one_dragon')
        return option
    run.get_config = scanner_config
    cfg = config(account)
    group = dict(set='SwingJazz', slot='5', main='atk_')
    cfg['aliases'] = {'SwingJazz':'摇摆爵士'}
    broader = dict(set='摇摆爵士', count=3)
    cfg['reserve_rules'] = [broader, dict(set='摇摆爵士', slot=5, main='atk_', count=6)]
    save(account/'config.json', cfg)
    widget.on_interface_shown()
    assert widget.editor.toPlainText() == '暂存的配置'
    with patch.object(subscriptions, 'sources', side_effect=AssertionError('toggling must not fetch')):
        widget.auto_sync.click()
        assert config(account)['auto_sync'] is False and widget.editor.toPlainText() == '暂存的配置'
    widget.reset_text()
    original = json.loads(widget.editor.toPlainText())
    widget.editor.setPlainText(json.dumps(dict(original,reserve=9)))
    widget.auto_sync.click()
    assert config(account)['reserve'] == original['reserve']
    widget.save_text()
    assert config(account)['reserve'] == 9 and config(account)['auto_sync'] is True

    assert '开启「驱动盘」' in widget.scanner_note.text()
    option.scan_drive_disk = True
    widget.on_interface_shown()
    assert '已就绪' in widget.scanner_note.text()

    def inherit(dialog):
        assert dialog.custom.isChecked() and dialog.count.value() == 6
        assert '上级规则 3 张' in dialog.inherit.text()
        dialog.inherit.click()
        return True

    with patch.object(QuotaEditor, 'exec', inherit):
        assert widget.set_quota(dict(group, fp='fixture')) == '继承 · 通用'
    assert config(account)['reserve_rules'] == [broader]
    # Create a preset for a non-scanned set, then verify its scope on two different slots.
    page = widget.reserve_presets
    page.suit.setCurrentIndex(page.suit.findData('SwingJazz'))
    assert page.scope() == {'set':'SwingJazz'} and '通用 3 张' in page.current.text()
    page.slot.setCurrentIndex(page.slot.findData('5'))
    assert page.scope() == {'set':'SwingJazz', 'slot':'5'}

    def narrow(dialog):
        dialog.purpose.setCurrentText('偏暴击')
        dialog.custom.click()
        dialog.count.setValue(2)
        dialog.purpose.setCurrentText('偏暴伤')
        dialog.custom.click()
        dialog.count.setValue(4)
        return True

    with patch.object(QuotaEditor, 'exec', narrow):
        page.change.click()
    assert config(account)['reserve_rules'] == [broader, dict(set='SwingJazz', slot='5', purpose='偏暴击', count=2, stats=['crit_']),
                                               dict(set='SwingJazz', slot='5', purpose='偏暴伤', count=4, stats=['crit_dmg_'])]
    assert '偏暴伤 4 张' in page.current.text()
    page.slot.setCurrentIndex(page.slot.findData('4'))
    assert '偏暴伤' not in page.current.text()
    assert page.grid.rowCount() == 3
    from drive_disc_manager.purpose_editor import PurposeEditor
    assert '角色' in page.grid.item(0,4).text() and page.grid.item(2,4).text() == '暴击伤害'
    probe_row = dict(set='SwingJazz',slot='4',main='def_',fp='demo-ap',decision='弃置候选',marked=False,
                     keptBeforeReserves=False,rolls={'anomProf':1},development={'upgrades':4},review=[],
                     reasons=[],recommendations=[],explanation=[])
    draft = dict(config(account), reserve=0,reserve_rules=[])
    dialog = QuotaEditor(widget,{'set':'SwingJazz'},draft,{},'摇摆爵士',report={'discs':[probe_row]})
    def abnormal(editor):
        editor.name.setText('异常队')
        assert not editor.save_button.isEnabled()
        editor.checks['anomProf'].click()
        assert editor.save_button.isEnabled()
        return True
    with patch.object(PurposeEditor,'exec',abnormal):
        dialog.edit_purpose(new=True)
    dialog.try_button.click()
    assert '保留 0 → 1 张，其中备用 1 张' in dialog.impact.text()
    dialog.count.setValue(2)
    assert not dialog.impact.text()
    dialog.purpose.setCurrentText('通用')
    dialog.purpose.setCurrentText('异常队')
    assert dialog.result_rules()[0] == [dict(set='SwingJazz',purpose='异常队',count=2,stats=['anomProf'])]
    assert draft['reserve_rules']==[] and probe_row['decision']=='弃置候选'
    dialog.inherit.click()
    assert dialog.stat_note.text() == '此用途未设名额'
    dialog.custom.click()
    assert dialog.stat_note.text() == '异常精通' and dialog.count.value() == 2
    dialog.purpose.setCurrentText('通用')
    def smart_sort(editor):
        assert editor.by_role.isChecked() and not editor.checks['crit_'].isEnabled()
        editor.by_role.click()
        assert editor.checks['crit_'].isEnabled()
        editor.by_role.click()
        return True
    with patch.object(PurposeEditor,'exec',smart_sort):
        dialog.edit_purpose(new=False)
    dialog.count.setValue(3)
    assert dialog.result_rules()[0][-1] == dict(set='SwingJazz',purpose='通用',count=3)
    cfg = config(account)
    cfg['profiles'] = [dict(name='临时队',request=dict(roleSystem='ATTACK',characterName='艾莲'))]
    save(account/'config.json', cfg)
    widget.on_interface_shown()
    def edit_other_setting(*args):
        save(account/'config.json', dict(config(account), reserve=8))
        widget.on_interface_shown()
        return 0.25, True
    with patch.object(QInputDialog, 'getDouble', side_effect=edit_other_setting):
        try:
            widget.edit_tolerance()
        except ValueError as exc:
            assert '配置已更新' in str(exc)
        else:
            raise AssertionError('stale team edit replaced a newer configuration')
    assert config(account)['reserve'] == 8 and 'tolerancePct' not in config(account)['profiles'][0]
    newer = dict(config(account),reserve=7)
    widget.editor.setPlainText(json.dumps(dict(config(account),reserve=6)))
    save(account/'config.json',newer)
    try:
        widget.save_text()
    except ValueError as exc:
        assert 'reserve' in str(exc)
    else:
        raise AssertionError('concurrent change to the same advanced option was lost')
    assert config(account) == newer
    widget.reset_text()
    team = dict(config(account)['profiles'][0], simultaneous='第一队', tolerancePct=.3, sourceId='test-agent',
                mainWindow='常态', windows=[dict(name='常态',weight=1,buffs=[])])
    save(account/'config.json',dict(config(account), profiles=[team], fieldProfile=team['name'], run_scanner=True))
    widget.reload()
    with patch.object(QInputDialog, 'getText', return_value=('爆发队',True)):
        widget.profile_menu.menu().actions()[0].trigger()
    current = config(account)
    original_team, variant = current['profiles']
    assert original_team == team and variant == dict(team, name='爆发队', simultaneous='')
    assert current['fieldProfile'] == original_team['name'] and widget.teams.currentRow() == 1
    widget.teams.selectRow(0)
    with patch.object(QInputDialog, 'getText', return_value=('常态队',True)):
        widget.profile_menu.menu().actions()[1].trigger()
    assert config(account)['fieldProfile'] == '常态队' and config(account)['profiles'][1] == variant
    def disc(key, slot, decision, reasons, marked=False):
        return dict(id=key,fp=key,set='SwingJazz',slot=slot, main='atk_',level=0,decision=decision,
                    marked=marked,reasons=reasons,review=[])
    discs = [disc('chosen','4','保留',['配队 · 常态队']),disc('spare','5','保留',['备用 · 偏暴击']),
             disc('old','5','已弃置',['游戏已标记弃置'],True)]
    save(account/'preview.json',dict(configuration=widget.config_hash,rules='older',counts={},discs=discs,
                                     assignments={'常态队':dict(ids=['demo'])}))
    widget.reload()
    assert '请重新计算' in widget.status.text() and not widget.apply_button.isEnabled()
    old_inventory = dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=[])
    save(account/'inventory.json', old_inventory)
    save(account/'preview.json', dict(widget.report, managerVersion='0.0.0', rules=widget.rules['version'],
                                      inventory=digest(old_inventory)))
    widget.reload()
    assert '插件版本已更新，请重新计算' in widget.status.text() and not widget.apply_button.isEnabled()
    (account/'inventory.json').unlink()
    browser = widget.inventory_filter
    browser.search.setText('摇摆爵士 5号')
    browser.kind.setCurrentText('额外备用')
    assert browser.total.text() == '1 / 3 张' and browser.selected_identity() == ('spare','spare')
    with patch.object(widget, 'edit_cfg') as change:
        widget.pin()
        change.assert_called_once_with(pins=['spare'])
    browser.kind.setCurrentText('已标记弃置')
    assert browser.selected_identity() == ('old','old') and widget.table.isRowHidden(1) and not widget.pin_button.isEnabled()
    browser.search.setText('无匹配')
    assert widget.table.currentRow() == -1 and browser.total.text() == '0 / 3 张'
    from drive_disc_manager.maintenance import exclusive
    with exclusive(account.parent/'_maintenance'), patch('drive_disc_manager.settings.import_inventory') as importer:
        try:
            widget.calculate(import_path='sample.json')
        except ValueError as exc:
            assert '任务或维护正在运行' in str(exc)
        else:
            raise AssertionError('the game worker did not retain its inventory')
        importer.assert_not_called()
    from PySide6.QtCore import Qt
    from drive_disc_manager.set_rule_editor import SetRuleEditor
    from drive_disc_manager.roster import compile_roster
    role_key = 'build/Ellen/sets2'
    fields = {role_key:['PolarMetal'],key:['atk_']}
    save(account/'rules.json',dict(fields=fields,version='sets-fixture'))
    widget.reload()
    widget.rule_search.setText('Ellen 两件套')
    assert widget.visible_keys == [role_key]
    def choose_set(dialog):
        dialog.search.setText('摇摆爵士')
        shown = [dialog.sets.item(i) for i in range(dialog.sets.count()) if not dialog.sets.item(i).isHidden()]
        assert len(shown)==1 and shown[0].toolTip()=='SwingJazz'
        shown[0].setCheckState(Qt.CheckState.Checked)
        return True
    with patch.object(QInputDialog,'getItem',return_value=('额外接受',True)), patch.object(SetRuleEditor,'exec',choose_set):
        widget.edit_rule()
    assert config(account)['overrides'][role_key] == dict(mode='add',values=['SwingJazz'])
    agent = dict(label='艾莲',selected=True, builds=['Ellen'],guides=[])
    roles, _, _ = compile_roster([agent],fields,{role_key:dict(mode='add',values=['摇摆爵士'])},{'摇摆爵士':'SwingJazz'})
    assert set(roles)=={'swingjazz','polarmetal'}
    from drive_disc_manager.main_rule_editor import MainRuleEditor
    sub_key = 'guide/Ellen Joe/subs'
    fields[sub_key] = ['CR% (until cap) > CD%³ = ATK%']
    save(account/'rules.json', dict(fields=fields, version='role-stats-fixture'))
    widget.reload()
    widget.rule_search.setText('Ellen 副词条')
    assert widget.visible_keys == [sub_key]
    def choose_stats(dialog):
        assert all(dialog.checks[k].isChecked() for k in ('crit_', 'crit_dmg_', 'atk_'))
        assert '暴击伤害' in dialog.checks['crit_dmg_'].text()
        dialog.checks['crit_dmg_'].click()
        return True
    with patch.object(QInputDialog,'getItem',return_value=('只接受',True)), patch.object(MainRuleEditor,'exec',choose_stats):
        widget.edit_rule()
    accepted = config(account)['overrides'][sub_key]
    assert accepted['mode'] == 'only' and set(accepted['values']) == {'crit_', 'atk_'}
    widget.close()


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    with TemporaryDirectory() as tmp:
        smoke(args.parse_args().home.resolve(), Path(tmp))
    print('PASS embedded settings: responsive sync, account switch and inherited quota')

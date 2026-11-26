"""Two encounter teams, one embedded target and a twelve-disc local optimizer smoke."""
import argparse
import os
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from time import monotonic, sleep
from types import SimpleNamespace
from unittest.mock import patch


def smoke(host, temp):
    os.environ['QT_QPA_PLATFORM'] = 'offscreen'
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src')]
    from PySide6.QtCore import QCoreApplication, QEvent
    from PySide6.QtWidgets import QApplication, QMessageBox
    from one_dragon.utils import os_utils
    from drive_disc_manager.engine import NAME, VERSION, managed
    from drive_disc_manager.loadout import verify_bindings
    from drive_disc_manager.optimizer import candidate_pool, loopback_json
    from drive_disc_manager.planner import plan
    from drive_disc_manager.roster import scanner_agent_key
    from drive_disc_manager.settings import ManagerInterface
    from drive_disc_manager.store import config, read, save

    app = QApplication([])
    os_utils.set_work_dir(str(temp))
    root = temp/'accounts/1'
    wiki = read(host/'plugin_data/wiki_data/zzz_translation.json')
    save(temp/'plugin_data/wiki_data/zzz_translation.json', wiki)
    exe = Path('_optimizer')/VERSION/NAME
    (root.parent/exe).parent.mkdir(parents=True)
    shutil.copyfile(host/'plugin_data/drive_disc_manager'/exe, root.parent/exe)
    ice = ['普罗米娅', '莱卡恩', '丽娜']
    fire = ['11号', '珂蕾妲', '耀嘉音']
    pref = dict(agents={name: {} for name in ice+fire}, parties=[ice, fire])
    for name in ('普罗米娅', '耀嘉音'):
        pref['agents'][name] = dict(mindscape=6, engineRank=5)
    save(root/'challenge-preferences.json', pref)
    template = dict(name='火队', agentKey=scanner_agent_key(wiki, '11号'),
                    request=dict(characterName='11号', roleSystem='ATTACK', baseATK=1250,
                                 baseCritRate=85, baseCritDmg=50, characterElement='FIRE'),
                    squad=[dict(agent='珂蕾妲', mindscape=1), dict(agent='耀嘉音', mindscape=3)],
                    mainWindow='爆发', windows=[dict(name='爆发', weight=1, buffs=[])])
    save(root/'config.json', dict(config(root), profiles=[template], fieldProfile='火队'))
    ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:root.parent)
    widget = ManagerInterface(ctx)
    widget.on_interface_shown()
    page, panel = widget.challenge, widget.optimizer_page

    def save_teams():
        widget.navigation.open('关卡分队')
        page.mode.setCurrentText('危局强袭战')
        for controls, weak, role in zip(page.controls, ['冰属性','火属性'], ['击破','支援']):
            controls[0].setCurrentText(weak)
            controls[2].setCurrentText(role)
        page.compare.click()
        assert [set(lane['team']) for lane in page.lineup] == [set(ice), set(fire)], page.status.text()
        page.save_lineup.click()
        assert widget.tabs.currentWidget() is widget.navigation.pages['我的配队']

    warnings = []
    try:
        with patch.object(QMessageBox, 'warning', side_effect=lambda parent, title, msg: warnings.append(msg)):
            save_teams()
            cfg = config(root)
            assert cfg['profiles'][0] == template and cfg['fieldProfile'] == template['name']
            group = cfg['profiles'][1:]
            assert len(group) == 6 and sum(bool(p.get('needsTarget')) for p in group) == 5
            assert [p['request']['characterName'] for p in group[:2]] == [ice[0], fire[0]]
            assert group[1]['squad'] == template['squad'] and group[1]['windows'] == template['windows']
            assert group[0]['mindscape'] == 6 and '冰属性' in widget.teams.item(1,0).toolTip()
            assert not widget.apply_button.isEnabled() and '5 位' in widget.apply_button.toolTip()
            try:
                verify_bindings(cfg, group[1]['name'], wiki)
            except ValueError as exc:
                assert '5 位' in str(exc)
            else:
                raise AssertionError('Applied a partial group')
            save_teams()
            assert config(root) == cfg
            widget.teams.selectRow(1)
            widget.refresh_target_button.trigger()
            assert widget.tabs.currentWidget() is panel and panel.import_button.text() == '用于普罗米娅的配装', warnings
            deadline = monotonic()+20
            while not (panel.endpoint and panel.import_button.isEnabled()) and monotonic()<deadline:
                app.processEvents()
                sleep(.02)
            assert panel.endpoint and panel.import_button.isEnabled(), panel.status.text()
            request = dict(characterName='普罗米娅', roleSystem='ANOMALY', mode='ANOMALY_AP',
                           baseATK=1000, extraStats={'ANOMALY_PROFICIENCY':90})
            loopback_json(panel.endpoint, '/api/character-targets', dict(plans=[dict(
                id='promeia-test', characterName='普罗米娅', request=request)]))
            panel.import_button.click()
            cfg = config(root)
            assert not warnings, warnings
            assert cfg['fieldProfile'] == template['name'] and cfg['profiles'][1]['sourceId'] == 'promeia-test'
            assert cfg['profiles'][1]['squad'] == group[0]['squad'] and '4 位' in widget.apply_button.toolTip()

            discs = []
            for slot, main in enumerate(('hp','atk','def','def_','ice_dmg_','atk_'), 1):
                for i in range(2):
                    subs = ('crit_', 'crit_dmg_', 'pen', 'anomProf')
                    discs.append(dict(id=f'{slot}-{i}', slotKey=str(slot), mainStatKey=main, rarity='S', level=15,
                                      substats=[dict(key=k, upgrades=4 if j==i else 0) for j,k in enumerate(subs)],
                                      setKey='SwingJazz', lock=False, trash=False, location='', lockState='unlocked',
                                      ownerState='empty', markState='unmarked'))
            inventory = dict(format='ZOD', version=1, dbVersion=2, discs=discs, characters=[], wengines=[],
                             audit=dict(game_total=12, exported=12, delta=0))
            with managed(root) as endpoint:
                result = plan(inventory, cfg, {'version':'smoke'}, {'SwingJazz':'摇摆爵士'},
                              lambda url,p,discs,current: candidate_pool(endpoint,p,discs,current), wiki=wiki)
            assert not result['issues'], result['issues']
            first, second = [set(result['assignments'][p['name']]['ids']) for p in group[:2]]
            assert len(first) == len(second) == 6 and first.isdisjoint(second)
            assert all(any(r.startswith('配队 · ') for r in d['reasons']) for d in result['discs'])
            token = widget.edit_token()
            ctx.current_instance_idx = 2
            try:
                widget.finish_edit(token, cfg)
            except ValueError as exc:
                assert '账号' in str(exc)
            else:
                raise AssertionError('Saved into a stale account')
            assert not config(root.parent/'2')['profiles']
    finally:
        panel.stop()
        widget.close()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    print('PASS two teams saved once, original squad and windows reused, embedded target bound, 12 distinct discs assigned')


if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--home', required=True, type=Path)
    with TemporaryDirectory() as folder:
        smoke(parser.parse_args().home.resolve(), Path(folder))

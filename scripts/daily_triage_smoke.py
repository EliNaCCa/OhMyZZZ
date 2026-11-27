"""Three-disc smoke for the daily entry point. Pass the OneDragon installation folder."""
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
import os
import sys
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(sys.argv[1])/'src')]
from drive_disc_manager.store import config, read, save, digest
from drive_disc_manager.cn_export import normalize
from drive_disc_manager.manager_app import ManagerApp
from drive_disc_manager.service import preview
with TemporaryDirectory() as tmp:
    root=Path(tmp)/'1'; cfg=dict(config(root),profiles=[dict(name='11号',request=dict(characterName='11号'))],fieldProfile='11号',auto_sync=False,run_scanner=False,inventorySource='cn_export', includeOwned=False, reserve=1,trimRecommended=True)
    original=dict(slotKey='4',setKey='TheSkyAblaze',level=0,rarity='S',mainStatKey='atk_',location='',lock=False,trash=False)
    def disc(i,keys): return dict(original,id=str(i),substats=[dict(key=k,upgrades=1) for k in keys])
    a=disc(11,['hp','def','crit_']); b=disc(12,['def','def_','hp']); c=disc(13,['hp_','def','pen'])
    z=normalize(dict(format='ZOOD',version=1,source='zzz-drive-exporter',discs=[a,b,c]))
    rules={'fields':{'matrix/TheSkyAblaze/4':['atk_']},'version':'v'}
    save(root/'cn-inventory.json',z); save(root/'config.json',cfg); save(root/'rules.json',rules)
    save(root/'cn-preview.json',dict(assignments={'11号':{'ids':['13']}}, discs=[dict(fp='t',buildId='13',pinKeys=['cn-uid/13'])]))
    def fail(message): raise AssertionError(message)
    manager=SimpleNamespace(ctx=SimpleNamespace(current_instance_idx=1),root=root,account=1,group='one_dragon',round_success=str,round_fail=fail)
    with patch('drive_disc_manager.planner.as_disc',side_effect=AssertionError('daily started preparing builds')), patch('drive_disc_manager.engine.managed',side_effect=AssertionError('daily started optimizer')):
        for _ in range(2):
            ManagerApp.manage_inventory(manager); r=read(root/'cn-preview.json')
            rows={x['id']:x for x in r['discs']}
            assert r['triageOnly'] and not r['issues'] and not r['assignments']
            assert rows['11']['upgrade']['target']==3 and rows['12']['checkForDiscard'], rows
            assert '已保存配装用盘' in rows['13']['reasons']
        for new_stat, next_target in [('hp_',None),('crit_dmg_',6)]:
            updated=dict(a,level=3,substats=a['substats']+[dict(key=new_stat,upgrades=1)])
            z=normalize(dict(format='ZOOD',version=1,source='zzz-drive-exporter',discs=[updated,b,c]))
            save(root/'cn-inventory.json',z); ManagerApp.manage_inventory(manager)
            row=read(root/'cn-preview.json')['discs'][0]
            assert (row.get('upgrade') or {}).get('target')==next_target, row
        from drive_disc_manager.disc_trace import teams_for_disc
        from drive_disc_manager.inventory_comparison import verify_cn_inventory
        from drive_disc_manager.live import Marker
        from collections import Counter
        # Pause the profile for two daily runs, then turn it back on after
        # upgrading its saved disc. Its physical UID stays the same.
        paused = dict(cfg, profiles=[dict(cfg['profiles'][0], enabled=False)])
        save(root/'config.json', paused)
        for _ in range(2):
            ManagerApp.manage_inventory(manager)
            r = read(root/'cn-preview.json')
            old_pick = next(x for x in r['discs'] if x['id'] == '13')
            assert old_pick['checkForDiscard'] and not teams_for_disc(r, old_pick)
        upgraded = dict(c, level=3, substats=[*c['substats'], dict(key='def_',upgrades=1)])
        z = normalize(dict(format='ZOOD',version=1,source='zzz-drive-exporter',discs=[a,b,upgraded]))
        save(root/'cn-inventory.json',z); save(root/'config.json',cfg)
        ManagerApp.manage_inventory(manager)
        remote = read(root/'cn-preview.json')
        saved = next(r for r in remote['discs'] if r['id'] == '13')
        assert '已保存配装用盘' in saved['reasons'], saved
        assert [name for name, *_ in teams_for_disc(remote,saved)] == ['11号']
        # First game verification has no earlier OCR preview to inherit from.
        samples = [dict(d, ownerState='bag', lockState='unlocked', markState='unmarked', trash=False, observedGrade='S') for d in z['discs']]
        view = verify_cn_inventory(z, dict(source='Zenless Optimizer', audit=dict(game_total=3)), samples, {})
        save(root/'inventory.json', view)
        local = preview(root, refresh=False, verified_game=True, cn_plan=remote, triage_only=True)
        kept = next(r for r in local['discs'] if r['exportUid'] == '13')
        assert [name for name, *_ in teams_for_disc(local,kept)] == ['11号']
        marker = Marker.__new__(Marker)
        marker.rows = {r['fp']:r for r in local['discs']}
        marker.seen_counts = Counter(r['fp'] for r in local['discs'])
        assert kept['fp'] in marker.phase_targets('lock') and kept['fp'] not in marker.phase_targets('mark')
        os.environ['QT_QPA_PLATFORM'] = 'offscreen'
        from PySide6.QtWidgets import QApplication
        from drive_disc_manager.settings import ManagerInterface
        app = QApplication([])
        ctx = SimpleNamespace(current_instance_idx=1, get_plugin_data_dir=lambda _:root.parent,
                              run_context=SimpleNamespace(is_context_stop=True))
        widget = ManagerInterface(ctx)
        widget.on_interface_shown()
        widget.navigation.open('我的配队')
        widget.refresh_teams('11号')
        assert widget.slots.rowCount() == 1 and widget.slots.item(0,3).text() == '3'
        assert widget.slots.item(0,0).text() == '4' and not widget.build_tabs.isHidden()
        assert not widget.apply_button.isEnabled() and widget.teams.item(0,4).text() == '上次配装'
        widget.close()
    print('PASS daily x2, saved manual build, discard candidate, +3 reveal continue/stop')
    print('PASS profile off/on, saved UID after a level change, team trace and game mark lists')

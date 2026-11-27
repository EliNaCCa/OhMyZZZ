"""Three-disc smoke for the daily entry point. Pass the OneDragon installation folder."""
from pathlib import Path
from types import SimpleNamespace
from tempfile import TemporaryDirectory
from unittest.mock import patch
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
    print('PASS daily x2, saved manual build, discard candidate, +3 reveal continue/stop')

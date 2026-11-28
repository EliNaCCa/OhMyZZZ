"""First daily plan from three CN export rows, with only the selected item read in game."""
from contextlib import ExitStack
from pathlib import Path
import sys
from tempfile import TemporaryDirectory
from types import SimpleNamespace as NS
from unittest.mock import patch

host = Path(sys.argv[1])
sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(host/'src'), str(host/'plugins')]
from one_dragon.utils import os_utils
from drive_disc_manager import incremental_marks as delta
from drive_disc_manager.cn_export import EXPORT_NAME, SOURCE
from drive_disc_manager.live import Marker
from drive_disc_manager.manager_app import ManagerApp
from drive_disc_manager.store import read, save


with TemporaryDirectory() as tmp:
    os_utils.set_work_dir(tmp)
    root = Path(tmp)/'1'
    readings = read(host/'plugin_data/drive_disc_manager/1/recent-discs.json')['discs'][:3]
    # Recorded player stats, temporary IDs. A pinned unlocked piece, an already locked piece,
    # and an unknown suit exercise three different inspection states.
    fields = ('setKey', 'slotKey', 'level', 'rarity', 'mainStatKey', 'substats')
    raw = [dict({k:r[k] for k in fields}, id=str(i+101), location='', lock=i == 1, trash=False)
           for i,r in enumerate(readings)]
    raw[2]['setKey'] = 'NoRules'
    path = root/'exports'/EXPORT_NAME
    export = dict(format='ZOOD', version=1, source=SOURCE, discs=raw)
    save(path, export)
    save(root/'config.json', dict(inventorySource='cn_export', auto_sync=False, applyMarkers=True,
                                  pins=['cn-uid/101'], reserve=0))
    save(root/'owned-agents.json', ['Alpha'])
    save(root/'rules.json', dict(version='fixture', fields={"matrix/Dawn'sBloom/4":['atk_'],
         'build/Alpha/sets4':["Dawn'sBloom"], 'build/Alpha/slot4':['ATK%'], 'build/Alpha/subs':['CR, CD']}))
    scanner = NS(config=NS(scan_drive_disk=True), drive_disk_scanner=NS(parser=None), _ensure_screen_areas=lambda:None)
    app = NS(root=root, account=1, group='one_dragon', ctx=NS(current_instance_idx=1,
             controller=NS(game_win=NS(is_win_active=True)), run_context=NS(is_context_running=True, get_application=lambda *a:scanner)),
             round_success=str, round_fail=lambda s:(_ for _ in ()).throw(AssertionError(s)))
    looked = []
    def pages(scanner, total, guard, *, by_acquisition):
        assert total == 3 and by_acquisition
        guard()
        for offset, i in enumerate([1,0,2]):  # Deliberately different from UID order.
            r = dict(readings[i], setKey=raw[i]['setKey'], observedGrade='S', lock=i == 1,
                     lockState='locked' if i == 1 else 'unlocked', orderedCell=(0,offset),
                     ownerState='bag', location='', markState='unmarked', trash=False)
            looked.append(101+i)
            yield r, lambda:r
        raise AssertionError('read past the last target')

    def mark(marker):
        assert set(marker.scope) == {'101'}
        assert {r['exportUid'] for r in marker.phase_targets('lock').values()} == {'101'}
        return {}

    with ExitStack() as stack:
        for target, value in [('scanner_status.scanner_status',(True,'')), ('task_order.following_native',None),
                              ('dismantle.following_native',None)]:
            stack.enter_context(patch('drive_disc_manager.'+target, return_value=value))
        stack.enter_context(patch('drive_disc_manager.equip_targets.warehouse_observations', pages))
        stack.enter_context(patch.object(Marker, 'run', mark))
        note = ManagerApp.manage_inventory(app)
    assert looked == [102,101,103] and '首次整理完成' in note, note
    facts = read(root/delta.FILE)['facts']
    assert set(facts) == {'101','102','103'} and facts['102']['disc']['markState'] == 'unknown'
    assert not (root/'inventory.json').exists()
    from drive_disc_manager.service import preview
    cn = read(root/'cn-inventory.json')
    plan = preview(root, refresh=False, triage_only=True)
    assert not delta.worklist(cn, plan, facts, {}, None)[1]
    _, audited = delta.worklist(cn, plan, facts, {}, NS(dismantle_abandon=True, dismantle_level='A及以下'))
    assert audited == {'102','103'}
    from drive_disc_manager import disc_order
    from drive_disc_manager.cn_export import refresh
    state = read(root/disc_order.FILE)
    assert {uid:i['number'] for uid,i in state['live'].items()} == {'101':2,'102':3,'103':1}
    # Removed one while a new, smaller game UID arrives. The total stays three.
    save(path, dict(export, discs=[raw[2],dict(raw[0], id='8'),raw[1]]))
    refresh(root, lambda:None)
    state = read(root/disc_order.FILE)
    assert state['live']['8']['number'] == 4 and state['live']['102']['number'] == 3
    assert [(r['uid'],r['number'],r['lastRank']) for r in state['removed']] == [('101',2,2)]
    anchors = disc_order.PageRanks(5)
    for page,offset,uid in [(0,0,'a'),(0,1,'b'),(0,2,'c'),(1,0,'c'),(1,1,'d'),(2,0,'x')]:
        anchors.see({'orderedCell':(page,offset)},uid)
    assert anchors.ranks() == dict(a=1,b=2,c=3,d=4)
    print('PASS three ordered CN discs, stable numbers, same-total replacement, last position and overlap')

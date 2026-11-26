"""One ZOD disc over loopback, two game weeks in a temporary account."""
import argparse
import base64
import hashlib
import json
import socket
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from threading import Thread
from types import SimpleNamespace
from unittest.mock import patch


def serve(sock, exported):
    with sock.accept()[0] as peer:
        end = bytes((13, 10))
        req = b''
        while end*2 not in req:
            part = peer.recv(2048)
            assert part
            req += part
        key = next(line.split(b':',1)[1].strip() for line in req.splitlines() if line.lower().startswith(b'sec-websocket-key:'))
        accept = base64.b64encode(hashlib.sha1(key+b'258EAFA5-E914-47DA-95CA-C5AB0DC85B11').digest())
        peer.sendall(end.join((b'HTTP/1.1 101 Switching Protocols', b'Upgrade: websocket', b'Connection: Upgrade', b'Sec-WebSocket-Accept: '+accept, b'', b'')))
        body = json.dumps(exported).encode()
        peer.sendall(bytes((129,126)) + len(body).to_bytes(2, 'big') + body)
        peer.recv(1024)


def smoke(home, tmp):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    from one_dragon.utils import os_utils
    from drive_disc_manager.frequency import ManagerRunRecord
    from drive_disc_manager.network_reader import fetch_export, capture
    from drive_disc_manager.service import preview
    from drive_disc_manager.store import config, read, save, inventory
    os_utils.set_work_dir(str(tmp))
    root = tmp/'accounts'/'1'
    disc = dict(setKey='SwingJazz', slotKey='4', mainStatKey='atk_', rarity='S', level=0, location='', lock=False, trash=False,
                substats=[dict(key=key, upgrades=0) for key in ('crit_', 'hp', 'pen', '')])
    exported = dict(format='ZOD', version=1, source='ZZZ Packet Capture', characters=[dict(key='Nicole')], discs=[disc], wengines=[])
    with socket.socket() as sock:
        sock.bind(('127.0.0.1',0)); sock.listen(1)
        th = Thread(target=serve, args=(sock,exported), daemon=True); th.start()
        with patch('drive_disc_manager.network_reader.fetch_export', lambda:fetch_export(sock.getsockname()[1], identify=lambda _: 'fixture')):
            network = capture(root, lambda:None)
        th.join(3); assert not th.is_alive()
    cfg = dict(config(root), inventorySource='network', auto_sync=False, dailyLimit=2, weeklyLimit=3)
    with patch('drive_disc_manager.network_reader.fetch_export', return_value=('fixture', exported)):
        try: capture(root.parent/'2', lambda:None)
        except ValueError: pass
        else: raise AssertionError('bound reader reused by a different account')
    save(root/'config.json', cfg)
    result = preview(root, refresh=False)
    row, = result['discs']
    assert row['observedValues']['main']=={'atk_':7.5} and row['rolls']['crit_']==1
    check_reader_identity(cfg, disc, exported)
    assert read(root/'network-preview.json')['inventory']==result['inventory'] and not (root/'inventory.json').exists()
    try: inventory(network)
    except ValueError: pass
    else: raise AssertionError('network preview accepted as a game scan')
    ctx = SimpleNamespace(get_plugin_data_dir=lambda _:root.parent, game_account_config=SimpleNamespace(game_refresh_hour_offset=0))
    from drive_disc_manager.manager_app import ManagerApp
    ctx.current_instance_idx = 1
    ctx.run_context = SimpleNamespace(is_context_running=True)
    manager = SimpleNamespace(ctx=ctx, root=root, account=1, group='one_dragon', round_success=lambda msg:msg,
                              round_fail=lambda msg:(_ for _ in ()).throw(AssertionError(msg)))
    with patch('drive_disc_manager.network_reader.fetch_export', return_value=('fixture', exported)):
        assert '预览' in ManagerApp.manage_inventory(manager)
        assert read(root/'network-preview.json')['discs'][0]['main'] == 'atk_'
    record = ManagerRunRecord(ctx, 1)
    def finish(state):
        record.update_status(record.STATUS_RUNNING); record.update_status(state)
    with patch.object(record,'get_current_dt',return_value='20261126'):
        finish(record.STATUS_FAIL); assert not record.is_done
        finish(record.STATUS_SUCCESS); assert not record.is_done
        finish(record.STATUS_SUCCESS); assert record.is_done
    with patch.object(record,'get_current_dt',return_value='20261127'):
        record.check_and_update_status(); assert not record.is_done
        finish(record.STATUS_SUCCESS); assert record.is_done
    with patch.object(record,'get_current_dt',return_value='20261130'):
        record.check_and_update_status(); assert not record.is_done
    print('PASS loopback inventory preview, separate game scan, daily/weekly limits and Monday reset')


def check_reader_identity(cfg, disc, exported):
    from drive_disc_manager.equip_review import changed_slots
    from drive_disc_manager.manual_equip import SelectionChanged, confirmation, verify_selection
    from drive_disc_manager.network_reader import normalize
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import digest, equipment_names, fingerprint, toggle_pin

    names = equipment_names({})
    en, zh = 'TheSkyAblaze', '拂晓行纪'
    mains = ['hp', 'atk', 'def', 'atk_', 'atk_', 'atk_']
    discs = [dict(disc, slotKey=str(slot), mainStatKey=main, setKey=en, level=0,
                  substats=[dict(key=k, upgrades=0) for k in ('crit_', 'crit_dmg_', 'pen')])
             for slot, main in enumerate(mains, 1)]
    network = normalize(dict(exported, discs=discs))
    samples = [dict(d, setKey=zh, id='ocr-'+str(i), observedValues={
        group:{k:float(v) for k,v in stats.items()} for group,stats in d['observedValues'].items()})
        for i,d in enumerate(network['discs'])]
    ocr = dict(network, source='Zenless Optimizer', networkPreview=False, discs=samples,
               audit=dict(game_total=6, exported=6, delta=0))
    net_fp, ocr_fp = fingerprint(network['discs'][0]), fingerprint(samples[0])
    assert net_fp != ocr_fp
    for saved_pin, snapshot in [(ocr_fp, network), (net_fp, ocr)]:
        report = plan(snapshot, dict(cfg, pins=[saved_pin]), {}, names)
        row = report['discs'][0]
        assert '手动保护' in row['reasons'], 'switching readers lost a saved pin'
        assert not any('手动保护' in r['reasons'] for r in report['discs'][1:])
        assert toggle_pin(row, [net_fp, ocr_fp, 'unrelated']) == ['unrelated']
        assert toggle_pin(row, []) == [net_fp]
    ambiguous_names = names | {'AnotherSet':zh}
    ambiguous = plan(ocr, dict(cfg, pins=[net_fp]), {}, ambiguous_names)
    assert '手动保护' not in ambiguous['discs'][0]['reasons']
    profile = '示例配队'
    cfg = dict(cfg, profiles=[dict(name=profile, agentKey='Nicole')])

    def selection(snapshot):
        ids = [fingerprint(d) for d in snapshot['discs'][:6]]
        return dict(configuration=digest(cfg), inventory=digest(snapshot), assignments={profile:dict(ids=ids)})

    request, original = confirmation(1, profile, cfg, selection(network), network, preview_only=True)
    verify_selection(request, cfg, selection(ocr), ocr)
    for changed, difference in [(dict(samples[0], location='Other', ownerState='worn'), ('装备者', '背包', '朱鸢')),
                    (dict(samples[0], observedValues=dict(samples[0]['observedValues'], main={'hp':549.0})), ('主属性', '生命值 550', '生命值 549')),
                    (dict(samples[0], setKey='OtherSet'), ('套装', zh, 'OtherSet'))]:
        revised = dict(ocr, discs=[changed, *samples[1:]])
        try: verify_selection(request, cfg, selection(revised), revised)
        except SelectionChanged as change:
            rows = changed_slots(original, change.targets, names, {'Other':'朱鸢'})
            assert rows == [(profile+' · 1 号位', *difference)], rows
            verify_selection(change.current, cfg, selection(revised), revised)
            try: verify_selection(change.current, cfg, dict(selection(revised), rules='new'), revised)
            except SelectionChanged: raise AssertionError('old rule revision incorrectly offered for reconfirmation')
            except ValueError: pass
            else: raise AssertionError('old rule revision passed verification')
        else: raise AssertionError('changed equipment passed confirmation')
    duplicate = dict(ocr, discs=[*samples, dict(samples[0], setKey=en, id='other-copy')],
                     audit=dict(game_total=7, exported=7, delta=0))
    try: verify_selection(request, cfg, selection(duplicate), duplicate)
    except SelectionChanged: raise AssertionError('ambiguous target was offered for reconfirmation')
    except ValueError as exc: assert '1 号位有 2 张盘属性相同' in str(exc)
    else: raise AssertionError('ambiguous equipment passed confirmation')


if __name__ == '__main__':
    parser = argparse.ArgumentParser(); parser.add_argument('--home', required=True, type=Path)
    with TemporaryDirectory() as directory:
        smoke(parser.parse_args().home, Path(directory))

"""Import the local reader's public ZOD export for inventory previews."""
import json
import socket
from contextlib import closing

from .store import inventory, network_agents, observed_agents, save


def reader_process(conn):
    import os
    import psutil
    import win32ts
    from pathlib import Path
    peer, local = conn.getpeername(), conn.getsockname()
    owners = {row.pid for row in psutil.net_connections(kind='tcp4') if row.laddr == peer and row.raddr == local}
    if len(owners) == 1 and None not in owners:
        pid = owners.pop()
        if win32ts.ProcessIdToSessionId(pid) == win32ts.ProcessIdToSessionId(os.getpid()):
            proc = psutil.Process(pid)
            if Path(proc.name()).name.lower() in ('zzzgui.exe', 'zzzcap.exe'):
                return f'{pid}/{proc.create_time()}'
    raise ValueError('请在当前游戏桌面打开网络读取器')


def fetch_export(port=23313, *, identify=reader_process):
    import websocket
    try:
        # A preconnected socket excludes system proxies, redirects and remote destinations.
        with closing(socket.create_connection(('127.0.0.1', port), timeout=5)) as conn:
            identity = identify(conn)
            with closing(websocket.create_connection(f'ws://127.0.0.1:{port}/ws', socket=conn, redirect_limit=0,
                                                      timeout=8, suppress_origin=True)) as ws:
                payload = ws.recv()
    except (OSError, websocket.WebSocketException) as exc:
        raise ValueError('请先打开网络读取器，在登录前开启 Live export。') from exc
    if not isinstance(payload, str) or not payload or len(payload) > 16_000_000:
        raise ValueError('网络读取器还没有可用的库存')
    return identity, json.loads(payload)


def normalize(data):
    from .optimizer import STATS, exported_s_main
    if (not isinstance(data, dict) or (data.get('format'), data.get('version'), data.get('source')) !=
            ('ZOD', 1, 'ZZZ Packet Capture') or not all(isinstance(data.get(k), list) for k in ('characters', 'discs', 'wengines'))):
        raise ValueError('请在网络读取器导出角色、驱动盘和音擎，再登录游戏读取库存。')
    discs = []
    for i, d in enumerate(data['discs']):
        if not isinstance(d, dict) or type(d.get('lock')) is not bool or type(d.get('trash')) is not bool:
            raise ValueError('网络读取器缺少锁定或弃置记录')
        if not isinstance(d.get('location'), str):
            raise ValueError('请在国际服读取器开启驱动盘穿戴信息后刷新')
        if not isinstance(d.get('substats'), list) or not all(isinstance(s, dict) for s in d['substats']):
            raise ValueError('副词条格式有误')
        # The reader pads unused lines to four. Exported upgrades start at zero.
        subs = [s for s in d['substats'] if s != {'key':'', 'upgrades':0}]
        disc = {key:d[key] for key in ('setKey','slotKey','level','mainStatKey','rarity','location','lock','trash')}
        if any(type(s.get('upgrades')) is not int or not 0 <= s['upgrades'] <= 5 for s in subs):
            raise ValueError('副词条强化次数有误')
        # The existing OCR inventory counts the initial roll as one. Normalize on input
        # so switching readers keeps a player's pins on the same fingerprint.
        disc.update(id=f'net-{i}', substats=[dict(key=s['key'], upgrades=s['upgrades']+1) for s in subs], lockState='locked' if d['lock'] else 'unlocked',
                    markState='marked' if d['trash'] else 'unmarked', ownerState='worn' if d['location'] else 'bag')
        main = disc['mainStatKey']
        maximum = STATS.get(main, (None,None,None))[2]
        if (disc['rarity'] == 'S' and maximum and type(d['level']) is int and 0 <= d['level'] <= 15
                and all(STATS.get(s['key'], (None,None))[1] for s in subs)):
            value = exported_s_main(main, d['level'])
            disc['observedValues'] = dict(main={main:value}, subs={s['key']:round(STATS[s['key']][1]*(s['upgrades']+1),4) for s in subs})
        discs.append(disc)
    snapshot = dict(format='ZOD', version=1, dbVersion=2, source='ZZZ Packet Capture', discs=discs,
                    characters=data['characters'], wengines=data['wengines'], networkPreview=True)
    if not all(isinstance(row, dict) and isinstance(row.get('key'), str) for row in snapshot['characters']):
        raise ValueError('网络读取器返回的角色列表有误')
    inventory(snapshot, preview_only=True)
    return snapshot


def capture(root, check_account):
    from .store import read
    identity, exported = fetch_export()
    snapshot = normalize(exported)
    check_account()
    # Called under the plugin maintenance lock. Never reuse a reader opened for
    # one OneDragon account as the source of a different account's preview.
    binding = root.parent/'_reader'/'session.json'
    old = read(binding, {})
    if old.get('reader') == identity and old.get('account') != root.name:
        raise ValueError('切换账号后，请重新打开读取器并登录当前游戏账号')
    save(binding, dict(reader=identity, account=root.name))
    agents = network_agents(root) | observed_agents(snapshot)
    save(root/'network-owned-agents.json', sorted(agents))
    save(root/'network-inventory.json', snapshot)
    return snapshot


def inventory_guard(root):
    from .cn_export import file_stamp
    path = root/'network-inventory.json'
    stamp = file_stamp(path.stat())

    def check():
        if not path.is_file() or file_stamp(path.stat()) != stamp:
            raise ValueError('网络库存已更新，请重新整理')
    return check

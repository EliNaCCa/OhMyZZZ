"""Resolve one fielded lineup and verify physical ownership after every equip."""
from time import time
from .store import config, digest, fingerprint, inventory, roll_base, save
from .stat_reading import checked_values


def target_loadout(cfg, report, snapshot, name):
    profiles = [p for p in cfg['profiles'] if p.get('enabled', True)]
    selected = next((p for p in profiles if p['name'] == name), None)
    if selected is None:
        raise ValueError('出场方案已停用或不存在')
    group = selected.get('simultaneous')
    lineup = [p for p in profiles if group and p.get('simultaneous') == group] if group else [selected]
    discs, counts = inventory(snapshot)
    if report['configuration'] != digest(cfg) or report['inventory'] != digest(snapshot):
        raise ValueError('当前配队或库存已变化，请重新计算')
    by_fp = {fingerprint(d):d for d in discs}
    agents, claimed, targets = set(), set(), []
    for profile in lineup:
        agent = profile.get('agentKey')
        if not agent or agent in agents:
            raise ValueError('同组角色重复或缺少角色对应关系')
        agents.add(agent)
        assignment = report['assignments'].get(profile['name'], {})
        ids = assignment.get('ids', [])
        if len(ids) != 6 or assignment.get('error'):
            raise ValueError(profile['name']+' 缺少完整六盘方案')
        for slot, fp in enumerate(ids, 1):
            if counts[fp] != 1 or fp in claimed:
                raise ValueError('目标盘不唯一或同时出场的方案重盘')
            disc = by_fp[fp]
            if int(disc['slotKey']) != slot or disc.get('trash') or disc.get('markState') != 'unmarked':
                raise ValueError('目标盘号位或弃置状态不适合换装')
            observed = checked_values(disc, roll_base(snapshot))
            if not observed:
                raise ValueError(f'{agent} 的 {slot} 号盘缺少实测词条数值，请用 OhMyZZZ 重新扫描')
            claimed.add(fp)
            targets.append(dict(profile=profile['name'],agent=agent,slot=slot,fp=fp,
                                location=disc.get('location',''),set=disc['setKey'],main=disc['mainStatKey'],
                                observedValues=observed))
    return targets


def apply_loadout(root, cfg, report, snapshot, ui, *, name=None):
    name = name or cfg['fieldProfile']
    targets = target_loadout(cfg, report, snapshot, name)
    receipt = dict(profile=name,configuration=report['configuration'],inventory=report['inventory'],
                   started=time(),status='checking',targets=targets,actions=[],verified=[])
    path = root/'equip-result.json'

    def checkpoint():
        if digest(config(root)) != receipt['configuration']:
            raise ValueError('换装过程中配置已变化')
        ui.guard()

    save(path, receipt)
    try:
        for target in targets:
            checkpoint()
            # Opening the same target on retry observes the current live action button.
            state = ui.inspect(target)
            if state == 'equipped':
                continue
            if state != 'available':
                raise ValueError('当前盘的穿戴状态待核')
            checkpoint()
            action = dict(agent=target['agent'],slot=target['slot'],fp=target['fp'],state='pending')
            receipt['actions'].append(action)
            save(path,receipt)
            ui.equip(target, checkpoint)
            if ui.inspect(target) != 'equipped':
                raise ValueError(f"{target['agent']} 的 {target['slot']} 号盘换装后未能复读")
            action['state'] = 'observed'
            save(path,receipt)
        # Re-open each slot after the last teammate has finished borrowing discs.
        for target in targets:
            checkpoint()
            if ui.inspect(target) != 'equipped':
                raise ValueError(f"最终核对未通过：{target['agent']} 的 {target['slot']} 号盘")
            receipt['verified'].append(target['fp'])
        receipt['status'] = 'verified'
    except Exception as exc:
        receipt.update(status='interrupted', error=str(exc))
        raise
    finally:
        receipt['finished'] = time()
        save(path, receipt)
    return receipt

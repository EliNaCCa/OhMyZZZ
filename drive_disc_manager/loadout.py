"""Resolve one fielded lineup and verify physical ownership after every equip."""
from time import time
from .store import config, digest, fingerprint, inventory, roll_base, save
from .stat_reading import checked_values


def fielded_profiles(cfg, name):
    profiles = [p for p in cfg['profiles'] if p.get('enabled', True)]
    selected = next((p for p in profiles if p['name'] == name), None)
    if selected is None:
        raise ValueError('出场方案已停用或不存在')
    group = selected.get('simultaneous')
    return [p for p in profiles if p.get('simultaneous') == group] if group else [selected]


def verify_bindings(cfg, name, wiki):
    from .roster import scanner_agent_key
    for profile in fielded_profiles(cfg, name):
        actor = (profile.get('request') or {}).get('characterName')
        key = scanner_agent_key(wiki, actor) if isinstance(actor, str) else ''
        if not key or key != profile.get('agentKey'):
            raise ValueError(f"「{profile['name']}」的换装角色绑定待核对，请在「我的配队」更新所选配队。")


def target_loadout(cfg, report, snapshot, name, *, preview_only=False):
    lineup = fielded_profiles(cfg, name)
    discs, counts = inventory(snapshot, preview_only=preview_only)
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
            if disc.get('ownerState') == 'unknown':
                raise ValueError(f'{agent} 的 {slot} 号盘穿戴者待复核，请重新扫描')
            if int(disc['slotKey']) != slot or disc.get('trash') or disc.get('markState') != 'unmarked':
                raise ValueError('目标盘号位或弃置状态不适合换装')
            observed = checked_values(disc, roll_base(snapshot))
            if not observed:
                raise ValueError(f'{agent} 的 {slot} 号盘缺少实测词条数值，请用 OhMyZZZ 重新扫描')
            claimed.add(fp)
            targets.append(dict(profile=profile['name'],agent=agent,slot=slot,fp=fp,
                                location=disc.get('location',''),ownerState=disc.get('ownerState','legacy'),
                                set=disc['setKey'],main=disc['mainStatKey'],
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

    freed_slots = set()
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
            donor = target['location']
            if donor == target['agent']:
                raise ValueError('扫描时已穿戴的盘目前不在位，请重新计算')
            if (donor, target['slot']) in freed_slots:
                # An earlier verified action in this very plan displaced the donor's old disc.
                donor = ''
            if donor:
                checkpoint()
                if ui.inspect(dict(target, agent=donor)) != 'equipped':
                    raise ValueError(f"{donor} 的 {target['slot']} 号位已变化，请重新计算借盘方案")
                checkpoint()
                if ui.inspect(target) != 'available':
                    raise ValueError('返回目标角色后，驱动盘的穿戴状态已变化')
            checkpoint()
            action = dict(agent=target['agent'],slot=target['slot'],fp=target['fp'],donor=donor,state='pending')
            receipt['actions'].append(action)
            save(path,receipt)
            ui.equip(dict(target, location=donor), checkpoint)
            if ui.inspect(target) != 'equipped':
                raise ValueError(f"{target['agent']} 的 {target['slot']} 号盘换装后未能复读")
            action['state'] = 'observed'
            freed_slots.add((target['agent'], target['slot']))
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

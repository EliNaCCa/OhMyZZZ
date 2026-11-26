"""Resolve one fielded lineup and verify physical ownership after every equip."""
from time import time
from .marks import scanned_identities
from .store import config, digest, equipment_names, fingerprint, inventory, roll_base, save
from .stat_reading import checked_values


def fielded_profiles(cfg, name):
    profiles = [p for p in cfg['profiles'] if p.get('enabled', True)]
    selected = next((p for p in profiles if p['name'] == name), None)
    if selected is None:
        raise ValueError('出场方案已停用或不存在')
    group = selected.get('simultaneous')
    return [p for p in profiles if p.get('simultaneous') == group] if group else [selected]


def missing_target_note(cfg, profile):
    group = profile.get('simultaneous')
    if not group:
        return ''
    count = sum(1 for p in cfg['profiles'] if p.get('simultaneous') == group and p.get('needsTarget'))
    return f'还有 {count} 位代理人待选配装目标' if count else ''


def equip_lineup(cfg, name):
    profiles = fielded_profiles(cfg, name)
    note = missing_target_note(cfg, profiles[0])
    if note:
        raise ValueError(note)
    return profiles


def verify_bindings(cfg, name, wiki):
    from .roster import scanner_agent_key
    for profile in equip_lineup(cfg, name):
        actor = (profile.get('request') or {}).get('characterName')
        key = scanner_agent_key(wiki, actor) if isinstance(actor, str) else ''
        if not key or key != profile.get('agentKey'):
            raise ValueError(f"「{profile['name']}」的换装角色绑定待核对，请在「我的配队」更新所选配队。")


def target_loadout(cfg, report, snapshot, name, *, preview_only=False, names=None):
    lineup = equip_lineup(cfg, name)
    discs, _ = inventory(snapshot, preview_only=preview_only)
    if report['configuration'] != digest(cfg) or report['inventory'] != digest(snapshot):
        raise ValueError('当前配队或库存已变化，请重新计算')
    if names is None:
        names = equipment_names({}) | cfg.get('aliases', {})
    keys, counts = scanned_identities(discs, names)
    cn_preview = preview_only and snapshot.get('source') == 'zzz-drive-exporter' and snapshot.get('networkPreview') is True
    by_id = {d['id'] if cn_preview else fingerprint(d):d for d in discs}
    preview_rows = {r['exportUid']:r for r in report.get('discs', []) if r.get('exportUid')} if cn_preview else {}
    reader_owners = report.get('readerOwners', {}) if snapshot.get('networkPreview') is True else {}
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
        for slot, piece in enumerate(ids, 1):
            if piece not in by_id or piece in claimed:
                raise ValueError(f"请核对「{profile['name']}」的 {slot} 号位，目标盘已缺失或与同场方案重盘")
            disc = by_id[piece]
            fp = fingerprint(disc)
            if counts.get(fp, 0) > 1:
                raise ValueError(f"「{profile['name']}」的 {slot} 号位有 {counts[fp]} 张盘属性相同，无法确定要换哪张")
            if not cn_preview and disc.get('ownerState') == 'unknown':
                raise ValueError(f'{agent} 的 {slot} 号盘穿戴者待复核，请重新扫描')
            allowed_marks = ('unmarked', 'unknown') if cn_preview else ('unmarked',)
            if int(disc['slotKey']) != slot or disc.get('trash') or disc.get('markState') not in allowed_marks:
                raise ValueError('目标盘号位或弃置状态不适合换装')
            observed = checked_values(disc, roll_base(snapshot))
            if not observed:
                raise ValueError(f'{agent} 的 {slot} 号盘缺少实测词条数值，请用 OhMyZZZ 重新扫描')
            claimed.add(piece)
            owner = preview_rows.get(piece, disc)
            location = owner.get('location', '')
            # The physical ID identifies a CN selection even if the game writes its
            # set name or optional mainStatValue differently. Values remain checked.
            uid = disc['id'] if cn_preview else disc.get('exportUid')
            targets.append(dict(profile=profile['name'],agent=agent,slot=slot,fp=fp,
                                selectionFp=('cn-uid/'+uid if uid and cfg.get('inventorySource') == 'cn_export'
                                             else fingerprint(dict(disc, setKey=keys[disc['setKey']]))),
                                selectionSet=keys[disc['setKey']],
                                location=reader_owners.get(location, location),ownerState=owner.get('ownerState','legacy'),
                                set=disc['setKey'],main=disc['mainStatKey'],level=disc['level'],rarity=disc['rarity'],
                                observedValues=observed))
    return targets


def apply_loadout(root, cfg, report, snapshot, ui, *, name=None, names=None):
    name = name or cfg['fieldProfile']
    targets = target_loadout(cfg, report, snapshot, name, names=names)
    return apply_checked_targets(root, report, name, targets, ui)


def apply_checked_targets(root, report, name, targets, ui):
    receipt = dict(profile=name,configuration=report['configuration'],inventory=report['inventory'],
                   started=time(),status='checking',targets=targets,actions=[],verified=[])
    path = root/'equip-result.json'

    def checkpoint():
        if digest(config(root)) != receipt['configuration']:
            raise ValueError('换装过程中配置已变化')
        ui.guard()

    save(path, receipt)
    try:
        # Track where the still-needed pieces are worn. Replacing a borrowed disc
        # can swap the displaced piece to the donor instead of sending it to the bag.
        owners = {t['fp']: t['location'] for t in targets}
        pending_worn = {}
        for t in targets:
            if t['location']:
                position = t['location'], t['slot']
                if position in pending_worn:
                    raise ValueError(f"扫描里有两张盘都穿在 {t['location']} 的 {t['slot']} 号位，请重新扫描")
                pending_worn[position] = t
        for target in targets:
            checkpoint()
            # Opening the same target on retry observes the current live action button.
            state = ui.inspect(target)
            dest = target['agent'], target['slot']
            donor = owners[target['fp']]
            pending_worn.pop((donor, target['slot']), None)
            if state == 'equipped':
                continue
            if state != 'available':
                raise ValueError('当前盘的穿戴状态待核')
            if donor == target['agent']:
                raise ValueError('扫描时已穿戴的盘目前不在位，请重新计算')
            displaced = pending_worn.pop(dest, None)
            if displaced:
                checkpoint()
                if ui.inspect(dict(displaced, agent=target['agent'])) != 'equipped':
                    raise ValueError(f"{target['agent']} 的 {target['slot']} 号位已变化，请重新扫描")
            if donor:
                checkpoint()
                if ui.inspect(dict(target, agent=donor)) != 'equipped':
                    raise ValueError(f"{donor} 的 {target['slot']} 号位已变化，请重新计算借盘方案")
            if displaced or donor:
                checkpoint()
                if ui.inspect(target) != 'available':
                    raise ValueError('返回目标角色后，驱动盘的穿戴状态已变化')
            checkpoint()
            action = dict(agent=target['agent'],slot=target['slot'],fp=target['fp'],donor=donor,state='pending')
            receipt['actions'].append(action)
            save(path,receipt)
            ui.equip(dict(target, location=donor), checkpoint)
            checkpoint()
            if ui.inspect(target) != 'equipped':
                raise ValueError(f"{target['agent']} 的 {target['slot']} 号盘换装后未能复读")
            action['state'] = 'observed'
            save(path,receipt)
            if displaced:
                new_owner = ''
                if donor:
                    checkpoint()
                    state = ui.inspect(dict(displaced, agent=donor))
                    if state == 'equipped':
                        new_owner = donor
                        pending_worn[donor, target['slot']] = displaced
                    elif state != 'available':
                        raise ValueError('换下的驱动盘穿戴状态待核，请重新扫描')
                owners[displaced['fp']] = new_owner
                action['displaced'] = dict(fp=displaced['fp'], location=new_owner)
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

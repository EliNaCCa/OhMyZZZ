"""Two teammates need the same slot, with the first disc borrowed from a third agent."""
from pathlib import Path
import sys
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.loadout import apply_loadout
from drive_disc_manager.store import defaults, digest, fingerprint, read, save


def smoke(root, swaps, interrupt=False):
    profiles = [dict(name=agent, agentKey=agent, simultaneous='同场') for agent in ('A', 'C')]
    cfg = dict(defaults(), profiles=profiles, fieldProfile='A')
    save(root/'config.json', cfg)
    pieces = []
    ids = {}
    mains = [('hp', 550), ('atk', 79), ('def', 46), ('hp_', 7.5), ('ice_dmg_', 7.5), ('atk_', 7.5)]
    for agent in ('A', 'B', 'C'):
        for slot, (main, amount) in enumerate(mains, 1):
            if agent == 'B' and slot != 1:
                continue
            disc = dict(id=f'{agent}{slot}', setKey=f'Set{agent}', slotKey=str(slot), level=0, rarity='S',
                        mainStatKey=main, location=agent, ownerState='worn', markState='unmarked', trash=False, lock=False,
                        substats=[dict(key=k, upgrades=0) for k in ('crit_', 'crit_dmg_', 'pen')],
                        observedValues=dict(main={main:amount}, subs={'crit_':2.4,'crit_dmg_':4.8,'pen':9}))
            ids[agent, slot] = fingerprint(disc)
            pieces.append(disc)
    inventory = dict(format='ZOD', dbVersion=2, version=1, discs=pieces, characters=[], wengines=[],
                     audit=dict(game_total=len(pieces), exported=len(pieces), delta=0))
    assignment = {agent: dict(ids=[ids[donor, 1], *(ids[agent, i] for i in range(2, 7))])
                  for agent, donor in [('A','B'), ('C','A')]}
    report = dict(configuration=digest(cfg), inventory=digest(inventory), assignments=assignment)
    worn = ids.copy()
    clicks = []

    class UI:
        def guard(self): pass

        def inspect(self, target):
            return 'equipped' if worn.get((target['agent'], target['slot'])) == target['fp'] else 'available'

        def equip(self, target, checkpoint):
            checkpoint()
            donor = next((agent for (agent, slot), fp in worn.items() if fp == target['fp']), '')
            assert target['location'] == donor, (target['agent'], 'expected live owner', donor, 'got', target['location'])
            position = target['agent'], target['slot']
            old = worn.get(position)
            if donor:
                del worn[donor, target['slot']]
                if swaps and old:
                    worn[donor, target['slot']] = old
            worn[position] = target['fp']
            clicks.append((donor, target['agent']))
            if interrupt and len(clicks) == 1:
                raise ConnectionError('模拟确认换盘后断线')

    ui = UI()
    if interrupt:
        try:
            apply_loadout(root, cfg, report, inventory, ui)
        except ConnectionError:
            log = read(root/'equip-result.json')
            assert log['status'] == 'interrupted' and log['actions'][0]['state'] == 'pending'
        else:
            raise AssertionError('missing interruption')
        # The next task starts with a fresh warehouse scan, including the third agent.
        for disc in pieces:
            owner = next((agent for (agent, _), fp in worn.items() if fp == fingerprint(disc)), '')
            disc.update(location=owner, ownerState='worn' if owner else 'bag')
        report['inventory'] = digest(inventory)
    receipt = apply_loadout(root, cfg, report, inventory, ui)
    assert clicks == [('B','A'), ('B' if swaps else '', 'C')]
    assert len(set(receipt['verified'])) == 12
    assert all(worn[agent, slot] == fp for agent, build in assignment.items() for slot, fp in enumerate(build['ids'], 1))
    assert len(receipt['actions']) == (1 if interrupt else 2)
    if not interrupt:
        assert receipt['actions'][0]['displaced'] == dict(fp=ids['A', 1], location='B' if swaps else '')


if __name__ == '__main__':
    with TemporaryDirectory() as temp:
        for name, swaps, interrupt in [('swap', True, False), ('bag', False, False), ('resume', True, True)]:
            smoke(Path(temp)/name, swaps, interrupt)
    print('PASS same-slot borrowing, swapped or unequipped disc, restart after one confirmed click')

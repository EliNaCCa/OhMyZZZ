"""Compare the CN export with this account's last complete game scan."""
from collections import Counter, defaultdict

from .stat_reading import checked_values
from .store import equipment_key, equipment_keys, inventory, roll_base


def compare_game_scan(exported, scanned, names):
    game_discs, _ = inventory(scanned)
    reverse = equipment_keys(names)

    def group(discs, base):
        groups = defaultdict(list)
        for disc in discs:
            key = (equipment_key(disc['setKey'], names, reverse), str(disc['slotKey']),
                   disc['mainStatKey'], disc['level'], disc['rarity'],
                   tuple(sorted((s['key'], 1 + s['upgrades'] - base) for s in disc['substats'])))
            groups[key].append(disc)
        return groups

    observed = group(game_discs, roll_base(scanned))
    imported = group(exported['discs'], roll_base(exported))
    owner_slots = Counter((d.get('location'), str(d['slotKey'])) for d in game_discs if d.get('ownerState') == 'worn')
    matched = measured = 0
    last_worn = {}
    # An identical stat line can occur on multiple physical pieces. Count the
    # common copies, but attribute an old wearer only for a one-to-one match.
    for key in observed.keys() & imported.keys():
        old, new = observed[key], imported[key]
        matched += min(len(old), len(new))
        if len(old) != 1 or len(new) != 1:
            continue
        values = checked_values(old[0], roll_base(scanned))
        if values is None or values != checked_values(new[0], roll_base(exported)):
            continue
        measured += 1
        owner = old[0].get('location')
        if old[0].get('ownerState') == 'worn' and owner and owner_slots[(owner, str(old[0]['slotKey']))] == 1:
            last_worn[new[0]['id']] = owner
    return dict(gameTotal=len(game_discs), exportTotal=len(exported['discs']), matched=matched,
                measured=measured, lastWorn=last_worn)

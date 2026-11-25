"""One field across three upstream revisions. No game or account data."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import patch

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager import subscriptions as sub
from drive_disc_manager.store import config, read, save


def smoke(root):
    key = 'matrix/SwingJazz/4'
    cfg = config(root)
    cfg['overrides'][key] = dict(mode='only', values=['crit_'])
    save(root/'config.json', cfg)

    def update(values):
        with patch.object(sub, 'sources', return_value=({key:values}, {'Set+Main':'42'}, {key:['Set+Main']})):
            return sub.sync(root)

    def conflict(snapshot):
        return sub.current_conflicts(snapshot, config(root)['overrides']).get(key)

    update(['atk_', 'crit_'])
    one = update(['atk_', 'crit_dmg_'])
    two = update(['crit_dmg_'])
    first = conflict(one)
    latest = conflict(two)
    assert first['active'] == latest['active'] == ['crit_']
    assert latest['old'] == ['atk_', 'crit_'] and latest['remote'] == ['crit_dmg_']
    try:
        sub.resolve_conflict(root, key, first, 'remote')
    except ValueError:
        pass
    else:
        raise AssertionError('stale rule accepted')
    original = (root/'rules.json').read_bytes()
    with patch.object(sub, 'fetch', return_value='Disc 4,ATK,ATK,DEF,CRIT Rate,CRIT DMG,Anomaly Proficiency'), \
         patch.object(sub, 'sources', side_effect=lambda: sub.matrix_fields(sub.fetch('fixture'))):
        try:
            sub.sync(root)
        except ValueError:
            pass
        else:
            raise AssertionError('duplicate header accepted')
    assert (root/'rules.json').read_bytes() == original
    sub.resolve_conflict(root, key, latest, 'local')
    assert conflict(read(root/'rules.json')) is None
    assert conflict(update(['crit_dmg_'])) is None
    three = update(['crit_dmg_', 'anomProf'])
    sub.resolve_conflict(root, key, conflict(three), 'union')
    local = config(root)['overrides'][key]
    assert local['mode'] == 'add' and local['values'] == ['crit_']
    four = update(['anomProf'])
    assert conflict(four) is None and sub.effective(four['fields'][key], local) == ['anomProf', 'crit_']
    update(['anomProf', 'crit_'])
    five = update(['anomProf'])
    assert not sub.can_merge(conflict(five))
    sub.resolve_conflict(root, key, conflict(five), 'remote')
    assert key not in config(root)['overrides'] and conflict(five) is None
    from drive_disc_manager.service import preview, rule_snapshot
    pinned = rule_snapshot(root, refresh=False)
    update(['atk_'])
    save(root/'inventory.json', dict(format='ZOD', version=1, dbVersion=2, characters=[], wengines=[], discs=[],
                                     audit=dict(game_total=0, exported=0, delta=0)))
    assert preview(root, snapshot=pinned)['rules'] == five['version'] != read(root/'rules.json')['version']


if __name__ == '__main__':
    with TemporaryDirectory() as temp:
        smoke(Path(temp))
    print('PASS subscription: three-way decisions, stale edits, acknowledgement, union and failed schema')

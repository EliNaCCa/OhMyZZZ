"""Replay the next farming day's scan against one independent native batch."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.salvage_audit import observe_native, prepare_batch, reconcile
from drive_disc_manager.marks import reconcile_scan
from drive_disc_manager.store import digest, fingerprint, read, save


def disc(label, suit, main, stats, *, marked=False):
    return dict(id=label, setKey=suit, slotKey='4', mainStatKey=main, rarity='S', observedGrade='S', level=0,
                substats=[dict(key=stat, upgrades=1) for stat in stats], observedValues=dict(main={main:7.5}, subs=stats),
                markState='marked' if marked else 'unmarked', trash=marked, lockState='unlocked', lock=False, ownerState='bag')


def warehouse(*discs):
    return dict(format='ZOD', dbVersion=2, source='Zenless Optimizer', version=1, characters=[], wengines=[],
                discs=list(discs), audit=dict(game_total=len(discs), exported=len(discs), delta=0))


def smoke(root):
    old = disc('old', 'SwingJazz', 'atk_', {'crit_':2.4, 'hp':112, 'pen':9}, marked=True)
    kept = disc('keep', 'PolarMetal', 'hp_', {'crit_':2.4, 'atk':19, 'pen':9})
    drop = disc('new', 'AstralVoice', 'hp_', {'atk_':3, 'def':15, 'pen':9})
    before = warehouse(old, kept)
    report = dict(inventory=digest(before), configuration='fixture')
    batch = prepare_batch(before, report, {}, 'A及以下')
    batch.update(account=1, started=10, nativeAtPreview=None)

    def replay(label, *after, state=1):
        account = root/label
        save(account/'preview.json', dict(report, discs=[dict(fp=fingerprint(kept), set=kept['setKey'],
             slot='4', main='hp_', level=0)]))
        save(account/'markers.json', {fingerprint(old): dict(state='already-discarded')})
        save(account/'dismantle-preview.json', batch)
        observe_native(account, 1, dict(account=1, updated=11, status=state), 12)
        fresh = warehouse(*after)
        reconcile(account, fresh)
        result = read(account/'dismantle-result.json')
        reconcile(account, fresh)
        assert read(account/'dismantle-result.json') == result
        return result

    for state in (1, 3):
        result = replay('added'+str(state), dict(kept, id='rescanned-keep'), drop, state=state)
        assert result['status'] == 'reconciled', result['resolution']
        assert result['beforeTotal'] == result['afterTotal'] == 2 and result['newDiscs'] == 1
        assert result['observations'] == {'已不在库存':1} and len(result['entries']) == 1
    lost = replay('lost', drop)
    assert lost['status'] == 'needs_review'
    assert [(e['set'], e['after'], e['count']) for e in lost['stockChanges']] == [
        ('PolarMetal', '清单外少盘', 1), ('AstralVoice', '本次多出', 1)]
    alias = dict(old, id='looks-new', setKey='摇摆爵士', trash=False, markState='unmarked')
    assert replay('lookalike', kept, alias)['status'] == 'needs_review'
    unclear = replay('unclear', kept, dict(drop, observedValues=None))
    assert unclear['status'] == 'needs_review' and unclear['stockChanges'][0]['after'] == '词条数值未读全'
    assert replay('still-marked', old, kept, drop)['status'] == 'needs_review'
    assert replay('duplicate', kept, drop, dict(drop, id='twin'))['status'] == 'needs_review'
    assert replay('older-batch', kept)['status'] == 'reconciled'
    # A later *new* drop with the old physical stats is not the player's undo.
    account = root/'added1'
    later = warehouse(kept, drop, dict(old, id='new-copy', trash=False, markState='unmarked'))
    assert fingerprint(old) not in reconcile_scan(account, later)
    # A mark cleared on a disc which remained in the warehouse still belongs to the player.
    account = root/'still-there'
    replay('still-there', kept, dict(old, trash=False, markState='unmarked'))
    assert fingerprint(old) in reconcile_scan(account, later)
    del batch['entries'][0]['shape']  # Receipts already written by v0.2.0.
    assert replay('legacy-arrival', kept, drop)['status'] == 'needs_review'
    assert replay('legacy-exact', kept)['status'] == 'reconciled'

    # If the host continues into dismantling after a marker interruption, only
    # a mark observed in THIS batch is a known addition to the old marks.
    fresh_scan = warehouse(old, kept, drop)
    kept_fp, drop_fp = fingerprint(kept), fingerprint(drop)
    planned = dict(inventory=digest(fresh_scan), configuration='fixture', discs=[
        dict(fp=fp, decision='弃置候选') for fp in (kept_fp, drop_fp)])
    starting = prepare_batch(fresh_scan, planned, {}, 'A及以下', before_marking=True)
    starting.update(account=1, started=10, nativeAtPreview=None)
    for state, wanted in [('marked','reconciled'), ('pending','needs_review')]:
        account = root/('stopped-'+state)
        save(account/'dismantle-preview.json', starting)
        stamp = dict(batch=starting['batch'], inventory=starting['inventory'], time=11)
        save(account/'markers.json', {kept_fp:dict(stamp, state=state, intended='mark'),
                                      drop_fp:dict(stamp, state='marked', batch='older')})
        observe_native(account, 1, dict(account=1, updated=12, status=1), 13)
        reconcile(account, warehouse(drop))
        result = read(account/'dismantle-result.json')
        assert result['status'] == wanted and {e['fp'] for e in result['entries']} == {fingerprint(old),kept_fp}
        assert (read(account/'markers.json')[kept_fp]['state'] == 'absent-after-rescan') == (state == 'marked')


if __name__ == '__main__':
    with TemporaryDirectory() as temp:
        smoke(Path(temp))
    print('PASS next-day salvage reconciliation with one new disc, surviving gear and an interrupted run')

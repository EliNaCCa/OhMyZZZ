"""Small simulated inventory through the CN plan, bag verification, and one tier receipt."""
import sys
from collections import Counter
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock

sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(Path(sys.argv[1])/'src')]
from drive_disc_manager.cn_export import normalize, normalize_disc
from drive_disc_manager.inventory_comparison import execution_plan, verify_cn_inventory
from drive_disc_manager.owned_stock import EMBRYO, stock_count
from drive_disc_manager.planner import plan
from drive_disc_manager.reserve_policy import max_useful_rolls, quota_impact
from drive_disc_manager.store import config, digest, read
from drive_disc_manager.upgrade import RECEIPT, eligible, execute


def disc(uid, level, stats, suit='Common'):
    return dict(id=str(uid), setKey=suit, slotKey='4', mainStatKey='atk_', rarity='S', level=level,
                substats=[dict(key=k, upgrades=v) for k, v in stats.items()], lock=False, trash=False, location='')


def sample(d):
    return dict(d, ownerState='bag', markState='unmarked', lockState='unlocked', observedGrade='S', trash=False)


with TemporaryDirectory() as temp:
    root = Path(temp)/'1'
    cfg = dict(config(root), reserve=2, futureAgents=['Future'], rotateSpareLocks=True, autoUpgrade=True)
    fields = {'matrix/'+s+'/4':['atk_'] for s in ('Common', 'Spare')}
    for who in ('Alpha', 'Bravo', 'Future'):
        fields.update({f'build/{who}/sets4':['Common'], f'build/{who}/slot4':['ATK%'], f'build/{who}/subs':['CR, CD']})
    rules = dict(version='local', fields=fields)
    owned = ['Alpha', 'Bravo', 'Alpha']
    assert [stock_count(2,n) for n in (1,2,3)] == [2,3,4] and stock_count(1,2)==2
    assert max_useful_rolls(0, dict(crit_=1,hp=1,def_=1), ['crit_']) == 5
    assert max_useful_rolls(0, dict(crit_=1,hp=1,def_=1), ['crit_', 'crit_dmg_']) == 6
    assert max_useful_rolls(9, dict(crit_=2,crit_dmg_=1,hp=2,def_=2), ['crit_', 'crit_dmg_']) == 5
    items = [disc(i, 15, dict(crit_=cr,crit_dmg_=cd,hp=hp,def_=d))
             for i, (cr,cd,hp,d) in enumerate([(4,3,1,1),(3,3,1,2),(3,2,2,2),(2,2,2,3)],1)]
    items += [disc(5,0,dict(crit_=1,hp=1,def_=1)), disc(6,0,dict(hp=1,def_=1,pen=1,atk=1)),
              disc(7,0,dict(crit_=1,crit_dmg_=1,def_=1,hp=1),'Spare'),
              disc(8,0,dict(crit_=1,crit_dmg_=1,hp=1),'Spare'),
              disc(9,3,dict(crit_=1,crit_dmg_=1,hp=1,def_=1),'Spare')]
    z = normalize(dict(format='ZOOD',version=1,source='zzz-drive-exporter',discs=items))
    remote = plan(z,cfg,rules,{},known_owned=owned)
    def by_id(report): return {r['id']:r for r in report['discs']}
    rs = by_id(remote)
    assert rs['4']['checkForDiscard'] and [rs[str(i)]['decision'] for i in (1,2,3)] == ['保留']*3
    assert rs['7']['reasons'] == [EMBRYO] and not rs['7'].get('upgrade')
    assert all(rs[i]['checkForDiscard'] for i in ('6','8','9'))
    assert set(rs['5']['ownedUses']) == {'alpha','bravo'} and rs['5']['upgrade']['target'] == 3
    impact = quota_impact(remote, {'set':'Spare'}, dict(cfg, reserve=1), {})
    assert impact['before'] == 1 and impact['after'] == 0
    samples = [sample(d) for d in z['discs']]
    samples[-1].update(trash=True, markState='marked')
    view = verify_cn_inventory(z, dict(source='Zenless Optimizer',audit=dict(game_total=len(items))), samples, {})
    inspected = plan(view,dict(cfg,reserve=0),rules,{},known_owned=owned)
    inspected['configuration'] = digest(cfg)
    local = execution_plan(remote, inspected, view, cfg=cfg, names={})
    rows = by_id(local)
    assert rows['4']['decision'] == '弃置候选' and rows['7']['reasons'] == [EMBRYO] and rows['9']['decision'] == '已弃置'
    assert eligible(rows['5']) and rows['5']['upgrade']['target'] == 3
    from drive_disc_manager.live import Marker
    marker = Marker.__new__(Marker)
    marker.rows = {r['fp']:r for r in rows.values()}
    marker.seen_counts = Counter(r['fp'] for r in rows.values())
    assert set(marker.phase_targets('mark')) == {rows[k]['fp'] for k in ('4','6','8')}
    assert rows['7']['fp'] in marker.phase_targets('lock')
    for new, keep in [('crit_dmg_', True), ('atk', False)]:
        ui = Mock(); ui.preview.return_value = (0,3)
        ui.confirm.side_effect = lambda current, goal, before_click: before_click()
        ui.read_after.return_value = sample(normalize_disc(disc(5,3,dict(crit_=1,hp=1,def_=1,**{new:1}))))
        execute(root,ui,rows['5'],samples[4],view,cfg,{})
        receipt = read(root/RECEIPT)
        assert bool(receipt['nextUpgrade']) == keep and bool(receipt['outgrown']) != keep, receipt['note']
    print('PASS owned roster quotas, one-third +0 pool, strict five-roll ceiling, CN bag plan, +3 continue/discard')

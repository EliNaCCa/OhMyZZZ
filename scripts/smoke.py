"""One small offline fixture. No game window, account or server required."""
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from drive_disc_manager.store import defaults, import_inventory, save
from drive_disc_manager.service import preview
from drive_disc_manager.optimizer import prefer
from drive_disc_manager.subscriptions import merge

with TemporaryDirectory() as tmp:
    root=Path(tmp)
    def sample(n,up):
        return dict(id=str(n),setKey='SwingJazz',slotKey='4',level=0,rarity='S',mainStatKey='def_',
                    substats=[dict(key=k,upgrades=u) for k,u in [('crit_',up),('atk',0),('hp',0)]],lock=False,trash=False,location='')
    discs=[sample(1,0),sample(2,0)]
    discs[1]['substats'][0]['key']='crit_dmg_'
    z=dict(format='ZOD',dbVersion=2,version=1,discs=discs,characters=[],wengines=[],audit=dict(game_total=2,exported=2,delta=0))
    src=root/'sample.json'; save(src,z); import_inventory(root,src)
    cfg=defaults(); cfg.update(auto_sync=False,reserve=1); save(root/'config.json',cfg)
    key='matrix/SwingJazz/4'; fields={key:['atk_'],key+'/known':['atk_','def_']}
    save(root/'rules.json',merge({},fields,{},{}))
    assert preview(root)['counts']=={'保留':1,'弃置候选':1}
    from drive_disc_manager.marks import mark_action
    assert mark_action('marked',None,False)=='already-discarded'
    assert mark_action('unmarked',None,True)=='mark'
    assert mark_action('unmarked','marked',True)=='manual-keep'
    z['discs'][0]['trash']=True
    save(src,z); import_inventory(root,src)
    assert preview(root)['counts']=={'保留':1,'已弃置':1}
    # Same two discs: a recommended main stat can be capped, and an old mark
    # remains part of the live batch even when the disc ranks as a spare.
    from drive_disc_manager.planner import plan
    from drive_disc_manager.salvage_audit import prepare_batch, record, reconcile
    from drive_disc_manager.store import read
    from drive_disc_manager.scan_marks import observed_grade
    z['discs'][0].update(trash=False,markState='unmarked',observedGrade='S')
    z['discs'][1].update(markState='unmarked',observedGrade='S')
    rules={'fields':{key:['def_'],key+'/known':['def_']},'version':'smoke'}
    assert plan(z,cfg,rules,{})['counts']=={'保留':2}
    limited=dict(cfg,trimRecommended=True)
    report=plan(z,limited,rules,{})
    assert report['counts']=={'保留':1,'弃置候选':1} and report['reserveCoverage'][0]['kept']==1
    z['discs'][0].update(trash=True,markState='marked')
    report=plan(z,limited,rules,{})
    batch=prepare_batch(z,report,{},'A及以下')
    assert [r['source'] for r in batch['entries']]==['游戏原有弃置']
    assert batch['status']=='not_submitted'
    # A v0.1.9 execution that stopped near confirmation must be reconciled first.
    record(root,dict(batch,status='pending'))
    reconcile(root,z)
    assert read(root/'dismantle-result.json')['status']=='needs_review'
    after=dict(z,discs=z['discs'][1:],audit=dict(game_total=1,exported=1,delta=0))
    reconcile(root,after)
    assert read(root/'dismantle-result.json')['observations']=={'已不在库存':1}
    assert not prepare_batch(after,plan(after,limited,rules,{}),{},'A及以下')['entries']
    assert observed_grade([dict(text='等级0/15',confidence=0.95)])=='S'
    cases=[dict(ids=['a'],critRate=97.59,outputScore=100),dict(ids=['b'],critRate=97.6,outputScore=99.5)]
    assert prefer(cases,0.5,set())['ids']==['b']
    assert prefer(cases,0,set())['ids']==['a']
    overflow=dict(ids=['c'],critRate=104.8,outputScore=100.01)
    selected=prefer(cases+[overflow],0.5,set())
    assert selected['ids']==['c'] and selected['effectiveCritRate']==100
    assert prefer([dict(cases[0],critRate=98),cases[1]],0.5,set())['ids']==['a']
    from drive_disc_manager.allocation import assign_group
    members = [(dict(name='主C'),set()),(dict(name='队友'),set())]
    options = {'主C':[dict(ids=['1','2','3','4','5','6'],critRate=100,outputScore=100),
                      dict(ids=['7','2','3','4','5','6'],critRate=100,outputScore=99.8)],
               '队友':[dict(ids=['1','8','9','10','11','12'],score=20,reason='职业排名')]}
    def shortlist(url,profile,pool,current):
        available = {item['id'] for item in pool}
        return [c for c in options[profile['name']] if available.issuperset(c['ids'])]
    pool = [dict(id=str(i)) for i in range(1,13)]
    group = assign_group(members,pool,'',shortlist)
    assert group[0][1]['ids'][0]=='7' and group[0][1]['relativeLossPct']==0.2
    assert set(group[0][1]['ids']).isdisjoint(group[1][1]['ids'])
    # One required swap goes through just before the (simulated) disconnect.
    from drive_disc_manager.loadout import apply_loadout
    from drive_disc_manager.store import digest, fingerprint
    cfg=defaults(); cfg.update(fieldProfile='冰队',run_scanner=True,profiles=[dict(name='冰队',agentKey='Ellen')])
    save(root/'config.json',cfg)
    six=[dict(sample(i,0),slotKey=str(i),level=15,markState='unmarked') for i in range(1,7)]
    from drive_disc_manager.optimizer import as_disc
    from drive_disc_manager.store import roll_base
    host_disc=dict(six[3],substats=[dict(key=k,upgrades=1) for k in ('crit_','crit_dmg_','atk')])
    parsed=as_disc(host_disc,{'SwingJazz':'摇摆爵士'},roll_base({'source':'Zenless Optimizer'}))
    stats={s['type']:s['value'] for s in parsed['subStats']}
    assert stats['CRIT_RATE']==2.4 and stats['CRIT_DMG']==4.8
    snapshot=dict(z,discs=six,audit=dict(game_total=6,exported=6,delta=0))
    ids=[fingerprint(d) for d in six]
    report=dict(configuration=digest(cfg),inventory=digest(snapshot),assignments={'冰队':dict(ids=ids)})
    class SimulatedEquipment:
        def __init__(self):
            self.worn=set(ids[1:]); self.clicks=0
        def guard(self): pass
        def inspect(self,target): return 'equipped' if target['fp'] in self.worn else 'available'
        def equip(self,target,checkpoint):
            checkpoint(); self.clicks+=1; self.worn.add(target['fp'])
            raise ConnectionError('模拟换装后断线')
    ui=SimulatedEquipment()
    try:
        apply_loadout(root,cfg,report,snapshot,ui)
    except ConnectionError:
        assert read(root/'equip-result.json')['actions'][0]['state']=='pending'
    else: raise AssertionError('未观察到模拟中断')
    result=apply_loadout(root,cfg,report,snapshot,ui)
    assert result['verified']==ids and result['actions']==[] and ui.clicks==1
print('smoke OK: two discs, one reserve, team preference')

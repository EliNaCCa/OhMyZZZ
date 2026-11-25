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
    # Four-line double crit at birth vs a three-line piece that revealed its fourth at +3.
    crit_pair = [dict(key=k, upgrades=0) for k in ('crit_', 'crit_dmg_', 'atk', 'hp')]
    seeds = [dict(sample(1,0),substats=crit_pair), dict(sample(2,0),level=3,substats=crit_pair),
             dict(sample(3,0),substats=[dict(key=k, upgrades=0) for k in ('atk', 'hp', 'futureStat')])]
    three = dict(z,discs=seeds,audit=dict(game_total=3,exported=3,delta=0))
    empty_quota = dict(cfg,reserve=0,trimRecommended=True)
    assert [r['decision'] for r in plan(three,empty_quota,rules,{})['discs']] == ['保留','弃置候选','待复核']
    uses = [dict(purpose='偏暴伤',count=5,stats=['crit_dmg_'])]
    coverage = plan(three,dict(empty_quota,reserve_rules=uses),rules,{})['reserveCoverage'][0]
    assert coverage['purposes'][-1]['covered']==1 and coverage['kept']==1
    cases=[dict(ids=['a'],critRate=97.59,outputScore=100),dict(ids=['b'],critRate=97.6,outputScore=99.5)]
    assert prefer(cases,0.5,set())['ids']==['b']
    assert prefer(cases,0,set())['ids']==['a']
    overflow=dict(ids=['c'],critRate=104.8,outputScore=100.01)
    selected=prefer(cases+[overflow],0.5,set())
    assert selected['ids']==['c'] and selected['effectiveCritRate']==100
    assert prefer([dict(cases[0],critRate=98),cases[1]],0.5,set())['ids']==['a']
    # A build missing from the other window's shortlist must still be rescored
    # with exactly the same six ids. The imported bonus is active only in burst.
    from drive_disc_manager.optimizer import candidate_pool
    slots=[dict(id=str(i),slot=i,mainStat=dict(type='HP_FLAT',value=112),setName='s',subStats=[]) for i in range(1,7)]
    alternate=dict(slots[3],id='alt'); bonus=dict(source='队友',stat='CRIT_RATE',value=15)
    profile=dict(request=dict(characterName='主C',roleSystem='ATTACK',baseCritRate=88,combatExtraStats={'CRIT_RATE':15}),
                 windows=[dict(name='常态',weight=20,buffs=[dict(bonus,mode='inactive')]),
                          dict(name='爆发',weight=80,buffs=[dict(bonus,mode='included')])],mainWindow='爆发')
    def single_top(url,args):
        extra=args['combatExtraStats']['CRIT_RATE']; assert extra in (0,15)
        fixed=len(args['discs'])==6
        choice=args['discs'] if fixed else [alternate if d['slot']==4 and extra else d for d in slots]
        alt=any(d['id']=='alt' for d in choice)
        attack=(120 if extra else 80) if alt else 100
        rate=min(100,88+extra)
        return {'results':[dict(discs=choice,outputScore=round(attack*(1+rate*50/10000),4),
                                critRate=rate,critDmg=50,combatFinalAttack=attack,setSummary={'s':6})]}
    by_slot={c['ids'][3]:c for c in candidate_pool('',profile,slots+[alternate],set(),single_top)}
    assert by_slot['alt']['outputScore']==167.04 and by_slot['4']['outputScore']==148.8
    assert [w['critRate'] for w in by_slot['alt']['windows']]==[88,100]
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
    for d in six:
        d['observedValues'] = {'main':{'def_':48}, 'subs':{'crit_':2.4, 'atk':19, 'hp':112}}
    from drive_disc_manager.optimizer import as_disc
    from drive_disc_manager.store import roll_base
    host_disc=dict(six[3],substats=[dict(key=k,upgrades=1) for k in ('crit_','crit_dmg_','atk')])
    host_disc.pop('observedValues')  # legacy +15 export for preview
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

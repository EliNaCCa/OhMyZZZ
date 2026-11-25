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
    cases=[dict(ids=['a'],critRate=98,outputScore=100),dict(ids=['b'],critRate=100,outputScore=99.5)]
    assert prefer(cases,0.5,set())['ids']==['b']
    assert prefer(cases,0,set())['ids']==['a']
print('smoke OK: two discs, one reserve, team preference')

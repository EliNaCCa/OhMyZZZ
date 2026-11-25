from .reserve_policy import quota, SELECTORS
from .optimizer import STATS
from .store import defaults
from .optimizer import number


def validate(value):
    cfg={**defaults(),**value}
    quota(cfg['reserve'])
    for key in ('reserve_rules','profiles','pins','roster'):
        if not isinstance(cfg[key],list): raise ValueError(key+' 应为数组')
    for key in ('protect15','trimRecommended','auto_sync','run_scanner','afterEarly','applyMarkers'):
        if type(cfg[key]) is not bool: raise ValueError(key+' 应为布尔值')
    for r in cfg['reserve_rules']:
        quota(r['count'])
        if 'trimRecommended' in r and type(r['trimRecommended']) is not bool:
            raise ValueError('推荐盘参与限额应为布尔值')
        if any(k in r and not isinstance(r[k], (str, int)) for k in SELECTORS):
            raise ValueError('备用规则的套装、号位、主属性无效')
        if 'stats' in r and (not isinstance(r['stats'],list) or any(stat not in STATS for stat in r['stats'])):
            raise ValueError('备用用途包含未知副词条')
    names=set()
    for p in cfg['profiles']:
        if not p['name'] or p['name'] in names or not isinstance(p['request'],dict):
            raise ValueError('配队名重复或参数缺失')
        names.add(p['name'])
        if not 0<=number(p.get('tolerancePct',0.5))<=100: raise ValueError('配队输出容差无效')
    for item in cfg['overrides'].values():
        if item['mode'] not in ('inherit','add','only') or not isinstance(item['values'],list) or not all(isinstance(s,str) for s in item['values']):
            raise ValueError('字段覆盖无效')
    return cfg

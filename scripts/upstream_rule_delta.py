"""Compare literals on four review fixtures without importing upstream code."""
import ast
import sys
from pathlib import Path


SAMPLES = [
    ('啄木鸟防御胚子', 'WoodpeckerElectro','4','def_',0,('crit_', 'atk','hp')),
    ('极地暴伤胚子', 'PolarMetal','4','crit_dmg_',0,('crit_', 'pen','hp')),
    ('摇摆爵士穿透盘', 'SwingJazz','5','pen_',9,('def','hp','atk','crit_')),
    ('震星冲击盘', 'ShockstarDisco','6','impact',12,('hp','hp_','def','def_')),
]
CONSTANTS = {'DEFAULT_SET_TYPE','DEFAULT_DEAD_MATRIX','R2_LOW_SUBS','R2_MIN_LEVEL','R2_MIN_SUBS'}


def rules(path):
    module = ast.parse(path.read_text('utf-8-sig'))
    values = {}
    for node in module.body:
        if isinstance(node,ast.Assign) and len(node.targets)==1 and isinstance(node.targets[0],ast.Name):
            name=node.targets[0].id
            if name in CONSTANTS:
                values[name]=ast.literal_eval(node.value)
    if set(values)!=CONSTANTS:
        raise ValueError('上游规则字段已变化')
    methods = [ast.dump(node) for node in module.body if isinstance(node,ast.ClassDef) and node.name=='DiscRules']
    return values,methods


def classify(config, disc):
    _, name, slot, main, level, subs = disc
    kind=config['DEFAULT_SET_TYPE'].get(name)
    if kind is None:
        return '待核'
    dead=main in config['DEFAULT_DEAD_MATRIX'][kind].get(slot,())
    depleted=(level>=config['R2_MIN_LEVEL'] and len(subs)>=config['R2_MIN_SUBS']
              and set(subs).issubset(config['R2_LOW_SUBS']))
    return '弃置候选' if (dead and level<15) or (not dead and depleted) else '保留'


def describe(before, after):
    print('### 上游规则变化 · 四张固定样盘')
    try:
        old, old_methods = rules(before)
        new, new_methods = rules(after)
        states=[(disc[0],classify(old,disc),classify(new,disc)) for disc in SAMPLES]
    except (OSError, SyntaxError, TypeError, ValueError, KeyError) as exc:
        print('样盘比较待人工核对。规则解析失败。',type(exc).__name__)
        return
    print('\n| 样盘 | 旧矩阵 | 新矩阵 |\n| --- | --- | --- |')
    for name, a, b in states:
        print(f'| {name} | {a} | {b} |')
    added=[name for name,a,b in states if b=='弃置候选' and a!=b]
    print('\n这批样盘新增弃置候选数为',len(added),'。','、'.join(added))
    if old_methods!=new_methods:
        print('\n上游判定代码有改动，请结合实现核对矩阵结果。')
    print('\n样盘使用上游默认矩阵。同步后请审核并移植适用改动。')


if __name__=='__main__':
    describe(*map(Path,sys.argv[1:]))

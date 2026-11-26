"""Encounter-specific team suggestions inside the host settings page."""
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, PushButton

from .challenge import MODES, account_pool, encounter_shortlist, reward_lineup
from .disc_detail_page import fill, table
from .store import read
from time import time


class ChallengePage(QWidget):
    def __init__(self):
        super().__init__()
        self.root = None
        self.shown_mode = None
        self.conditions = {}
        box = QVBoxLayout(self)
        top = QHBoxLayout()
        box.addLayout(top)
        self.mode = ComboBox()
        self.mode.addItems(list(MODES))
        top.addWidget(self.mode)
        self.goal = BodyLabel()
        top.addWidget(self.goal)
        top.addStretch()
        self.read_agents = PushButton('读取角色展柜')
        top.addWidget(self.read_agents)
        self.roster = BodyLabel()
        self.roster.setWordWrap(True)
        box.addWidget(self.roster)
        grid = QGridLayout()
        box.addLayout(grid)
        for column, title in enumerate(('关卡', '弱点', '抗性', '环境增益适用职业')):
            grid.addWidget(BodyLabel(title), 0, column)
        self.controls = []
        elements = ['冰属性','火属性','电属性','以太属性','物理属性','风属性','流明属性']
        for i in range(3):
            grid.addWidget(BodyLabel(f'第 {i+1} 路'), i+1, 0)
            controls = []
            for j, values in enumerate([elements, elements, ['强攻','异常','命破','锋御','击破','支援','防护']], 1):
                combo = ComboBox()
                combo.addItems(['未指定', *values])
                grid.addWidget(combo, i+1, j)
                controls.append(combo)
                combo.currentIndexChanged.connect(self.clear_result)
            self.controls.append(controls)
        self.compare = PushButton('比较分队')
        self.save_lineup = PushButton('保存到我的配队')
        actions = QHBoxLayout()
        box.addLayout(actions)
        actions.addWidget(self.compare)
        actions.addWidget(self.save_lineup)
        actions.addStretch()
        self.lineup = []
        self.save_lineup.setEnabled(False)
        self.status = BodyLabel()
        self.status.setWordWrap(True)
        box.addWidget(self.status)
        self.result = table(['关卡', '代理人', '入选原因'])
        box.addWidget(self.result, 1)
        self.compare.clicked.connect(self.calculate)
        self.mode.currentTextChanged.connect(self.switch_mode)

    def clear_result(self, *_):
        self.lineup = []
        self.save_lineup.setEnabled(False)
        self.status.clear()
        self.result.setRowCount(0)

    def switch_mode(self, *_):
        if self.shown_mode is not None:
            self.conditions[self.shown_mode] = [[combo.currentText() for combo in row] for row in self.controls]
        mode = self.mode.currentText()
        self.rotation = read(self.root/'challenge-current.json', {}).get(mode, {}) if self.root else {}
        if not self.rotation.get('startsAt', float('inf')) <= time() < self.rotation.get('endsAt', 0):
            self.rotation = {}
        default, rating = MODES[mode]
        needed = self.rotation.get('needed', default)
        self.goal.setText(f'{needed} 队{rating}' + (' · '+self.rotation['period'] if self.rotation else ''))
        stages = self.rotation.get('stages', [])
        self.shown_mode = (mode, self.rotation.get('period'))
        draft = self.conditions.get(self.shown_mode)
        for i, controls in enumerate(self.controls):
            for j,(key, combo) in enumerate(zip(('weak','resist','buffRole'), controls)):
                value = draft[i][j] if draft else (stages[i].get(key) if i < len(stages) else '')
                combo.setCurrentIndex(max(0,combo.findText(value or '未指定')))
                combo.setEnabled(not stages or i < len(stages))
        self.clear_result()

    def present(self, root, snapshot, wiki):
        switched = root != self.root
        self.root = root
        preferences = read(root/'challenge-preferences.json', {})
        self.pool = account_pool(root, snapshot, wiki)
        self.familiar = preferences.get('parties', [])
        def label(item):
            name, stats = item
            if 'mindscape' in stats and 'engineRank' in stats:
                return f"{name}（{stats['mindscape']}+{stats['engineRank']}）"
            return name
        self.roster.setText('角色池　' + ('、'.join(map(label,self.pool.items())) or '等待读取角色'))
        details = []
        for name, stats in self.pool.items():
            fields = [('level', '级'), ('mindscape', '影'), ('engineRank', '星音擎')]
            parts = [f'{stats[key]}{unit}' for key, unit in fields if key in stats]
            if parts:
                details.append(name + '　' + ' · '.join(parts))
        self.roster.setToolTip('\n'.join(details))
        self.compare.setEnabled(bool(self.pool))
        if switched:
            self.shown_mode = None
            self.conditions.clear()
            self.mode.setCurrentIndex(0)
        self.switch_mode()

    def calculate(self):
        self.clear_result()
        mode = self.mode.currentText()
        needed = self.rotation.get('needed', MODES[mode][0])
        originals = self.rotation.get('stages', [])
        stages = []
        for i, controls in enumerate(self.controls[:len(originals) or 3]):
            conditions = {key:(combo.currentText() if combo.currentIndex()>0 else '') for key,combo in
                          zip(('weak','resist','buffRole'), controls)}
            if conditions['weak'] and conditions['weak'] == conditions['resist']:
                self.clear_result()
                self.status.setText(f'第 {i+1} 路的弱点和抗性相同，请核对。')
                return
            stages.append(conditions)
        unchanged = originals and all(all(c.get(k,'') == v for k,v in s.items())
                                      for c,s in zip(originals, stages, strict=True))
        if unchanged and any(stage.get('results') for stage in originals):
            ranked = reward_lineup(dict(self.rotation, mode=mode), self.pool)
            if ranked['lineup']:
                by_stage = {stage['name']: conditions for stage, conditions in zip(originals, stages)}
                self.lineup = [dict(stage=r['stage'], team=list(r['team']), conditions=by_stage[r['stage']]) for r in ranked['lineup']]
                self.save_lineup.setEnabled(True)
                self.status.setText(f"本期已有成绩的队伍中，{ranked['cleared']} 路达标")
                fill(self.result, [(r['stage'], ' / '.join(r['team']), f"{r['value']}{r['unit']}（目标 {r['target']}{r['unit']}）")
                                   for r in ranked['lineup']])
                return
        if not any(any(s.values()) for s in stages):
            self.status.setText('选择本期敌人的弱点、抗性或环境增益，再比较分队。')
            return
        rows, clipped = encounter_shortlist(self.pool, stages, needed, self.familiar)
        self.lineup = [dict(stage=originals[r['stage']]['name'] if originals else f"第 {r['stage']+1} 路",
                            team=list(r['team']), conditions=stages[r['stage']]) for r in rows]
        self.save_lineup.setEnabled(bool(rows))
        fill(self.result, [(originals[r['stage']]['name'] if originals else f"第 {r['stage']+1} 路", ' / '.join(r['team']),
                            '、'.join(note for yes,note in [(r['weak'],'命中弱点'),(r['buff'],'能用环境增益'),(r['resisted'],'敌人有抗性')] if yes) or '补齐分队') for r in rows])
        self.status.setText(('按关卡条件初选 · 达标成绩待核' + (' · 已达搜索上限' if clipped else '')) if rows
                            else f'需要至少 {needed} 路关卡条件和 {3*needed} 位可分队的代理人。')

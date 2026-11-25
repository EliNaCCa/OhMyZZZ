"""Select reserve scopes without requiring a scanned inventory."""
from PySide6.QtCore import Signal
from PySide6.QtWidgets import QComboBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget

from .disc_detail_page import fill, table
from .main_rule_editor import NAMES
from .purpose_editor import ranking_label
from .quota_editor import amount
from .reserve_policy import DEFAULT_STATS, matching_rules, normalized_rule, rule_selectors
from .store import equipment_keys
from .subscriptions import SLOT_MAIN


STAT_NAMES = {**NAMES, 'hp':'生命值', 'atk':'攻击力', 'def':'防御力'}


def scope_name(scope, names):
    suit = scope.get('set')
    slot = scope.get('slot')
    stat = scope.get('main')
    parts = [names.get(suit, suit) if suit else '所有套装']
    if slot:
        parts.append(slot+' 号位')
    if stat:
        parts.append(STAT_NAMES.get(stat, stat))
    return ' · '.join(parts)


class ReservePresets(QWidget):
    edit = Signal(object, str)

    def __init__(self):
        super().__init__()
        self.rules = []
        self.cfg = None
        self.names = {}
        layout = QVBoxLayout(self)
        form = QFormLayout()
        layout.addLayout(form)
        row = QHBoxLayout()
        self.suit = QComboBox()
        self.slot = QComboBox()
        self.main = QComboBox()
        self.slot.addItem('所有号位', '')
        for index in range(1, 7):
            self.slot.addItem(f'{index} 号位', str(index))
        for widget in (self.suit, self.slot, self.main):
            row.addWidget(widget)
        form.addRow('适用范围', row)
        self.current = QLabel()
        self.current.setWordWrap(True)
        layout.addWidget(self.current)
        self.change = QPushButton('设置此范围的备用数量')
        layout.addWidget(self.change)
        self.grid = table(['已设范围', '用途', '同类名额', '推荐盘策略', '排序依据'])
        layout.addWidget(self.grid)
        self.suit.currentIndexChanged.connect(self.describe)
        self.slot.currentIndexChanged.connect(self.show_stats)
        self.main.currentIndexChanged.connect(self.describe)
        self.change.clicked.connect(lambda: self.edit.emit(self.scope(), '通用'))
        self.grid.cellDoubleClicked.connect(self.edit_row)
        self.show_stats()

    def scope(self):
        return rule_selectors({'set':self.suit.currentData(), 'slot':self.slot.currentData(), 'main':self.main.currentData()})

    def show_stats(self, *_):
        original = self.main.currentData()
        stats = SLOT_MAIN.get(self.slot.currentData(), STAT_NAMES)
        self.main.blockSignals(True)
        try:
            self.main.clear()
            self.main.addItem('所有主属性', '')
            for stat in stats:
                self.main.addItem(STAT_NAMES.get(stat, stat), stat)
            self.main.setCurrentIndex(max(0, self.main.findData(original)))
        finally:
            self.main.blockSignals(False)
        self.describe()

    def present(self, cfg, names, fields, account):
        switched = getattr(self, 'account', None) != account
        self.account = account
        self.names = names
        reverse = equipment_keys(names)
        self.rules = [normalized_rule(rule, reverse) for rule in cfg['reserve_rules']]
        self.cfg = dict(cfg, reserve_rules=self.rules)
        sets = set(names)
        for key in fields:
            parts = key.split('/')
            if len(parts) == 3 and parts[0] == 'matrix':
                sets.add(parts[1])
        for rule in self.rules:
            suit = rule_selectors(rule).get('set')
            if suit:
                sets.add(suit)
        previous = '' if switched else self.suit.currentData()
        self.suit.blockSignals(True)
        try:
            self.suit.clear()
            self.suit.addItem('所有套装', '')
            for key in sorted(sets, key=lambda item:(names.get(item,item),item)):
                self.suit.addItem(names.get(key, key), key)
            self.suit.setCurrentIndex(max(0, self.suit.findData(previous)))
        finally:
            self.suit.blockSignals(False)
        if switched:
            self.slot.setCurrentIndex(0)
            self.main.setCurrentIndex(0)
        self.describe()
        def row(rule):
            mode = '继承' if 'trimRecommended' not in rule else '按名额筛选' if rule['trimRecommended'] else '全部保留'
            purpose = rule.get('purpose') or '通用'
            return (scope_name(rule_selectors(rule), names), purpose, amount(rule['count']), mode,
                    ranking_label(purpose, rule.get('stats', DEFAULT_STATS)))
        fill(self.grid, [row(rule) for rule in self.rules])

    def describe(self, *_):
        if self.cfg is None:
            return
        limits, trim = matching_rules(self.scope(), self.cfg)
        uses = '、'.join(purpose+' '+amount(count) for purpose, (_, count, _) in limits.items())
        self.current.setText('每种同类盘分别计数 · '+uses+'　｜　推荐盘'+('按名额筛选' if trim else '全部保留'))

    def edit_row(self, index, _column):
        if 0 <= index < len(self.rules):
            rule = self.rules[index]
            self.edit.emit(rule_selectors(rule), rule.get('purpose') or '通用')

"""Edit a single set/slot/main-stat reserve policy inside OneDragon."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QLabel,
                               QRadioButton, QSpinBox, QVBoxLayout)

from .reserve_policy import DEFAULT_STATS, SELECTORS, exact_purpose, matching_rules, normalized_rule


def amount(n):
    return '不限' if n is None else f'{n} 张'


class QuotaEditor(QDialog):
    def __init__(self, parent, group, cfg, reverse, title):
        super().__init__(parent)
        self.group, self.cfg, self.reverse = group, cfg, reverse
        self.normalized = [normalized_rule(rule, reverse) for rule in cfg['reserve_rules']]
        self.limits, _ = matching_rules(group, dict(cfg, reserve_rules=self.normalized))
        self.setWindowTitle('同类备用数量')
        box = QVBoxLayout(self)
        box.addWidget(QLabel(title))
        form = QFormLayout()
        box.addLayout(form)
        self.purpose = QComboBox()
        self.purpose.addItems(list(dict.fromkeys([*self.limits, '偏暴击', '偏暴伤'])))
        form.addRow('用途', self.purpose)
        self.inherit = QRadioButton()
        self.custom = QRadioButton('为此类盘单独设置')
        box.addWidget(self.inherit)
        box.addWidget(self.custom)
        settings = QFormLayout()
        box.addLayout(settings)
        self.count = QSpinBox()
        self.count.setRange(-1, 9999)
        self.count.setSpecialValueText('不限')
        self.count.setSuffix(' 张')
        settings.addRow('额外保留', self.count)
        self.trim = QComboBox()
        self.trim.addItems(['沿用当前策略','推荐盘全部保留','推荐盘也按同类名额筛选'])
        self.trim.setToolTip('适用于此套装、号位和主属性下的所有用途。')
        settings.addRow('此类别的推荐盘', self.trim)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.custom.toggled.connect(self.count.setEnabled)
        self.custom.toggled.connect(self.trim.setEnabled)
        self.purpose.currentTextChanged.connect(self.show_purpose)
        self.show_purpose(self.purpose.currentText())

    def show_purpose(self, purpose):
        matching = [r for r in self.normalized if exact_purpose(r, self.group, purpose, {})]
        broader = [r for r in self.normalized if not exact_purpose(r, self.group, purpose, {})]
        inherited, trim = matching_rules(self.group, dict(self.cfg, reserve_rules=broader))
        limit = inherited.get(purpose)
        text = '此用途未设名额'
        if limit:
            source = '全局' if limit[0] == -1 else '上级规则'
            text = source + ' ' + amount(limit[1])
        self.inherit.setText('继承 · '+text)
        current = self.limits.get(purpose, (None, 0, None))[1]
        self.count.setMaximum(max(9999, current or 0))
        self.count.setValue(-1 if current is None else current)
        policy = next((r['trimRecommended'] for r in reversed(matching) if 'trimRecommended' in r), None)
        self.trim.setItemText(0, '继承 · '+('按名额筛选' if trim else '推荐盘全部保留'))
        self.trim.setCurrentIndex(0 if policy is None else 1 + int(policy))
        self.custom.setChecked(bool(matching))
        self.inherit.setChecked(not matching)
        self.count.setEnabled(bool(matching))
        self.trim.setEnabled(bool(matching))

    def result_rules(self):
        purpose = self.purpose.currentText()
        original = self.cfg['reserve_rules']
        positions = [i for i, r in enumerate(original) if exact_purpose(r, self.group, purpose, self.reverse)]
        keep = [r for i, r in enumerate(original) if i not in positions]
        if self.inherit.isChecked():
            return keep, '继承 · '+purpose
        count = self.count.value()
        rule = {k:self.group[k] for k in SELECTORS}
        rule.update(purpose=purpose, count=None if count < 0 else count)
        stats = self.limits[purpose][2] if purpose in self.limits else {'偏暴击':['crit_'], '偏暴伤':['crit_dmg_']}[purpose]
        if stats is not DEFAULT_STATS:
            rule['stats'] = list(stats)
        if self.trim.currentIndex():
            rule['trimRecommended'] = self.trim.currentIndex() == 2
        # Keep the original rule's order when it is edited. Equal-specificity
        # policies from other purposes retain their configured precedence.
        index = positions[-1] - len(positions) + 1 if positions else len(keep)
        keep.insert(index, rule)
        return keep, purpose+' · 同类额外保留 '+amount(rule['count'])

"""Edit a single set/slot/main-stat reserve policy inside OneDragon."""
from PySide6.QtWidgets import (QComboBox, QDialog, QDialogButtonBox, QFormLayout, QHBoxLayout, QLabel,
                               QPushButton, QRadioButton, QSpinBox, QVBoxLayout)

from .purpose_editor import PurposeEditor, ranking_label
from .reserve_policy import DEFAULT_STATS, PURPOSE_STATS, exact_purpose, matching_rules, normalized_rule, rule_selectors


def amount(n):
    return '不限' if n is None else f'{n} 张'


class QuotaEditor(QDialog):
    def __init__(self, parent, group, cfg, reverse, title, *, report=None):
        super().__init__(parent)
        self.group, self.cfg, self.reverse = group, cfg, reverse
        self.report = report
        self.purpose_stats = PURPOSE_STATS.copy()
        self.draft_rules = list(cfg['reserve_rules'])
        self.displayed_purpose = None
        self.changes = {}
        self.normalized = [normalized_rule(rule, reverse) for rule in cfg['reserve_rules']]
        self.limits, _ = matching_rules(group, dict(cfg, reserve_rules=self.normalized))
        for purpose, (_, _, stats) in self.limits.items():
            self.purpose_stats.setdefault(purpose, stats)
        self.setWindowTitle('同类备用数量')
        box = QVBoxLayout(self)
        box.addWidget(QLabel(title))
        form = QFormLayout()
        box.addLayout(form)
        self.purpose = QComboBox()
        self.purpose.addItems(list(dict.fromkeys([*self.limits, *self.purpose_stats])))
        actions = QHBoxLayout()
        actions.addWidget(self.purpose)
        add = QPushButton('添加用途'); actions.addWidget(add)
        self.edit_stats = QPushButton('编辑副词条'); actions.addWidget(self.edit_stats)
        add.clicked.connect(lambda:self.edit_purpose(new=True))
        self.edit_stats.clicked.connect(lambda:self.edit_purpose(new=False))
        form.addRow('用途', actions)
        self.stat_note = QLabel(); self.stat_note.setWordWrap(True)
        form.addRow('排序依据', self.stat_note)
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
        settings.addRow('基础名额' if cfg.get('includeOwned', True) else '额外保留', self.count)
        self.trim = QComboBox()
        self.trim.addItems(['沿用当前策略','推荐盘全部保留','推荐盘也按同类名额筛选'])
        self.trim.setToolTip('适用于此套装、号位和主属性下的所有用途。')
        settings.addRow('此类别的推荐盘', self.trim)
        if cfg.get('includeOwned', True):
            settings.setRowVisible(self.trim, False)
            settings.addRow(QLabel('每多一位适用代理人，增加 50% 基础名额。'))
        self.try_button = QPushButton('预览这次调整')
        self.impact = QLabel()
        self.impact.setWordWrap(True)
        box.addWidget(self.try_button)
        box.addWidget(self.impact)
        self.try_button.setEnabled(report is not None)
        self.try_button.clicked.connect(self.preview_impact)
        if report is None:
            self.impact.setText('导入完整库存并使用当前配置计算后，可预览保留张数。')
        else:
            self.custom.toggled.connect(self.impact.clear)
            self.count.valueChanged.connect(self.impact.clear)
            self.trim.currentIndexChanged.connect(self.impact.clear)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        box.addWidget(buttons)
        self.custom.toggled.connect(self.count.setEnabled)
        self.custom.toggled.connect(self.trim.setEnabled)
        self.custom.toggled.connect(self.show_stats)
        self.purpose.currentTextChanged.connect(self.show_purpose)
        self.show_purpose(self.purpose.currentText())

    def show_purpose(self, purpose):
        self.store_edits()
        self.displayed_purpose = purpose
        self.normalized = [normalized_rule(rule, self.reverse) for rule in self.draft_rules]
        self.limits, _ = matching_rules(self.group, dict(self.cfg, reserve_rules=self.normalized))
        matching = [r for r in self.normalized if exact_purpose(r, self.group, purpose, {})]
        broader = [r for r in self.normalized if not exact_purpose(r, self.group, purpose, {})]
        inherited, trim = matching_rules(self.group, dict(self.cfg, reserve_rules=broader))
        limit = inherited.get(purpose)
        text = '此用途未设名额'
        if limit:
            source = '全局' if limit[0] == -1 else '上级规则'
            text = source + ' ' + amount(limit[1])
        self.inherit.setText('继承 · '+text)
        self.inherited_stats = limit[2] if limit else None
        current = self.limits.get(purpose, (None, 0, None))[1]
        self.stats = self.limits[purpose][2] if purpose in self.limits else self.purpose_stats[purpose]
        self.count.setMaximum(max(9999, current or 0))
        self.count.setValue(-1 if current is None else current)
        policy = next((r['trimRecommended'] for r in reversed(matching) if 'trimRecommended' in r), None)
        self.trim.setItemText(0, '继承 · '+('按名额筛选' if trim else '推荐盘全部保留'))
        self.trim.setCurrentIndex(0 if policy is None else 1 + int(policy))
        self.custom.setChecked(bool(matching))
        self.inherit.setChecked(not matching)
        self.count.setEnabled(bool(matching))
        self.trim.setEnabled(bool(matching))
        self.show_stats()
        self.initial = self.controls()

    def controls(self):
        return self.custom.isChecked(), self.count.value(), self.trim.currentIndex(), self.stats is DEFAULT_STATS, tuple(self.stats)

    def show_stats(self, *_):
        stats = self.stats if self.custom.isChecked() else self.inherited_stats
        self.stat_note.setText('此用途未设名额' if stats is None else ranking_label(self.displayed_purpose, stats))

    def edit_purpose(self, *, new):
        dialog = PurposeEditor(self,[self.purpose.itemText(i) for i in range(self.purpose.count())],
                               purpose='' if new else self.displayed_purpose, stats=[] if new else self.stats)
        if not dialog.exec(): return
        purpose, stats = dialog.values()
        if new:
            self.purpose_stats[purpose] = stats
            self.purpose.addItem(purpose)
            self.purpose.setCurrentText(purpose)
            self.count.setValue(1)
        self.custom.setChecked(True)
        self.stats = stats
        self.show_stats()
        if self.report is not None:
            self.impact.clear()

    def store_edits(self):
        purpose = self.displayed_purpose
        if purpose is None or self.controls() == self.initial:
            return
        original = self.draft_rules
        positions = [i for i, r in enumerate(original) if exact_purpose(r, self.group, purpose, self.reverse)]
        keep = [r for i, r in enumerate(original) if i not in positions]
        if self.inherit.isChecked():
            self.draft_rules = keep
            self.changes[purpose] = '继承 · '+purpose
            self.initial = self.controls()
            return
        count = self.count.value()
        rule = rule_selectors(self.group)
        rule.update(purpose=purpose, count=None if count < 0 else count)
        if self.stats is not DEFAULT_STATS:
            rule['stats'] = list(self.stats)
        if self.trim.currentIndex():
            rule['trimRecommended'] = self.trim.currentIndex() == 2
        # Keep the original rule's order when it is edited. Equal-specificity
        # policies from other purposes retain their configured precedence.
        index = positions[-1] - len(positions) + 1 if positions else len(keep)
        keep.insert(index, rule)
        self.draft_rules = keep
        self.changes[purpose] = purpose+' · 同类额外保留 '+amount(rule['count'])
        self.initial = self.controls()

    def result_rules(self):
        self.store_edits()
        return self.draft_rules, '、'.join(self.changes.values())

    def preview_impact(self):
        from .reserve_policy import quota_impact
        rules, _ = self.result_rules()
        result = quota_impact(self.report,self.group,dict(self.cfg,reserve_rules=rules),self.reverse)
        if result is None:
            self.impact.setText('请先在仓库预览重新计算一次，以载入备用名额的计算依据。')
        else:
            summary = f"共 {result['total']} 张，保留 {result['before']} → {result['after']} 张，其中备用 {result['spares']} 张"
            if result['potential']:
                summary += f"，另留潜力盘 {result['potential']} 张"
            summary += '。'
            if result['newGameChecks']:
                summary += f"有 {result['newGameChecks']} 张需要进游戏核对后再标记弃置。"
            if result['newCandidates']:
                summary += f"新增弃置候选 {result['newCandidates']} 张。"
            uses = '、'.join(f'{name} {count} 张' for name, count in result['byPurpose'].items())
            if result['shared']:
                uses += f"（{result['shared']} 张同时入选多个用途）"
            self.impact.setText('\n'.join(filter(None, (summary, uses))))

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (QAbstractItemView, QHBoxLayout, QLabel, QPushButton, QTableWidget,
                               QTableWidgetItem, QTabWidget, QVBoxLayout, QWidget)

from .disc_trace import teams_for_disc
from .stat_reading import display_values


def label():
    widget = QLabel()
    widget.setTextFormat(Qt.TextFormat.PlainText)
    widget.setWordWrap(True)
    widget.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
    return widget


def table(headers):
    widget = QTableWidget(0, len(headers))
    widget.setHorizontalHeaderLabels(headers)
    widget.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
    widget.setSelectionMode(QAbstractItemView.SelectionMode.SingleSelection)
    widget.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
    widget.horizontalHeader().setStretchLastSection(True)
    return widget


def fill(widget, rows):
    widget.clearSelection()
    widget.setRowCount(0)
    for index, row in enumerate(rows):
        widget.insertRow(index)
        for column, text in enumerate(row):
            item = QTableWidgetItem(str(text)); item.setToolTip(str(text))
            widget.setItem(index, column, item)
    widget.resizeColumnsToContents()
    if rows:
        widget.selectRow(0)


def values(items):
    if items is None:
        return '字段缺失'
    return '、'.join(items) or '空'


class DiscDetails(QWidget):
    back = Signal()
    rule = Signal(str)
    team = Signal(str)

    def __init__(self):
        super().__init__()
        outer = QVBoxLayout(self)
        bar = QHBoxLayout(); outer.addLayout(bar)
        close = QPushButton('返回仓库'); bar.addWidget(close); close.clicked.connect(self.back.emit)
        self.title = label(); bar.addWidget(self.title, 1)
        self.stats = label(); outer.addWidget(self.stats)
        self.tabs = QTabWidget(); outer.addWidget(self.tabs)
        page = QWidget(); box = QVBoxLayout(page); self.tabs.addTab(page,'规则来源')
        self.rules = table(['角色 / 套装','字段','来源页签','对这张盘的判断']); box.addWidget(self.rules)
        self.field_value = label(); box.addWidget(self.field_value)
        self.rule_button = QPushButton('在规则订阅中查看'); box.addWidget(self.rule_button)
        self.rule_button.clicked.connect(self.open_rule)
        self.rules.itemSelectionChanged.connect(self.select_rule)
        page = QWidget(); box = QVBoxLayout(page); self.tabs.addTab(page,'配队与备用')
        self.teams = table(['方案','角色','同时出场组','选择依据']); box.addWidget(self.teams)
        self.team_button = QPushButton('在我的配队中查看'); box.addWidget(self.team_button)
        self.team_button.clicked.connect(self.open_team)
        self.teams.itemSelectionChanged.connect(self.select_team)
        self.spares = label(); box.addWidget(self.spares)
        from PySide6.QtWidgets import QPlainTextEdit
        self.summary = QPlainTextEdit(); self.summary.setReadOnly(True); self.tabs.addTab(self.summary,'判定明细')
        self.version = label(); outer.addWidget(self.version)

    def present(self, row, report, *, set_name, coverage, rule_keys, team_names, probe=False, stale=False):
        self.book = report.get('traceRules', {})
        self.uses = row.get('ruleUses', [])
        self.empty_hint = ('旧预览中没有逐字段来源，可返回仓库重新计算。' if 'traceRules' not in report else
                           '游戏已标记弃置，按现有弃置标记处理。' if row['marked'] else '这张盘没有对应的订阅字段。')
        self.rule_keys, self.team_names = set(rule_keys), set(team_names)
        title = f"{set_name} · {row['slot']} 号位 +{row['level']}　{row['decision']}"
        self.title.setText(title)
        self.stats.setText(display_values(row.get('observedValues')))
        # Keep recommendation evidence distinct from the final quota decision.
        summary = []
        reasons = row['reasons']
        if reasons:
            summary.append('保留依据　'+'、'.join(reasons) if not row['marked'] else '弃置标记　'+'、'.join(reasons))
        summary.extend(row.get('explanation', []))
        if row['review']:
            summary.append('待核　'+'、'.join(row['review']))
        if probe:
            summary.append('配队占用和备用名额需要完整库存。')
        self.summary.setPlainText(chr(10).join(summary))
        rule_rows = []
        for key, agent, outcome in self.uses:
            entry = self.book.get(key, {})
            rule_rows.append((agent or set_name, key, '、'.join(entry.get('tabs', [])) or '未记录页签', outcome))
        fill(self.rules,rule_rows); self.select_rule()
        teams = teams_for_disc(report, row)
        fill(self.teams, [(name, team.get('agent') or '—', team.get('group') or '可借盘', result.get('reason',''))
                          for name, team, result in teams]); self.select_team()
        lines = []
        if coverage:
            lines.append('订阅推荐盘参与备用排序' if coverage['trimRecommended'] else '订阅推荐盘直接保留')
            for use in coverage['purposes']:
                count = '不限' if use['count'] is None else str(use['count'])
                picked = ' · 这张已入选' if '备用 · '+use['purpose'] in reasons else ''
                lines.append(f"{use['purpose']} 名额 {count}，同类入选 {use['covered']} 张{picked}")
        if not teams and not probe:
            lines.append('本轮没有配队占用这张盘')
        if probe:
            lines.append('完整库存可用于计算配队占用和实际备用张数。')
        self.spares.setText('　｜　'.join(lines))
        rev = (report.get('rules') or '')[:12] or '无'
        scope = '单盘实测 · 当前规则' if probe else '库存预览 · 计算时规则'
        self.version.setText(f'{scope} {rev}' + ('　｜　配置或规则已有更新' if stale else ''))
        self.tabs.setCurrentIndex(0 if self.uses else 2)

    def select_rule(self):
        index = self.rules.currentRow()
        key = self.uses[index][0] if 0 <= index < len(self.uses) else None
        entry = self.book.get(key)
        self.rule_button.setEnabled(key in self.rule_keys)
        if not entry:
            self.field_value.setText(self.empty_hint)
            return
        mode = {'inherit':'继承上游', 'add':'额外接受', 'only':'只接受'}[entry['mode']]
        lines = ['上游　'+values(entry['remote']), '个人　'+mode+(' '+values(entry['local']) if entry['mode']!='inherit' else ''),
                 '生效　'+values(entry['active'])]
        if entry['conflict']:
            lines.insert(0,'上游变动前　'+values(entry['old']))
            lines.append('本轮按个人规则处理冲突')
        self.field_value.setText('　｜　'.join(lines))

    def open_rule(self):
        index = self.rules.currentRow()
        if 0 <= index < len(self.uses):
            self.rule.emit(self.uses[index][0])

    def select_team(self):
        item = self.teams.item(self.teams.currentRow(),0)
        self.team_button.setEnabled(bool(item and item.text() in self.team_names))

    def open_team(self):
        item = self.teams.item(self.teams.currentRow(),0)
        if item:
            self.team.emit(item.text())

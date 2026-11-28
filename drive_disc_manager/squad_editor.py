"""Edit the selected profile's party inside the OneDragon settings page."""
from PySide6.QtCore import Signal, Qt
from PySide6.QtWidgets import QGridLayout, QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, DoubleSpinBox, PrimaryPushButton, PushButton, SpinBox, StrongBodyLabel

from .combat_windows import DIRECT_ROLES
from .optimizer import DEFAULT_TOLERANCE_PCT, output_tolerance


class SquadEditor(QWidget):
    saved = Signal()
    canceled = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        box = QVBoxLayout(self)
        self.heading = StrongBodyLabel()
        box.addWidget(self.heading)
        grid = QGridLayout()
        box.addLayout(grid)
        grid.addWidget(BodyLabel('代理人'), 0, 1)
        grid.addWidget(BodyLabel('影画'), 0, 2)
        self.lead = BodyLabel()
        self.actors = [self.lead, ComboBox(), ComboBox()]
        self.ranks = []
        for i, (label, actor) in enumerate(zip(('配装角色', '队友一', '队友二'), self.actors), 1):
            rank = SpinBox()
            rank.setRange(0, 6)
            rank.setSuffix(' 影')
            self.ranks.append(rank)
            grid.addWidget(BodyLabel(label), i, 0)
            grid.addWidget(actor, i, 1)
            grid.addWidget(rank, i, 2)
            if i > 1:
                def change_actor(index, spin=rank, combo=actor):
                    spin.setEnabled(index > 0)
                    spin.setValue(self.account_rank(combo.currentText()) if index > 0 else 0)
                actor.currentIndexChanged.connect(change_actor)
        grid.setColumnStretch(3, 1)
        self.tolerance_label = BodyLabel('为满暴最多接受的输出差距')
        self.tolerance = DoubleSpinBox()
        self.tolerance.setDecimals(4)
        self.tolerance.setRange(0, 100)
        self.tolerance.setSingleStep(0.1)
        self.tolerance.setSuffix('%')
        self.tolerance.setAccessibleName(self.tolerance_label.text())
        grid.addWidget(self.tolerance_label, 4, 0, 1, 2)
        grid.addWidget(self.tolerance, 4, 2)
        bar = QHBoxLayout()
        box.addLayout(bar)
        for title, button_type, signal in [('保存', PrimaryPushButton, self.saved), ('取消', PushButton, self.canceled)]:
            button = button_type(title)
            button.clicked.connect(lambda checked=False, target=signal: target.emit())
            bar.addWidget(button)
        bar.addStretch(1)
        box.setAlignment(Qt.AlignmentFlag.AlignTop)

    def present(self, profile, choices, current, *, canonical, ranks):
        self.account_rank = lambda name: ranks.get(canonical(name), 0)
        self.heading.setText(profile['name'] + ' · 队伍成员')
        self.lead.setText(current)
        direct = profile['request'].get('roleSystem', '').upper() in DIRECT_ROLES
        self.tolerance_label.setVisible(direct)
        self.tolerance.setVisible(direct)
        if direct:
            self.tolerance.setValue(float(output_tolerance(profile.get('tolerancePct', DEFAULT_TOLERANCE_PCT))))
        self.ranks[0].setValue(profile.get('mindscape', self.account_rank(current)))
        members = profile.get('squad', [])
        by_agent = {canonical(name): name for name in choices}
        # Keep saved names verbatim: window buffs refer to these labels.
        by_agent.update((canonical(m['agent']), m['agent']) for m in members)
        by_agent.pop(canonical(current), None)
        names = sorted(by_agent.values())
        for index, actor in enumerate(self.actors[1:]):
            member = members[index] if index < len(members) else {}
            actor.clear()
            actor.addItem('空位')
            actor.addItems(names)
            actor.setCurrentIndex(max(0, actor.findText(member.get('agent', ''))))
            self.ranks[index+1].setValue(member.get('mindscape', self.account_rank(actor.currentText())))
            self.ranks[index+1].setEnabled(actor.currentIndex() > 0)

    def values(self):
        values = dict(mindscape=self.ranks[0].value(), squad=[
            dict(agent=actor.currentText(), mindscape=rank.value())
            for actor, rank in zip(self.actors[1:], self.ranks[1:]) if actor.currentIndex() > 0])
        if not self.tolerance.isHidden():
            values['tolerancePct'] = self.tolerance.value()
        return values

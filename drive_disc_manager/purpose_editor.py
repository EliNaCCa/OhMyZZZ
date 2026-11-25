from PySide6.QtWidgets import (QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QGridLayout,
                               QLabel, QLineEdit, QVBoxLayout)

from .reserve_policy import DEFAULT_STATS


SUBSTAT_NAMES = {
    'crit_':'暴击率', 'crit_dmg_':'暴击伤害', 'anomProf':'异常精通', 'pen':'穿透值',
    'atk_':'攻击力百分比', 'atk':'攻击力', 'hp_':'生命值百分比', 'hp':'生命值',
    'def_':'防御力百分比', 'def':'防御力',
}


def ranking_label(purpose, stats):
    if purpose == '通用' and stats is DEFAULT_STATS:
        return '优先匹配角色的有效副词条'
    return '、'.join(SUBSTAT_NAMES.get(stat, stat) for stat in stats)


class PurposeEditor(QDialog):
    def __init__(self, parent, names, *, purpose='', stats=()):
        super().__init__(parent)
        self.existing = set(names)
        self.original = purpose
        self.setWindowTitle('编辑备用用途' if purpose else '添加备用用途')
        layout = QVBoxLayout(self)
        form = QFormLayout()
        self.name = QLineEdit(purpose)
        self.name.setReadOnly(bool(purpose))
        self.name.setPlaceholderText('例如「异常输出」')
        form.addRow('用途名称', self.name)
        layout.addLayout(form)
        self.by_role = QCheckBox('优先匹配角色的有效副词条')
        self.by_role.setChecked(purpose == '通用' and stats is DEFAULT_STATS)
        self.by_role.setVisible(purpose == '通用')
        layout.addWidget(self.by_role)
        layout.addWidget(QLabel('自定义排序使用勾选副词条的总档数。'))
        grid = QGridLayout()
        self.checks = {}
        for index, (key,title) in enumerate(SUBSTAT_NAMES.items()):
            check = QCheckBox(title)
            check.setChecked(key in stats)
            check.setDisabled(self.by_role.isChecked())
            self.by_role.toggled.connect(check.setDisabled)
            grid.addWidget(check,index//2,index%2)
            self.checks[key] = check
        layout.addLayout(grid)
        self.note = QLabel()
        layout.addWidget(self.note)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        self.save_button = buttons.button(QDialogButtonBox.StandardButton.Save)
        layout.addWidget(buttons)
        self.name.textChanged.connect(self.validate)
        self.by_role.toggled.connect(self.validate)
        for check in self.checks.values():
            check.toggled.connect(self.validate)
        self.validate()

    def values(self):
        if self.original == '通用' and self.by_role.isChecked():
            return self.original, DEFAULT_STATS
        return self.name.text().strip(), [key for key,check in self.checks.items() if check.isChecked()]

    def validate(self, *_):
        name, stats = self.values()
        problem = ''
        if not name:
            problem = '请填写用途名称'
        elif name != self.original and name in self.existing:
            problem = '此范围已有同名用途'
        elif not stats:
            problem = '请选择至少一个副词条'
        self.note.setText(problem)
        self.save_button.setEnabled(not problem)

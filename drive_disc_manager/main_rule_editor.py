"""Edit a single Set+Main subscription field in the host's settings page."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout
from .subscriptions import SLOT_EXTRA
from .stat_reading import MAIN_STAT_NAMES as NAMES


class MainRuleEditor(QDialog):
    def __init__(self, parent, title, slot, mode, remote, selected, *, role_field=None):
        super().__init__(parent)
        self.setWindowTitle(title)
        outer = QVBoxLayout(self)
        note = '勾选额外接受的词条，上游推荐也会生效。' if mode=='add' else '此字段只接受勾选的词条。'
        outer.addWidget(QLabel(note))
        names = NAMES
        if role_field:
            from .roster import stat_parts
            original = QLabel('上游原文 · '+(' ｜ '.join(remote) or '空'))
            original.setTextFormat(Qt.TextFormat.PlainText)
            original.setWordWrap(True)
            outer.addWidget(original)
            remote, remote_unknown = stat_parts(role_field, remote)
            selected, selected_unknown = stat_parts(role_field, selected)
            remote = sorted(remote | remote_unknown)
            selected = sorted(selected | selected_unknown)
        if role_field == 'subs':
            from .purpose_editor import SUBSTAT_NAMES
            names = SUBSTAT_NAMES
            options = list(names)
        else:
            options = [*SLOT_EXTRA[slot], 'atk_', 'hp_', 'def_']
        grid = QGridLayout(); outer.addLayout(grid)
        self.checks = {}
        keys = dict.fromkeys([*options, *remote, *selected])
        for index, key in enumerate(keys):
            check = QCheckBox(names.get(key, key) + ('（上游推荐）' if key in remote else ''))
            check.setToolTip(key); check.setChecked(key in selected)
            grid.addWidget(check,index//2,index%2)
            self.checks[key] = check
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self):
        return [k for k, check in self.checks.items() if check.isChecked()]

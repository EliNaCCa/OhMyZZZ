"""Edit a single Set+Main subscription field in the host's settings page."""
from PySide6.QtWidgets import QCheckBox, QDialog, QDialogButtonBox, QGridLayout, QLabel, QVBoxLayout


NAMES = {'hp_':'生命值百分比', 'atk_':'攻击力百分比', 'def_':'防御力百分比',
         'crit_':'暴击率', 'crit_dmg_':'暴击伤害', 'anomProf':'异常精通', 'pen_':'穿透率',
         'impact':'冲击力', 'energyRegen_':'能量自动回复', 'anomMas_':'异常掌控',
         'physical_dmg_':'物理伤害', 'fire_dmg_':'火属性伤害', 'ice_dmg_':'冰属性伤害',
         'electric_dmg_':'电属性伤害', 'ether_dmg_':'以太伤害', 'wind_dmg_':'风属性伤害',
         'lumiflux_dmg_':'流光伤害'}
SLOTS = {'4':('crit_', 'crit_dmg_', 'anomProf'),
         '5':tuple(k for k in NAMES if k.endswith('_dmg_') and k != 'crit_dmg_') + ('pen_',),
         '6':('impact','energyRegen_','anomMas_')}


class MainRuleEditor(QDialog):
    def __init__(self, parent, title, slot, mode, remote, selected):
        super().__init__(parent)
        self.setWindowTitle(title)
        outer = QVBoxLayout(self)
        note = '勾选要额外接受的主属性，上游的推荐也会生效。' if mode=='add' else '勾选此套装和号位要接受的主属性。'
        outer.addWidget(QLabel(note))
        grid = QGridLayout(); outer.addLayout(grid)
        self.checks = {}
        keys = dict.fromkeys([*SLOTS[slot], 'atk_', 'hp_', 'def_', *remote, *selected])
        for index, key in enumerate(keys):
            check = QCheckBox(NAMES.get(key, key) + ('（上游推荐）' if key in remote else ''))
            check.setToolTip(key); check.setChecked(key in selected)
            grid.addWidget(check,index//2,index%2)
            self.checks[key] = check
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept); buttons.rejected.connect(self.reject)
        outer.addWidget(buttons)

    def values(self):
        return [k for k, check in self.checks.items() if check.isChecked()]

"""Compare one recorded disc with the disc worn in the same slot."""
from collections import defaultdict
from datetime import datetime

from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QHBoxLayout, QVBoxLayout, QWidget
from qfluentwidgets import BodyLabel, ComboBox, PushButton

from .disc_detail_page import fill, table
from .stat_reading import checked_values, format_stats
from .store import digest, read, roll_base, snapshot_path


def measured(disc, base, exported):
    if not exported and (disc.get('observedGrade') not in ('S', 'A', 'B') or disc['observedGrade'] != disc.get('rarity')):
        return None
    try:
        values = checked_values(disc, base)
    except (KeyError, TypeError, ValueError):
        return None
    return values


def stat_change(old, new):
    before = old['main'] | old['subs']
    after = new['main'] | new['subs']
    return {key:delta for key in sorted(before.keys() | after.keys())
            if (delta := round(after.get(key, 0) - before.get(key, 0), 4))}


class DiscComparison(QWidget):
    def __init__(self, back):
        super().__init__()
        self.equipped, self.bag = defaultdict(list), defaultdict(list)
        self.candidates, self.baseline = [], None
        layout = QVBoxLayout(self)
        bar = QHBoxLayout(); layout.addLayout(bar)
        button = PushButton('返回仓库'); button.clicked.connect(back); bar.addWidget(button)
        self.agent = ComboBox(); bar.addWidget(self.agent)
        self.slot = ComboBox(); bar.addWidget(self.slot)
        self.slot.addItems([f'{slot} 号位' for slot in range(1, 7)])
        self.slot.setCurrentIndex(3)
        self.stamp = BodyLabel(); bar.addStretch(); bar.addWidget(self.stamp)
        self.worn = BodyLabel(); self.worn.setWordWrap(True); layout.addWidget(self.worn)
        self.discs = table(['可替换的盘', '等级', '主属性', '副属性'])
        layout.addWidget(self.discs, 1)
        self.change = BodyLabel(); self.change.setWordWrap(True); layout.addWidget(self.change)
        self.agent.currentIndexChanged.connect(self.select_slot)
        self.slot.currentIndexChanged.connect(self.select_slot)
        self.discs.itemSelectionChanged.connect(self.select_disc)

    def present(self, root, agents, sets, cfg, report):
        selected = self.agent.currentData() if root == getattr(self, 'root', None) else None
        self.root, self.sets = root, sets
        self.equipped, self.bag = defaultdict(list), defaultdict(list)
        paths = [path for path in (root/'inventory.json', root/'scan-observations.json') if path.is_file()]
        canonical = snapshot_path(root, cfg)
        exported = cfg.get('inventorySource') in ('network', 'cn_export') and canonical.is_file()
        self.stamp.clear()
        if exported or paths:
            path = canonical if exported else max(paths, key=lambda p:p.stat().st_mtime_ns)
            data = read(path)
            partial = path.name == 'scan-observations.json'
            rows, base = (data['rows'], 1) if partial else (data['discs'], roll_base(data))
            date = datetime.fromtimestamp(data['at'] if partial else data.get('observedAt', path.stat().st_mtime))
            source = '导出库存' if exported else '扫描记录'
            self.stamp.setText(f'{source}　{date:%m-%d %H:%M}')
            plans = {}
            if exported and report and report.get('inventory') == digest(data):
                plans = {r['id']:r for r in report.get('discs', [])}
            for disc in rows:
                slot = str(disc.get('slotKey'))
                row = plans.get(disc.get('id'), {})
                owner = row.get('location') or row.get('lastScanOwner')
                if not owner and disc.get('ownerState') == 'worn':
                    owner = disc.get('location')
                if owner:
                    self.equipped[owner, slot].append((disc, measured(disc, base, exported)))
                elif (disc.get('ownerState') == 'bag' or exported) and (values := measured(disc, base, exported)):
                    self.bag[slot].append((disc, values))
        with QSignalBlocker(self.agent):
            self.agent.clear()
            for agent in sorted({name for name, slot in self.equipped}, key=lambda name:agents.get(name, name)):
                self.agent.addItem(agents.get(agent, agent), userData=agent)
            if selected and self.agent.findData(selected) >= 0:
                self.agent.setCurrentIndex(self.agent.findData(selected))
        self.select_slot()

    def select_slot(self, *_):
        slot = str(self.slot.currentIndex() + 1)
        worn = self.equipped.get((self.agent.currentData(), slot), [])
        self.baseline = worn[0] if len(worn) == 1 and worn[0][1] else None
        self.candidates = []
        if self.baseline:
            disc, values = self.baseline
            suit = self.sets.get(disc['setKey'], disc['setKey'])
            self.worn.setText(f"上次装备　{suit} +{disc['level']}　{format_stats(values['main'])}　｜　{format_stats(values['subs'])}")
            self.candidates = sorted(self.bag[slot], key=lambda item:(-item[0]['level'], item[0]['setKey'] != disc['setKey']))
        else:
            self.worn.setText('该号位的穿戴记录不完整' if self.equipped else '还没有穿戴记录')
        with QSignalBlocker(self.discs):
            fill(self.discs, [(self.sets.get(d['setKey'], d['setKey']), f"+{d['level']}",
                               format_stats(v['main']), format_stats(v['subs'])) for d, v in self.candidates])
        self.select_disc()

    def select_disc(self):
        index = self.discs.currentRow()
        if not 0 <= index < len(self.candidates):
            self.change.setText('这次库存里没有可比较的同号位盘' if self.baseline else '')
            return
        candidate, values = self.candidates[index]
        delta = stat_change(self.baseline[1], values)
        text = format_stats(delta, signed=True) or '相同'
        if candidate['setKey'] != self.baseline[0]['setKey']:
            text += '　｜　套装不同'
        if candidate.get('ownerState') == 'unknown':
            text += '　｜　装备者未知'
        self.change.setText('单盘词条变化　' + text)

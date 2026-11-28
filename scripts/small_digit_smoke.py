"""Replay a real panel whose first substat value is a small, thin "19".

At the panel's native size the OCR model reads the digit correctly but with too little confidence (0.796) for
the value check, which requires 0.85, so the disc came back without values. Read at twice the size the same
digit scores 1.000. OhMyZZZ reads such a panel again enlarged and keeps the result only when it names the same
lines and passes the same value check. No game window is used.
"""
import argparse

import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--home', required=True, type=Path)
home = ap.parse_args().home
repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
import cv2
import numpy as np
from one_dragon.utils import os_utils
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.base.matcher.ocr.onnx_ocr_matcher import OnnxOcrMatcher, OnnxOcrParam
from inventory_scan.parser.drive_disk_parser import DriveDiskParser
from drive_disc_manager.scan_marks import MarkReader, reread
from drive_disc_manager.order_capture import placed, usable

os_utils.set_work_dir(str(home))
ocr = OnnxOcrMatcher(OnnxOcrParam(ocr_model_name='ppocrv6', det_limit_side_len=1920))
assert ocr.is_file_existed() and ocr.init_model()
parser = DriveDiskParser()
# The live reader reads the lock/trash controls from a footer. This panel has none, so a blank one is supplied
# and the lock state comes back unknown; only the stats are under test.
blank_footer = np.zeros((85, 870, 3), np.uint8)
raw = cv2.imdecode(np.frombuffer((repo/'docs/e2e/small_digit_slot3_panel.png').read_bytes(), np.uint8), cv2.IMREAD_COLOR)
panel = cv2.cvtColor(raw, cv2.COLOR_BGR2RGB)
EXPECTED = {'main': {'def': 46.0}, 'subs': {'atk': 19.0, 'crit_dmg_': 4.8, 'pen': 9.0}}


def items_of(image):
    return [dict(text=t, confidence=m.confidence, position=(m.x, m.y, m.x+m.w, m.y+m.h))
            for t, boxes in ocr.run_ocr(image).items() for m in boxes]


def reader():
    return MarkReader(parser, ocr, blank_footer, controls={})


# The sample must still show the problem, or this replay proves nothing.
items = items_of(panel)
nineteen = next(i for i in items if i['text'] == '19')
assert .0 < nineteen['confidence'] < .85, 'the sample no longer has an unsure digit: %s' % nineteen['confidence']
first = reader().parse_ocr_result(items, None)
assert placed(first) and not usable(first), first
print('PASS  at native size the digit is read but below the confidence the value check requires (%.3f)' % nineteen['confidence'])

# Reading it again enlarged fills the values, and the lines are the same as before.
again = reread(reader(), ocr, panel, first)
assert again and usable(again) and again['observedValues'] == EXPECTED, again and again.get('observedValues')
assert all(again[k] == first[k] for k in ('setKey', 'slotKey', 'level', 'rarity', 'mainStatKey', 'substats')), 'the lines changed'
assert nineteen['text'] == '19' and again['observedValues']['subs']['atk'] == 19.0
print('PASS  read again enlarged, the same disc has its values: 防御力 46 / 攻击力 19 / 暴击伤害 4.8% / 穿透值 9')

# The re-read never loosens the check. Feed the enlarged reading a value the game cannot show for this line
# (flat attack only comes in 19, 38, 57, ...): it must be refused, and the caller keeps the first reading.
real_run_ocr = ocr.run_ocr
class FakeBoxes:
    def __init__(self, m, text):
        self.__dict__.update(text=text, confidence=m.confidence, x=m.x, y=m.y, w=m.w, h=m.h)
def ocr_reporting_18(image):
    out = {}
    for text, boxes in real_run_ocr(image).items():          # the real OCR, not this stand-in
        out['18' if text == '19' else text] = [FakeBoxes(m, '18' if text == '19' else text) for m in boxes]
    return out
with patch.object(ocr, 'run_ocr', side_effect=ocr_reporting_18):
    refused = reread(reader(), ocr, panel, first)
assert refused is None, refused and refused.get('observedValues')
print('PASS  a value the game cannot show is refused at the enlarged size too')

# A re-read that names different lines than the first reading is refused as well.
other_lines = dict(first, substats=[dict(s) for s in first['substats']])
other_lines['substats'][2] = dict(key='hp', upgrades=1)
assert reread(reader(), ocr, panel, other_lines) is None
print('PASS  a re-read that names other lines than the first reading is refused')

# The whole resume reading path on a full-size frame: the disc comes back usable in one call.
from drive_disc_manager import order_capture
import drive_disc_manager.scan_cell as scan_cell
import drive_disc_manager.scan_copies as scan_copies
screen = np.zeros((1080, 1920, 3), np.uint8)
h, w = panel.shape[:2]
screen[267:267+h, 1408:1408+w] = panel
scanner = SimpleNamespace(ctx=SimpleNamespace(ocr=ocr, screen_loader=SimpleNamespace(
    get_area=lambda *_: SimpleNamespace(rect=Rect(1408, 267, 1838, 768)))), drive_disk_scanner=SimpleNamespace(parser=parser))
with patch.object(scan_cell, 'selected_card', lambda frame, target: (4, 300)), patch.object(scan_copies, 'scroll_thumb', lambda frame: None):
    disc, _crop = order_capture.read_disc(scanner, screen, None, 0)
assert usable(disc) and disc['observedValues'] == EXPECTED, disc and disc.get('observedValues')
print('PASS  the resume pass reads this disc complete in one call')

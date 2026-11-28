"""Replay a real slot-5 disc whose main stat is wind damage, from its panel image through the whole reading path.

The scanner's own parser has no entry for wind, names the main stat by substring and falls back to HP%, then
drops any substat equal to that main stat. OhMyZZZ corrects the main stat from the game's own line and reads the
substats again. No game window is used.
"""
import argparse
import copy
import sys
from pathlib import Path

ap = argparse.ArgumentParser(description=__doc__)
ap.add_argument('--home', required=True, type=Path)
home = ap.parse_args().home
repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
import cv2
import numpy as np
from one_dragon.utils import os_utils
from one_dragon.base.matcher.ocr.onnx_ocr_matcher import OnnxOcrMatcher, OnnxOcrParam
from inventory_scan.parser.drive_disk_parser import DriveDiskParser
from drive_disc_manager.scan_marks import MarkReader

os_utils.set_work_dir(str(home))
ocr = OnnxOcrMatcher(OnnxOcrParam(ocr_model_name='ppocrv6', det_limit_side_len=1920))
assert ocr.is_file_existed() and ocr.init_model()
parser = DriveDiskParser()
# The live reader reads the lock/trash controls from a footer. These panels have none, so a blank one is supplied
# and the lock state comes back unknown; only the stats are under test.
blank_footer = np.zeros((85, 870, 3), dtype=np.uint8)


def items_of(name):
    frame = cv2.imdecode(np.frombuffer((repo/'docs/e2e'/name).read_bytes(), np.uint8), cv2.IMREAD_COLOR_RGB)
    return [dict(text=t, confidence=m.confidence, position=(m.x, m.y, m.x+m.w, m.y+m.h))
            for t, boxes in ocr.run_ocr(frame).items() for m in boxes]


def read(items):
    return MarkReader(parser, ocr, blank_footer, controls={}).parse_ocr_result(items, None)


wind = items_of('wind_slot5_panel.png')
# What the scanner's parser makes of it on its own: no wind in its table, so the main stat falls back to HP%.
alone = parser.parse_ocr_result([dict(i) for i in wind], None)
assert alone['mainStatKey'] == 'hp_', 'the sample no longer shows the scanner parser misreading wind'

disc = read(copy.deepcopy(wind))
assert disc['mainStatKey'] == 'wind_dmg_'
assert disc['observedValues'] == {'main': {'wind_dmg_': 7.5}, 'subs': {'def_': 4.8, 'hp': 112.0, 'crit_dmg_': 4.8}}, disc['observedValues']
print('PASS real 风属性伤害加成 panel: main stat corrected to wind, values agree with the screenshot')

# The same panel with its "防御力 4.8%" line turned into "生命值 3.0%": a legal HP% substat. The scanner's parser
# drops it because it believes the main stat is HP%; the corrected reading keeps it.
swapped = copy.deepcopy(wind)
for item in swapped:
    if item['text'] == '防御力' and 300 < item['position'][1] < 345:
        item['text'] = '生命值'
    elif item['text'] == '4.8%' and 300 < item['position'][1] < 345:
        item['text'] = '3.0%'
dropped = parser.parse_ocr_result([dict(i) for i in swapped], None)
assert [s['key'] for s in dropped['substats']] == ['hp', 'crit_dmg_'], 'the scanner parser no longer drops the HP% substat'
disc = read(swapped)
assert disc['mainStatKey'] == 'wind_dmg_' and [s['key'] for s in disc['substats']] == ['hp_', 'hp', 'crit_dmg_'], disc
assert disc['observedValues'] == {'main': {'wind_dmg_': 7.5}, 'subs': {'hp_': 3.0, 'hp': 112.0, 'crit_dmg_': 4.8}}, disc['observedValues']
print('PASS an HP% substat the scanner had dropped is read again under the corrected main stat')

# Correcting the name does not loosen the value check.
wrong = copy.deepcopy(wind)
for item in wrong:
    if item['text'] == '7.5%':
        item['text'] = '9.0%'
disc = read(wrong)
assert disc['mainStatKey'] == 'wind_dmg_' and disc['observedValues'] is None, disc
print('PASS a misread main value on a wind panel is still rejected')

# A disc whose main stat the scanner names correctly is left alone.
ether = read(items_of('ether_slot5_panel.png'))
assert ether['mainStatKey'] == 'ether_dmg_' and ether['observedValues'] == {
    'main': {'ether_dmg_': 7.5}, 'subs': {'atk_': 3.0, 'pen': 9.0, 'anomProf': 9.0}}, ether
print('PASS a correctly parsed disc is unchanged')

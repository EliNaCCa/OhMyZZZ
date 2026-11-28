"""Replay a real slot-5 disc whose main stat is 以太伤害加成, from its panel image through the value check.

The scan used to drop every such disc: the scanner's parser recognises the label by substring, but the
plugin's value check only accepted a spelling the game does not print. No game window is used.
"""
import argparse
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace

parser_args = argparse.ArgumentParser(description=__doc__)
parser_args.add_argument('--home', required=True, type=Path)
home = parser_args.parse_args().home
repo = Path(__file__).resolve().parents[1]
sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
from one_dragon.utils import os_utils
from one_dragon.base.matcher.ocr.onnx_ocr_matcher import OnnxOcrMatcher, OnnxOcrParam
from inventory_scan.parser.drive_disk_parser import DriveDiskParser
from drive_disc_manager.scan_marks import MarkReader
from drive_disc_manager.stat_reading import measure, label_maps, compact

import cv2
import numpy as np

os_utils.set_work_dir(str(home))
ocr = OnnxOcrMatcher(OnnxOcrParam(ocr_model_name='ppocrv6', det_limit_side_len=1920))
assert ocr.is_file_existed() and ocr.init_model()
real_parser = DriveDiskParser()
frame = cv2.imdecode(np.frombuffer((repo/'docs/e2e/ether_slot5_panel.png').read_bytes(), np.uint8), cv2.IMREAD_COLOR_RGB)
items = [dict(text=text, confidence=m.confidence, position=(m.x, m.y, m.x+m.w, m.y+m.h))
         for text, matches in ocr.run_ocr(frame).items() for m in matches]
# The live marker reads the lock/trash controls from the footer. This panel has no footer, so a blank one
# is supplied: the lock state comes back unknown, and only the stats are under test.
blank_footer = np.zeros((85, 870, 3), dtype=np.uint8)
disc = MarkReader(real_parser, ocr, blank_footer, controls={}).parse_ocr_result(items, frame)
assert disc, 'the scanner parser did not recognise the disc'
assert (disc['setKey'], disc['slotKey'], disc['level'], disc['rarity'], disc['mainStatKey']) == (
    'MoonlightLullaby', '5', 0, 'S', 'ether_dmg_'), disc
values = measure(items, disc, real_parser)
assert values == {'main': {'ether_dmg_': 7.5}, 'subs': {'atk_': 3.0, 'pen': 9.0, 'anomProf': 9.0}}, values
print('PASS real 以太伤害加成 panel: OCR, parser and value check agree with the screenshot')

# Every element the game has, in the spelling it prints on slot 5.
main_map, _ = label_maps(real_parser)
for element, key in (('物理', 'physical'), ('火', 'fire'), ('冰', 'ice'), ('电', 'electric'), ('以太', 'ether'),
                     ('风', 'wind'), ('流光', 'lumiflux')):
    assert main_map[compact(element+'伤害加成')] == key+'_dmg_' and main_map[compact(element+'属性伤害加成')] == key+'_dmg_'

# The main-stat value must be the one the game shows at that level; a misread is rejected, not accepted.
def with_main(text, level=None):
    changed = [dict(item, text=text if item['text'].endswith('7.5%') else item['text']) for item in items]
    return measure(changed, dict(disc, level=level) if level is not None else disc, real_parser)
assert with_main('9%') is None and with_main('30%') is None and with_main('7.5') is None
assert with_main('30%', level=15) == {'main': {'ether_dmg_': 30.0}, 'subs': {'atk_': 3.0, 'pen': 9.0, 'anomProf': 9.0}}
# Levels are not always multiples of three: the game shows a different main value at each level.
from drive_disc_manager.optimizer import exported_s_main
at_four = float(exported_s_main('ether_dmg_', 4))
assert at_four == 13.5
assert with_main('13.5%', level=4) == {'main': {'ether_dmg_': at_four}, 'subs': {'atk_': 3.0, 'pen': 9.0, 'anomProf': 9.0}}
assert with_main('13.5%', level=3) is None and with_main('9.8%', level=4) is None
print('PASS wrong main values rejected at +0, +3, +4 and +15; every elemental label resolves')

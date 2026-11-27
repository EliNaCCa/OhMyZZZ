"""Replay one saved CN material picker through the production confirm path, with a fake click."""
from pathlib import Path
import sys
from types import SimpleNamespace as NS
from unittest.mock import Mock


def smoke(home, folder):
    sys.path[:0] = [str(Path(__file__).resolve().parents[1]), str(home/'src'), str(home/'plugins')]
    import cv2
    import numpy as np
    from one_dragon.base.matcher.ocr.onnx_ocr_matcher import OnnxOcrMatcher, OnnxOcrParam
    from one_dragon.utils import os_utils
    from inventory_scan.parser.drive_disk_parser import DriveDiskParser
    from drive_disc_manager.upgrade_ui import UpgradeUI
    from drive_disc_manager.store import read

    os_utils.set_work_dir(str(home))
    ocr = OnnxOcrMatcher(OnnxOcrParam(ocr_model_name='ppocrv6', det_limit_side_len=1920))
    assert ocr.is_file_existed() and ocr.init_model()
    frame = cv2.imdecode(np.fromfile(folder/'preview.png', dtype=np.uint8), cv2.IMREAD_COLOR)
    rgb = cv2.cvtColor(frame, cv2.COLOR_BGR2RGB)
    before, record = read(folder/'disc.json'), read(folder/'result.json')
    scanner = NS(ctx=NS(ocr=ocr, controller=None), drive_disk_scanner=NS(parser=DriveDiskParser), screenshot=lambda:rgb)
    ui = UpgradeUI(scanner, lambda:None, 1, {})
    ui.title = record['disc']
    ui.expected_values = before['observedValues']
    events = []
    ui.click = lambda point: events.append('click')
    levels = ui.preview()
    assert levels == (record['current'], record['target'])
    ui.confirm(*levels, before_click=lambda: events.append('record'))
    assert events == ['record', 'click']

    # Keep the same set, slot and starting level. A different fourth line or main stat
    # changes only the intended piece, not the screenshot or underlying OCR result.
    for values in (dict(ui.expected_values, subs={**before['observedValues']['subs'], 'crit_':2.4}),
                   dict(ui.expected_values, main={'def_':7.5}),
                   dict(ui.expected_values, subs={**before['observedValues']['subs'], 'anomProf':18})):
        ui.expected_values = values
        before_click = Mock()
        try:
            ui.confirm(*levels, before_click=before_click)
        except ValueError as exc:
            assert '主副属性' in str(exc), exc
        else:
            raise AssertionError('accepted a different disc')
        before_click.assert_not_called()
    assert events == ['record', 'click']
    print('PASS', ui.title, levels, before['observedValues'], 'and three mismatches before journaling')


if __name__ == '__main__':
    smoke(Path(sys.argv[1]), Path(sys.argv[2]))

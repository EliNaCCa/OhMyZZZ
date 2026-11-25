"""Read one published disc before and after marking with the installed scanner and OCR."""
import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace


def smoke(home):
    import cv2
    import numpy as np

    repo = Path(__file__).resolve().parents[1]
    sys.path[:0] = [str(repo), str(home/'src'), str(home/'plugins')]
    from one_dragon.utils import os_utils
    from one_dragon.base.geometry.rectangle import Rect
    from one_dragon.base.matcher.ocr.onnx_ocr_matcher import OnnxOcrMatcher, OnnxOcrParam
    from inventory_scan.parser.drive_disk_parser import DriveDiskParser
    from drive_disc_manager.live import Marker
    from drive_disc_manager.manager_app import wiki_path
    from drive_disc_manager.planner import plan
    from drive_disc_manager.store import defaults, equipment_names, fingerprint, read

    os_utils.set_work_dir(str(home))
    dictionary = wiki_path()
    ocr = OnnxOcrMatcher(OnnxOcrParam(ocr_model_name='ppocrv6', det_limit_side_len=1920))
    assert ocr.is_file_existed() and ocr.init_model()
    with TemporaryDirectory() as fixture:
        root = Path(fixture)
        os_utils.set_work_dir(fixture)
        (root/'plugin_data/wiki_data').mkdir(parents=True)
        shutil.copyfile(dictionary, root/'plugin_data/wiki_data/zzz_translation.json')
        parser = DriveDiskParser()
        area = SimpleNamespace(rect=Rect(1408,267,1838,768))
        ctx = SimpleNamespace(current_instance_idx=1, run_context=SimpleNamespace(is_context_running=True),
                              controller=SimpleNamespace(game_win=SimpleNamespace(is_win_active=True)),
                              ocr=ocr, screen_loader=SimpleNamespace(get_area=lambda *_:area))
        app = SimpleNamespace(root=root, account=1, ctx=ctx)
        scanner = SimpleNamespace(drive_disk_scanner=SimpleNamespace(parser=parser))
        sample = dict(id='1', setKey=parser.translation_service.translate_equipment('谶羽之誓','EN').replace(' ',''),
                      slotKey='5',level=3,rarity='S',mainStatKey='atk_',
                      substats=[dict(key=k, upgrades=1) for k in ('atk','crit_','def_','hp')])
        fp = fingerprint(sample)
        # Only the title count uses 319. There is no inventory traversal.
        marker = Marker(app, scanner, dict(discs=[dict(fp=fp)]+[dict(fp=f'fixture-{i}') for i in range(318)]))
        for name, state in [('mark_before','unmarked'), ('mark_after','marked')]:
            path = repo/'docs/e2e/recap_20261101'/f'{name}.jpg'
            frame = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR_RGB)
            frame = cv2.resize(frame,(1920,1080),interpolation=cv2.INTER_CUBIC)
            app.screenshot = lambda: frame
            assert marker.detail() == (fp, True, state, False), name
        rules = {'version':'frame-smoke','fields':{'matrix/FeatheredFate/5':['atk_']}}
        cfg = dict(defaults(), reserve=0, pins=[fp])
        z = dict(format='ZOD',version=1,dbVersion=2,source='Zenless Optimizer',characters=[],wengines=[],
                 discs=[sample],audit=dict(game_total=1,exported=1,delta=0))
        names = equipment_names(read(dictionary))
        row, = plan(z,cfg,rules,names)['discs']
        assert row['set']=='FeatheredFate' and row['fp']==fp and '手动保护' in row['reasons'] and row['recommendations']
        duplicate = dict(sample,id='2',setKey='FeatheredFate')
        mixed = dict(z,discs=[dict(sample,setKey='谶羽之誓'),duplicate],audit=dict(game_total=2,exported=2,delta=0))
        assert all(row['ambiguous'] and row['review'] for row in plan(mixed,cfg,rules,names)['discs'])
    print('PASS two archived frames, one physical fingerprint, subscribed set and existing pin')


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    smoke(args.parse_args().home.resolve())

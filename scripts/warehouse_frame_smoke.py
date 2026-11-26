"""Read one published disc before and after marking with the installed scanner and OCR."""
import argparse
import shutil
import sys
from pathlib import Path
from tempfile import TemporaryDirectory
from types import SimpleNamespace
from unittest.mock import patch


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
    from drive_disc_manager.optimizer import as_disc
    from drive_disc_manager.scan_marks import MarkReader, observed_header
    from drive_disc_manager.scan_copies import selected_position, viewport_stamp
    from drive_disc_manager.stat_reading import measure
    from drive_disc_manager.disc_probe import read_selected
    from drive_disc_manager.warehouse import enter_warehouse
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
        values = {'main':{'atk_':12},'subs':{'atk':19,'crit_':2.4,'def_':4.8,'hp':112}}
        # Only the title count uses 319. There is no inventory traversal.
        marker = Marker(app, scanner, dict(discs=[dict(fp=fp,observedValues=values)]+[dict(fp=f'fixture-{i}') for i in range(318)]))
        for name, state in [('mark_before','unmarked'), ('mark_after','marked')]:
            path = repo/'docs/e2e/recap_20261101'/f'{name}.jpg'
            frame = cv2.imdecode(np.frombuffer(path.read_bytes(), np.uint8), cv2.IMREAD_COLOR_RGB)
            frame = cv2.resize(frame,(1920,1080),interpolation=cv2.INTER_CUBIC)
            app.screenshot = lambda: frame
            assert marker.detail() == (fp, 'unlocked', state, False), name
            if name == 'mark_before':
                # Replay a selection one row up on the same published frame, then
                # compare it with a genuine second viewport from the same warehouse.
                bottom, upper = 798, 624
                moved = frame.copy()
                for dst, src in ((bottom, upper), (upper, bottom)):
                    moved[dst-69:dst+70,503:641] = frame[src-69:src+70,503:641]
                positions = [selected_position(img, SimpleNamespace(x=572, y=y))
                             for img,y in ((frame,bottom), (moved,upper))]
                assert all(pos is not None and pos[0]==3 for pos in positions)
                assert positions[0][1]-positions[1][1] == bottom-upper
                assert selected_position(moved, SimpleNamespace(x=572, y=bottom)) is None
                stamp = viewport_stamp(frame,3)
                assert stamp and viewport_stamp(moved,3) == stamp
                other_bytes = (path.parent/'mark_grid_319.jpg').read_bytes()
                other = cv2.imdecode(np.frombuffer(other_bytes,np.uint8), cv2.IMREAD_COLOR_RGB)
                other = cv2.resize(other[-900:],(1920,1080),interpolation=cv2.INTER_CUBIC)
                assert viewport_stamp(other,3) != stamp
                scanner.ctx, scanner.screenshot = ctx, app.screenshot
                total = enter_warehouse(scanner, lambda:None)
                assert total == 319
                probe = read_selected(scanner, total, lambda:None)
                assert probe['fingerprint'] == fp and probe['observedValues'] == values
                assert probe['lockState'] == 'unlocked' and probe['markState'] == 'unmarked'
                # Exercise each UI transition without connecting to the game. The actual
                # captured disc above separately covers the final screen and OCR reads.
                from one_dragon.base.geometry.point import Point
                from one_dragon.base.matcher.match_result import MatchResult
                world, menu = '大世界-普通', '菜单'
                pages = [world, menu, '音擎仓库', '驱动仓库']
                route = []
                def nav_click(point, **kwargs):
                    index = len(route)
                    assert (point.x,point.y) == [(120,70),(706,952),(1680,115)][index]
                    assert bool(kwargs.get('pc_alt')) == (index == 0)
                    route.append(pages[index]); return True
                def nav_ocr(crop):
                    page = pages[len(route)]
                    text = ('驱动仓库319/2000' if page=='驱动仓库' else '音擎仓库' if page=='音擎仓库' else
                            '仓库' if page==menu and crop.shape[0]==134 else '')
                    return {text:[MatchResult(.99,500,20,12,16)]} if text else {}
                menu_area = SimpleNamespace(center=Point(120,70), pc_alt=True, gamepad_key='menu')
                bag_area = SimpleNamespace(rect=Rect(200,924,1728,1058))
                nav_ctx = SimpleNamespace(ocr=SimpleNamespace(run_ocr=nav_ocr), controller=SimpleNamespace(click=nav_click),
                    screen_loader=SimpleNamespace(get_area=lambda p,_:menu_area if p==world else bag_area))
                nav_op = SimpleNamespace(ctx=nav_ctx, screenshot=lambda:frame)
                match_page = lambda *a,screen_name: pages[len(route)] == screen_name
                switch_tab = lambda op,*a,**k: op.ctx.controller.click(Point(1680,115))
                with (patch('one_dragon.base.screen.screen_utils.is_target_screen', side_effect=match_page),
                      patch('inventory_scan.warehouse_nav.click_warehouse_tab', side_effect=switch_tab)):
                    assert enter_warehouse(nav_op, lambda:None) == 319 and route == pages[:-1]
            part = frame[267:768,1408:1838]
            items = [dict(text=text,confidence=m.confidence,position=(m.x,m.y,m.x+m.w,m.y+m.h))
                     for text,matches in ocr.run_ocr(part).items() for m in matches]
            scanned = MarkReader(parser, ocr, frame[985:1070,1050:1920]).parse_ocr_result(items,part)
            assert scanned['observedValues'] == values and fingerprint(scanned) == fp and scanned['markState'] == state
            if name == 'mark_before':
                # Same frame, with the label and fraction delivered out of order as two OCR boxes.
                label = next(i for i in items if '等级' in i['text'])
                left, top, right, bottom = label['position']
                split = [i for i in items if i is not label] + [dict(label, text='3/15', position=(right,top,right+30,bottom)),
                                                               dict(label, text='等级')]
                assert observed_header(split) == ('5', 3, 'S')
                verified = MarkReader(parser, ocr, frame[985:1070,1050:1920]).parse_ocr_result(split, part)
                assert verified['level'] == 3 and verified['observedValues'] == values
                assert observed_header(split + [dict(text='等级0/15', confidence=.99)]) == ('5', None, None)
            assert scanned['lockState'] == 'unlocked' and not scanned['lock']
            assert scanned['ownerState'] == 'bag' and not scanned['location']
            candidate = as_disc(scanned, {scanned['setKey']:'谶羽之誓'}, roll_base=1)
            assert candidate['level'] == 3 and candidate['mainStat'] == dict(type='ATK_PERCENT', value=12)
            wrong_roll = [dict(i,text='24%') if i['text']=='2.4%' else i for i in items]
            assert measure(wrong_roll, scanned, parser) is None
        marker.rows[fp]['observedValues'] = dict(values, main={'atk_':21})
        assert marker.detail() is None  # same set, slot, level and fingerprint, different numeric main
        marker.rows[fp].pop('observedValues')
        assert marker.detail() is None  # old fingerprints cannot authorize a click on their own
        # A previously unseen manual lock is preserved even when the player's quota is zero.
        from drive_disc_manager.scan_marks import read_controls
        def controls(*labels):
            return read_controls(SimpleNamespace(run_ocr=lambda _: {t:[SimpleNamespace(confidence=.99)] for t in labels}),None)
        assert controls('弃置', '解锁') == ('unmarked', 'locked')
        assert controls('解除', '锁定') == ('unknown', 'unknown')
        assert controls('弃置', '锁定', '解锁') == ('unmarked', 'unknown')
        sample['observedValues'] = values
        rules = {'version':'frame-smoke','fields':{'matrix/FeatheredFate/5':['atk_']}}
        cfg = dict(defaults(), reserve=0, pins=[fp])
        z = dict(format='ZOD',version=1,dbVersion=2,source='Zenless Optimizer',characters=[],wengines=[],
                 discs=[sample],audit=dict(game_total=1,exported=1,delta=0))
        names = equipment_names(read(dictionary))
        row, = plan(z,cfg,rules,names)['discs']
        assert row['set']=='FeatheredFate' and row['fp']==fp and '手动保护' in row['reasons'] and row['recommendations']
        policy = dict(cfg, pins=[], trimRecommended=True)
        locked = dict(sample, lock=True, lockState='locked')
        row, = plan(dict(z, discs=[locked]),policy,rules,names)['discs']
        assert row['decision']=='保留' and '游戏内已锁定' in row['reasons']
        row, = plan(dict(z, discs=[dict(sample, lockState='unknown')]),policy,rules,names)['discs']
        assert row['decision']=='待复核' and '现场锁定状态待核' in row['review']
        duplicate = dict(sample,id='2',setKey='FeatheredFate')
        mixed = dict(z,discs=[dict(sample,setKey='谶羽之誓'),duplicate],audit=dict(game_total=2,exported=2,delta=0))
        assert all(row['ambiguous'] and row['review'] for row in plan(mixed,cfg,rules,names)['discs'])
    print('PASS one +3 disc across two frames, five stat readings, correct optimizer input, mismatch blocked, pin preserved')


if __name__ == '__main__':
    args = argparse.ArgumentParser()
    args.add_argument('--home', type=Path, required=True)
    smoke(args.parse_args().home.resolve())

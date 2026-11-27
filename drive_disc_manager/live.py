"""Live reversible markers. The inventory scanner's lock/trash/rarity are placeholders.
Read the on-screen lock and trash controls again before handing control to the host.
"""
import re
import time
from collections import Counter
from pathlib import Path

import cv2
from one_dragon.base.geometry.point import Point
from one_dragon.utils import cv2_utils
from .warehouse import enter_warehouse, warehouse_header
from .store import digest, fingerprint, read, save, config
from .marks import mark_action, marker_for, owns_spare
from .scan_marks import mark_hint, observed_grade, read_controls
from .stat_reading import measure
from .task_order import marker_conflict


def hints(ocr, screen):
    return mark_hint(ocr, screen[995:1065,1550:1920])


class Marker:
    batch = None

    def __init__(self, app, scanner, report):
        if report.get('networkPreview'):
            raise ValueError('请先用游戏画面核对本轮标记清单')
        self.app = app
        self.scanner = scanner
        self.grid = scanner.drive_disk_scanner
        self.parser = self.grid.parser
        self.root = app.root
        self.cfg = config(self.root)
        self.report = report
        self.rows = {row['fp']:row for row in report['discs']}
        self.seen_counts = Counter(row['fp'] for row in report['discs'])
        self.history = read(self.root/'markers.json', {})
        self.observed = {}
        self.released = set()
        asset = Path(__file__).parent/'assets'/'lock_open.png'
        import numpy as np
        self.template = cv2.imdecode(np.frombuffer(asset.read_bytes(),np.uint8),cv2.IMREAD_GRAYSCALE)
        if self.template is None:
            raise ValueError('缺少开锁模板')

    def guard(self):
        if not self.app.ctx.run_context.is_context_running or self.app.ctx.current_instance_idx != self.app.account:
            raise ValueError('运行状态或账号已改变')
        if not self.app.ctx.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')

    def frame(self):
        self.guard()
        screen = self.app.screenshot()
        if screen is None or screen.shape[:2]!=(1080,1920):
            raise ValueError('驱动仓库画面尺寸变化')
        _, total = warehouse_header(self.app.ctx.ocr, screen)
        if total != len(self.report['discs']):
            raise ValueError('当前驱动仓库数量与计划不匹配')
        return screen

    def detail(self):
        screen = self.frame()
        area = self.app.ctx.screen_loader.get_area('仓库-驱动仓库','驱动盘属性')
        part = cv2_utils.crop_image_only(screen, area.rect)
        items = []
        for text,matches in (self.app.ctx.ocr.run_ocr(part) or {}).items():
            for m in matches:
                items.append(dict(text=text,confidence=m.confidence,position=(m.x,m.y,m.x+m.w,m.y+m.h)))
        if not items or any(i['confidence']<0.85 for i in items):
            return None
        texts = ''.join(item['text'] for item in items).replace(' ','')
        slot = re.search(r'\[([1-6])\]',texts.replace('【','[').replace('】',']'))
        level = re.search(r'(?:等级|Lv[.]?)([0-9]{1,2})/(?:9|12|15)(?![0-9])',texts,re.I)
        rank = observed_grade(items)
        if not slot or not level or rank is None or ('主属性' not in texts and 'MainStat' not in texts):
            return None
        disc = self.parser.parse_ocr_result([dict(i) for i in items],None)
        if not disc or str(disc['slotKey'])!=slot[1] or disc['level']!=int(level[1]):
            return None
        disc['rarity'] = rank
        fp = fingerprint(disc)
        if fp not in self.rows or self.seen_counts[fp]!=1:
            return None
        expected = self.rows[fp].get('observedValues')
        if not expected or measure(items, disc, self.parser) != expected:
            return None
        open_icon = cv2.cvtColor(screen[806:854,1508:1552],cv2.COLOR_RGB2GRAY)
        mark, lock = read_controls(self.app.ctx.ocr, screen[985:1070,1050:1920])
        icon_open = float(cv2.matchTemplate(open_icon,self.template,cv2.TM_CCOEFF_NORMED).max())>=0.90
        if (lock == 'unlocked') != icon_open:
            lock = 'unknown'
        from .disc_owner import read_owner
        state, owner = read_owner(part, self.parser.icon_matcher)
        planned = self.rows[fp]
        if state == 'unknown' or ('ownerState' in planned and
                (state, owner) != (planned['ownerState'], planned.get('location', ''))):
            return None
        return fp, lock, mark, state == 'worn'

    def remember(self,fp,state,intended=None,spare=False):
        entry = dict(state=state,intended=intended,inventory=self.report['inventory'],time=time.time())
        if self.rows[fp].get('exportUid'):
            entry['uid'] = self.rows[fp]['exportUid']
        if self.batch:
            entry['batch'] = self.batch
        if state in ('pending', 'released', 'manual-unlock', 'manual-keep'):
            entry['values'] = self.rows[fp]['observedValues']
        if spare:
            entry['spareLock'] = dict(origin='verified-spare-lock-v1', values=self.rows[fp]['observedValues'])
        self.history[fp] = entry
        save(self.root/'markers.json',self.history)

    def change(self, mode, detail, state, spare=False):
        fp, lock, mark, equipped = detail
        if self.detail() != detail:
            raise ValueError('点击前实物或状态已变化，请重新扫描')
        if digest(config(self.root)) != self.report['configuration']:
            raise ValueError('配置已变化，需重新预览')
        self.remember(fp, 'pending', mode)
        self.app.ctx.controller.click(Point(1447,833) if mode == 'mark' else Point(1530,830))
        time.sleep(.55)
        expected_lock = {'lock':'locked', 'release':'unlocked', 'mark':lock}[mode]
        expected_mark = 'marked' if mode == 'mark' else mark
        if self.detail() != (fp, expected_lock, expected_mark, equipped):
            raise ValueError('操作结果未能复读，先对账')
        self.observed[fp] = expected_mark
        self.remember(fp, state, spare=spare)

    def handle_disc(self, mode, detail, row):
        fp, lock, mark, equipped = detail
        entry = marker_for(self.history, fp, row.get('exportUid'))
        history = entry.get('state')
        if history == 'pending':
            raise ValueError('上次确认附近中断，需要现场对账')
        action = mark_action(mark, history, mode == 'mark')
        if action == 'already-discarded':
            if history not in ('marked','already-discarded'):
                self.remember(fp, 'already-discarded')
            if mode == 'lock':
                raise ValueError('计划保留的盘已被标记弃置，请重新扫描')
            return True
        if mark != 'unmarked' or lock not in ('locked', 'unlocked'):
            return False
        if action == 'manual-keep' and history != 'manual-keep':
            self.remember(fp, 'manual-keep')
        if mode == 'lock':
            if lock == 'locked' or row.get('manualUnlock'):
                return True
            if row.get('scannedLock') == 'locked':
                self.remember(fp, 'manual-unlock')
                raise ValueError('扫描后发现保留盘被解锁，请重新计算')
            spare = bool(row['reasons']) and all(r.startswith('备用 · ') for r in row['reasons'])
            self.change(mode, detail, 'manual-keep' if action == 'manual-keep' else 'locked',
                        spare=spare and not equipped and action != 'manual-keep' and row.get('scannedLock') == 'unlocked')
        elif mode == 'release':
            if (self.cfg.get('rotateSpareLocks') is not True or not row.get('managedSpareLock') or
                    row['decision'] != '弃置候选' or row['review'] or row['reasons'] or
                    not owns_spare(entry, row['observedValues'], uid=row.get('exportUid')) or equipped or action == 'manual-keep'):
                raise ValueError('换代条件已变化，请重新扫描')
            if lock != 'locked':
                self.remember(fp, 'manual-unlock')
                raise ValueError('发现旧备用锁已被手动解除，请重新计算')
            self.change(mode, detail, 'released')
            self.released.add(fp)
        elif action != 'manual-keep' and lock == 'unlocked' and not equipped and history != 'manual-unlock':
            if row.get('managedSpareLock') and not (fp in self.released and history == 'released' and
                                                    entry.get('inventory') == self.report['inventory']):
                raise ValueError('旧备用锁缺少本轮解除记录')
            self.change(mode, detail, 'marked')
        return True

    def phase_targets(self, mode):
        todo = {}
        for fp,row in self.rows.items():
            if row['review'] or self.seen_counts[fp]!=1: continue
            if mode=='lock' and row['decision']=='保留': todo[fp]=row
            if mode=='mark' and row['decision']=='弃置候选': todo[fp]=row
            if mode=='release' and row['decision']=='弃置候选' and row.get('managedSpareLock'): todo[fp]=row
        return todo

    def next_page(self):
        from inventory_scan.drive_disk.drive_disk_scan_const import SCROLL_MOVE_MIN_DIFF
        from .scan_copies import scroll_thumb

        def position():
            screen = self.frame()
            thumb = scroll_thumb(screen)
            # Only card pixels count. The footer and right-hand detail panel can animate independently.
            cards = cv2.resize(cv2.cvtColor(screen[240:860,110:1310], cv2.COLOR_RGB2GRAY), (160, 96),
                               interpolation=cv2.INTER_AREA)
            return thumb, cards

        start, before = position()
        self.app.ctx.controller.scroll(3, self.grid._scroll_list_center())
        time.sleep(.6)
        end, after = position()
        return bool(start and end and end[1] > start[1]) or cv2.absdiff(before, after).mean() >= SCROLL_MOVE_MIN_DIFF

    def apply(self, mode, todo):
        stop = '达到翻页上限'
        stalled = False
        for _ in range(400):
            points = self.grid._detect_points(self.frame())
            if not points:
                stop = '仓库网格未识别'
                break
            grid,_ = self.grid._resolve_grid(points)
            self.grid.grid_rows = grid
            for r,line in enumerate(grid):
                for c,pt in enumerate(line):
                    self.frame()
                    if not self.grid._ensure_selected(pt,r,c): continue
                    self.grid._stable_screenshot()
                    detail = self.detail()
                    if not detail: continue
                    fp,_,mark,_ = detail
                    self.observed[fp] = mark
                    if fp in todo and self.handle_disc(mode, detail, todo[fp]):
                        del todo[fp]
                        if not todo: return
            if not todo: return
            moved = self.next_page()
            if not moved and stalled:
                stop = '仓库列表未移动'
                break
            stalled = not moved
        if todo:
            action = {'lock':'锁定', 'release':'解除旧备用锁', 'mark':'弃置标记'}[mode]
            raise ValueError(f'{stop}，{action}尚有 {len(todo)} 张未核实，保留现场记录')

    def run(self):
        reason = marker_conflict(self.app.ctx,self.app.account,self.app.group)
        if reason: raise ValueError(reason)
        if self.report['issues'] or not self.report['rules']: raise ValueError('配装或规则待复核')
        if any(v.get('state') in ('pending', 'released') for v in self.history.values()):
            raise ValueError('上次确认附近中断，需要现场对账')
        phases = ['lock', 'mark']
        if self.cfg.get('rotateSpareLocks') and any(row.get('managedSpareLock') and row['decision']=='弃置候选' for row in self.rows.values()):
            if any(row['review'] and row['reasons'] for row in self.rows.values()):
                raise ValueError('有待复核的保留盘，本轮暂缓备用换代')
            phases.insert(1, 'release')
        for phase in phases:
            todo = self.phase_targets(phase)
            if not todo:
                continue
            # Re-enter the warehouse for each pass (the previous pass can leave a scrolled page).
            from zzz_od.operation.back_to_normal_world import BackToNormalWorld
            if not BackToNormalWorld(self.app.ctx).execute().success: raise ValueError('无法返回大世界')
            self.scanner._ensure_screen_areas()
            if enter_warehouse(self.app, self.guard) != len(self.report['discs']):
                raise ValueError('当前驱动仓库数量与计划不匹配')
            self.apply(phase, todo)
        return self.observed

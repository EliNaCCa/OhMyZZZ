"""Live reversible markers. The inventory scanner's lock/trash/rarity are placeholders.
Read the on-screen lock and trash controls again before handing control to the host.
"""
import re
import time
from collections import Counter
from pathlib import Path

import cv2
from one_dragon.base.geometry.point import Point
from one_dragon.base.geometry.rectangle import Rect
from one_dragon.utils import os_utils, cv2_utils
from inventory_scan.warehouse_nav import goto_drive_warehouse
from .store import digest, fingerprint, read, save, config
from .marks import mark_action
from .scan_marks import mark_hint


def marker_conflict(home, account, group):
    import yaml
    base = Path(home)/'config'/f'{account:02d}'
    # The built-in default is false. Check all saved groups this account can run.
    for f in base.glob('*/_group.yml'):
        group_cfg = yaml.safe_load(f.read_text('utf-8-sig')) or {}
        active = {item['app_id'] for item in group_cfg.get('app_list',[]) if item.get('enabled')}
        if 'disc_cleanup' in active:
            return '同账号应用组已启用旧驱动盘清理器'
        opts = f.parent/'drive_disc_dismantle.yml'
        if 'drive_disc_dismantle' in active and opts.is_file():
            values = yaml.safe_load(opts.read_text('utf-8-sig')) or {}
            if 'S' in str(values.get('dismantle_level','')):
                return '内置拆解还会选择未弃置的 S 级盘 · '+f.parent.name
    return ''


def hints(ocr, screen):
    return mark_hint(ocr, screen[995:1065,1550:1920])


class Marker:
    def __init__(self, app, scanner, report):
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
        asset = Path(__file__).parent/'assets'/'lock_open.png'
        import numpy as np
        self.template = cv2.imdecode(np.frombuffer(asset.read_bytes(),np.uint8),cv2.IMREAD_GRAYSCALE)
        if self.template is None:
            raise ValueError('缺少开锁模板')

    def frame(self):
        if not self.app.ctx.run_context.is_context_running or self.app.ctx.current_instance_idx != self.app.account:
            raise ValueError('运行状态或账号已改变')
        if not self.app.ctx.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')
        screen = self.app.screenshot()
        if screen is None or screen.shape[:2]!=(1080,1920):
            raise ValueError('驱动仓库画面尺寸变化')
        title = ''.join(self.app.ctx.ocr.run_ocr(screen[:200,:1250])).replace(' ','')
        total = re.search(r'驱动仓库[^0-9]{0,10}([0-9]+)/[0-9]+',title)
        if not total or int(total[1])!=len(self.report['discs']):
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
        slot = re.search(r'[【\[]([1-6])[】\]]',texts)
        level = re.search(r'(?:等级|Lv[.])([0-9]+)/15',texts,re.I)
        if not slot or not level or '主属性' not in texts and 'MainStat' not in texts:
            return None
        disc = self.parser.parse_ocr_result(items,part)
        if not disc or str(disc['slotKey'])!=slot[1] or disc['level']!=int(level[1]):
            return None
        fp = fingerprint(disc)
        if fp not in self.rows or self.seen_counts[fp]!=1:
            return None
        open_icon = cv2.cvtColor(screen[806:854,1508:1552],cv2.COLOR_RGB2GRAY)
        unlocked = float(cv2.matchTemplate(open_icon,self.template,cv2.TM_CCOEFF_NORMED).max())>=0.90
        equipped = bool(disc.get('location')) or self.parser.icon_matcher.is_region_colorful(part,54,54,85,85)
        return fp, unlocked, hints(self.app.ctx.ocr,screen), equipped

    def remember(self,fp,state,intended=None):
        self.history[fp] = dict(state=state,intended=intended,inventory=self.report['inventory'],time=time.time())
        save(self.root/'markers.json',self.history)

    def apply(self, mode):
        todo = {}
        for fp,row in self.rows.items():
            if row['review'] or self.seen_counts[fp]!=1: continue
            if mode=='lock' and row['decision']=='保留': todo[fp]=row
            if mode=='mark' and row['decision']=='弃置候选': todo[fp]=row
        if not todo: return
        seen = set()
        for page in range(400):
            points = self.grid._detect_points(self.frame())
            if not points: break
            grid,_ = self.grid._resolve_grid(points)
            new = 0
            for r,line in enumerate(grid):
                for c,pt in enumerate(line):
                    self.frame()
                    if not self.grid._ensure_selected(pt,r,c): continue
                    self.grid._stable_screenshot()
                    detail = self.detail()
                    if not detail: continue
                    fp,unlocked,mark,equipped = detail
                    if fp not in seen:
                        seen.add(fp); new+=1
                    history = self.history.get(fp,{}).get('state')
                    action = mark_action(mark, history, fp in todo and mode=='mark')
                    if action=='already-discarded':
                        if history not in ('marked','already-discarded'):
                            self.remember(fp,'already-discarded')
                        todo.pop(fp,None)
                        continue
                    if action=='manual-keep' and history!='manual-keep':
                        self.remember(fp,'manual-keep')
                    if fp not in todo: continue
                    if mark=='unknown': continue
                    if mode=='lock':
                        if not unlocked:
                            # Absence of an open-lock image is not proof of a lock.
                            # Rely only on explicit locked hint or verify via live control.
                            if self.locked_hint(): todo.pop(fp)
                            continue
                        if mark!='unmarked': continue
                        point = Point(1530,830)
                    else:
                        if action=='manual-keep' or equipped or not unlocked:
                            todo.pop(fp); continue
                        point = Point(1447,833)
                    if history=='pending': raise ValueError('上次确认附近中断，需要现场对账')
                    if self.detail()!=detail: continue
                    if digest(config(self.root))!=self.report['configuration']:
                        raise ValueError('配置已变化，需重新预览')
                    self.remember(fp,'pending',mode)
                    self.app.ctx.controller.click(point)
                    time.sleep(0.55)
                    check = self.detail()
                    expected = bool(check and check[0]==fp and (check[2]=='marked' if mode=='mark' else self.locked_hint()))
                    if not expected:
                        raise ValueError('标记结果未能复读，先对账')
                    self.remember(fp,'marked' if mode=='mark' else ('manual-keep' if action=='manual-keep' else 'locked'))
                    todo.pop(fp)
                    if not todo: return
            if not todo: return
            if not new and page: break
            self.app.ctx.controller.scroll(3,grid[-1][0]); time.sleep(.6)
        if todo:
            raise ValueError(f'{mode} 尚有 {len(todo)} 张未核实，保留现场记录')

    def locked_hint(self):
        screen=self.frame()
        text=''.join(self.app.ctx.ocr.run_ocr(screen[985:1070,1050:1920])).replace(' ','')
        return '解锁' in text or '解除锁定' in text

    def run(self):
        reason = marker_conflict(os_utils.get_work_dir(),self.app.account,self.app.group)
        if reason: raise ValueError(reason)
        if self.report['issues'] or not self.report['rules']: raise ValueError('配装或规则待复核')
        if any(v.get('state')=='pending' for v in self.history.values()):
            raise ValueError('上次确认附近中断，需要现场对账')
        for phase in ('lock','mark'):
            # Re-enter the warehouse for each pass (the previous pass can leave a scrolled page).
            from zzz_od.operation.back_to_normal_world import BackToNormalWorld
            if not BackToNormalWorld(self.app.ctx).execute().success: raise ValueError('无法返回大世界')
            self.scanner._ensure_screen_areas()
            why = goto_drive_warehouse(self.app,self.app.round_by_goto_screen)
            if why: raise ValueError(why)
            self.apply(phase)

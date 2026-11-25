"""OneDragon foreground adapter for the Chinese 1920×1080 agent equipment page."""
import math
import re
import time

from one_dragon.base.geometry.point import Point
from zzz_od.operation.back_to_normal_world import BackToNormalWorld

from .store import equipment_names, fingerprint, read
from .scan_marks import observed_grade
from .stat_reading import measure


# Navigation geometry from samsaq/ZZZ-Scanner's getImages.py. Every mutation
# additionally requires a matching OCR detail and an explicit equip button.
DETAIL = (595, 108, 990, 735)
GRID = (70, 96, 580, 942)
ACTIONS = (575, 730, 1890, 1065)
ROLE_NAME = (1032, 272, 1524, 378)
EQUIP_TAB = (1555, 963, 1780, 1030)
BASIC_TAB = (1035, 963, 1260, 1030)
REMOVE = {'卸下', '卸载', '卸下驱动盘'}
WEAR = {'装备', '穿戴', '装备驱动盘', '替换'}


class EquipUI:
    def __init__(self, app, scanner, wiki_path):
        self.app = app
        self.scanner = scanner
        self.controller = app.ctx.controller
        self.parser = scanner.drive_disk_scanner.parser
        wiki = read(wiki_path)
        self.names = {key:a['CHS'] for a in wiki['character'].values() if a.get('CHS')
                      for key in (a.get('code'),a.get('EN','').replace(' ','')) if key}
        from .store import config, equipment_keys
        self.set_names = equipment_names(wiki) | config(app.root).get('aliases',{})
        for label in equipment_keys(self.set_names):
            self.set_names.setdefault(label, label)
        self.page = 'start'

    def guard(self):
        extra = getattr(self.app, 'guard_game', None)
        if extra:
            extra()
        if self.app.ctx.current_instance_idx != self.app.account or not self.app.ctx.run_context.is_context_running:
            raise ValueError('换装时账号或运行状态已变化')
        if not self.controller.game_win.is_win_active:
            raise ValueError('游戏窗口已失去焦点')

    def frame(self):
        self.guard()
        screen = self.app.screenshot()
        if screen is None or screen.shape[:2] != (1080,1920):
            raise ValueError('需要 1920×1080 游戏画面')
        return screen

    def items(self, screen, region):
        x, y, right, bottom = region
        crop = screen[y:bottom,x:right]
        return [dict(text=text.strip(), confidence=box.confidence,
                     position=(box.x,box.y,box.x+box.w,box.y+box.h))
                for text, boxes in (self.app.ctx.ocr.run_ocr(crop) or {}).items() for box in boxes]

    def button(self, screen, region, labels):
        items = [i for i in self.items(screen,region) if i['text'].replace(' ','') in labels and i['confidence']>=0.92]
        if len(items) != 1:
            return None
        x1,y1,x2,y2 = items[0]['position']
        return Point(region[0]+(x1+x2)//2, region[1]+(y1+y2)//2)

    def click(self, pos):
        self.guard()
        if not self.controller.click(pos):
            raise ValueError('换装界面点击未送达')
        time.sleep(.45)

    def open_agent(self, target):
        expected = self.names.get(target['agent'])
        if not expected:
            raise ValueError('扫描器缺少该代理人的中文名称')
        if self.page == 'picker' and not self.button(self.frame(),BASIC_TAB,{'基础'}):
            self.guard()
            self.controller.keyboard_controller.tap('esc')
            time.sleep(.55)
            self.page = 'wheel'
        if self.page == 'start':
            self.frame()
            back = BackToNormalWorld(self.app.ctx, timeout_seconds=25)
            back.need_check_game_win = False
            if not back.execute().success:
                raise ValueError('无法返回大世界')
            self.frame()
            self.controller.keyboard_controller.tap('c')
            time.sleep(1)
        screen = self.frame()
        basic = self.button(screen,BASIC_TAB,{'基础'})
        if not basic or not self.button(screen,EQUIP_TAB,{'装备'}):
            raise ValueError('代理人界面未识别到基础和装备页签')
        self.click(basic)
        seen = set()
        for _ in range(len(self.names)+1):
            screen = self.frame()
            names = {i['text'] for i in self.items(screen,ROLE_NAME) if i['confidence']>=.92} & set(self.names.values())
            if len(names)!=1:
                raise ValueError('无法核实当前代理人姓名')
            current = names.pop()
            if current == expected:
                equip = self.button(screen,EQUIP_TAB,{'装备'})
                if not equip: raise ValueError('代理人装备页签消失')
                self.click(equip)
                self.page = 'wheel'
                return
            if current in seen: break
            seen.add(current)
            area = self.app.ctx.screen_loader.get_area('代理人-信息','按钮-下一位代理人')
            if area is None: raise ValueError('扫描器缺少下一位代理人按钮')
            self.click(area.rect.center)
        raise ValueError('在已拥有代理人中没有找到 '+expected)

    def detail(self, screen, target):
        data = self.items(screen, DETAIL)
        if not data or any(i['confidence'] < .85 for i in data): return None
        raw = ''.join(i['text'] for i in data).replace(' ','').replace('【','[').replace('】',']')
        slot = re.search(r'\[([1-6])\]',raw)
        level = re.search(r'(?:等级|Lv\.?)(\d{1,2})/15',raw,re.I)
        if not slot or int(slot[1]) != target['slot'] or not level or '主属性' not in raw or '副属性' not in raw:
            return None
        for i in data: i['text'] = i['text'].replace('【','[').replace('】',']')
        disc = self.parser.parse_ocr_result([dict(i) for i in data], None)
        if not disc or observed_grade(data) != 'S' or disc['level'] != int(level[1]): return None
        disc['observedValues'] = measure(data, disc, self.parser)
        return disc, raw

    def current(self, screen, target):
        detail = self.detail(screen,target)
        if not detail: return None, None
        disc, raw = detail
        if not disc['observedValues'] or disc['observedValues'] != target.get('observedValues'):
            return None, None
        if self.set_names.get(target['set'],'\0') not in raw or fingerprint(disc) != target['fp']:
            return None, None
        # The wheel's Equipment tab is outside the picker. It must never be
        # accepted as the button for changing a disc.
        if self.button(screen,BASIC_TAB,{'基础'}): return None, None
        remove = self.button(screen,ACTIONS,REMOVE)
        equip = self.button(screen,ACTIONS,WEAR)
        if remove and not equip: return 'equipped', None
        if equip and not remove: return 'available', equip
        return None, None

    def picker_visible(self, screen, target):
        if self.button(screen,BASIC_TAB,{'基础'}):
            return False
        if self.detail(screen,target):
            return True
        return any('驱动盘' in i['text'] and i['confidence']>=.92
                   for i in self.items(screen,(48,0,1010,100)))

    def inspect(self, target):
        self.open_agent(target)
        angle = math.radians((225,180,135,45,0,315)[target['slot']-1])
        self.click(Point(round(1440 + 270*math.cos(angle)), round(540 + 270*math.sin(angle))))
        self.page = 'picker'
        screen = self.frame()
        # Demand a drive-disc picker before exploring the four-column list.
        if not self.picker_visible(screen,target):
            raise ValueError('当前号位选盘页未识别，需核对游戏界面')
        state,_ = self.current(screen,target)
        if state: return state
        self.guard()
        self.controller.scroll(-120,Point(315,465))
        time.sleep(.5)
        previous = None
        for _ in range(150):
            screen = self.frame()
            # Compare the list alone. A stationary grid means we reached its end.
            x,y,x2,y2 = GRID
            view = screen[y:y2:8,x:x2:8].copy()
            if previous is not None and (view == previous).all(): break
            previous = view
            for row in range(5):
                for col in range(4):
                    if not self.picker_visible(self.frame(), target):
                        raise ValueError('选择实物时选盘界面已变化')
                    self.click(Point(round(144+134.4*col),round(162+170.64*row)))
                    screen = self.frame()
                    state,_ = self.current(screen,target)
                    if state: return state
            self.guard()
            self.controller.scroll(4,Point(315,465))
            time.sleep(.45)
        raise ValueError(f"{target['agent']} 的 {target['slot']} 号盘未匹配到唯一实物与装备状态")

    def equip(self, target, checkpoint):
        checkpoint()
        state, button = self.current(self.frame(),target)
        if state != 'available' or not button:
            raise ValueError('穿戴前实物或按钮已变化')
        self.click(button)
        screen = self.frame()
        body = ''.join(i['text'] for i in self.items(screen,(550,320,1380,650)) if i['confidence']>=.9)
        confirm = self.button(screen,(930,580,1430,810),{'确认','确定'})
        if confirm:
            if not any(term in body for term in ('驱动盘','驱动光盘')) or not any(term in body for term in ('替换','装备','穿戴')):
                raise ValueError('待确认弹窗内容与换装不符')
            donor = target.get('location','')
            mentions = {label for label in self.names.values() if label and label in body}
            # Some character names contain another character's entire name.
            mentions = {label for label in mentions if not any(label != other and label in other for other in mentions)}
            allowed = {self.names.get(key) for key in (donor, target['agent'])}
            if mentions - allowed:
                raise ValueError('换装提示中的装备者与本轮配队不符，请重新计算')
            checkpoint()
            self.click(confirm)
        # The next inspect re-enters the agent and reads the exact slot.

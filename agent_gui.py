"""PokerAgent - GUI 控制台

用法：python agent_gui.py（不要和 agent_server.py 同时运行）

依赖：flask, flask-cors, werkzeug, numpy, sounddevice（与 agent_server.py 相同）
"""

import tkinter as tk
import tkinter.font as tkfont
from tkinter import filedialog, messagebox
import threading
import sys
import os
import queue
import re
import json
import bisect
from collections import deque

try:
    import agent_server
except ImportError as e:
    print(f'[Agent] 无法导入 agent_server.py: {e}')
    sys.exit(1)

from werkzeug.serving import make_server

GUI_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gui_config.json')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 颜色方案
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BG = '#0a0a0a'
PANEL = '#1a1a1a'
HEADER = '#1a1a1a'
BTN = '#222222'
BTN_H = '#2a2a2a'
BORDER = '#2a2a2a'
TXT = '#d4d4d4'
TXT2 = '#737373'
BLUE = '#facc15'
GREEN = '#22c55e'
YELLOW = '#facc15'
RED = '#ef4444'
PURPLE = '#f97316'
CYAN = '#d4d4d4'
DISABLED_FG = '#52525b'

FONT_UI = ('Microsoft YaHei UI', 10)
FONT_UI_B = ('Microsoft YaHei UI', 10, 'bold')
FONT_TITLE = ('Microsoft YaHei UI', 14, 'bold')
FONT_MONO = ('Consolas', 10)
FONT_MONO_B = ('Consolas', 10, 'bold')

_ANSI_FG = {
    30: '#000000', 31: '#cd3131', 32: '#0dbc79', 33: '#e5e510',
    34: '#2472c8', 35: '#bc3fbc', 36: '#11a8cd', 37: '#e5e5e5',
    90: '#666666', 91: '#f14c4c', 92: '#23d18b', 93: '#f5f543',
    94: '#3b8eea', 95: '#d670d6', 96: '#29b8db', 97: '#ffffff',
}


def _lerp_color(c1, c2, t):
    r = int(int(c1[1:3], 16) + (int(c2[1:3], 16) - int(c1[1:3], 16)) * t)
    g = int(int(c1[3:5], 16) + (int(c2[3:5], 16) - int(c1[3:5], 16)) * t)
    b = int(int(c1[5:7], 16) + (int(c2[5:7], 16) - int(c1[5:7], 16)) * t)
    return f'#{r:02x}{g:02x}{b:02x}'


def _parse_ansi_to_parts(text):
    parts = []
    parts_re = re.split(r'(\x1b\[[0-9;]*m)', text)
    fg = None
    for part in parts_re:
        if not part:
            continue
        m = re.match(r'\x1b\[([0-9;]*)m', part)
        if m:
            codes = [int(c) for c in m.group(1).split(';') if c] if m.group(1) else [0]
            for code in codes:
                if code == 0:
                    fg = None
                elif code in _ANSI_FG:
                    fg = _ANSI_FG[code]
        else:
            color = fg if fg else TXT
            if part:
                parts.append((part, color))
    return parts if parts else [(text, TXT)]


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 日志行分类（所有日志行的统一分类入口）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
LOG_POLL_MS = 15  # [新增] 主线程日志队列拉取间隔（无感级）
_LOG_TS_RE = re.compile(r'^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\])\s+(\S+)(?:\s*\|\s*(.*))?$')
_LOG_HTTP_RE = re.compile(r'^\d+\.\d+\.\d+\.\d+\s')


def classify_log_line(line):
    """[新增·平移自 _append_parsed] 行分类：返回 (tag, parts)。所有日志行的统一分类入口"""
    if '\x1b[' in line:
        return 'txt', _parse_ansi_to_parts(line)
    if line.startswith('Agent > '):  # [新增] CLI 回显（现在走标准管道）
        return 'prompt', None
    if _LOG_TS_RE.match(line):
        return 'txt', None
    if _LOG_HTTP_RE.match(line):
        return 'http', None
    stripped = line.strip()
    if (stripped.startswith('===') or stripped.startswith('PokerAgent')
            or stripped.startswith('监听') or stripped.startswith('工作')
            or stripped.startswith('帮助') or stripped.startswith('操作')
            or stripped.startswith('[Agent]')):
        return 'banner', None
    if line == '（空指令或注释）':  # [新增] CLI 空指令提示
        return 'warn', None
    low = line.lower()
    if 'traceback' in low or line.startswith(' File '):
        return 'err', None
    return 'txt', None


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# LogCanvas
# [第四次重构] 核心架构：
#   1. 排版引擎自研：tokenize + word-wrap 装箱，宽度计算零 Tk item 调用
#      （ASCII 等宽快速路径 + CJK 单字符宽度缓存），Tk 退化为纯绘制器
#   2. 多色 ANSI 完整渲染：每视觉行按颜色分段绘制，颜色信息零丢失
#   3. 滑动窗口：内存只保留 [窗口头, 窗口尾) 行，双向磁盘回读
#      （行 ↔ 文件字节区间精确映射，server 推送附带偏移）
#   4. 字符级文本选择：排版数据含每段字符偏移，命中测试/高亮/复制全字符级
#   5. nowrap 模式横向滚动条：Canvas xview + scrollregion
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class LogCanvas:
    # 窗口与预读
    WINDOW_DEFAULT_LINES = 4096   # 默认内存窗口行数（可通过 gui_config.json 的 log_window_lines 覆盖）
    EVICT_MARGIN_LINES = 64       # 驱逐余量：批量驱逐摊薄 prefix 重建成本
    PRELOAD_UP_ROWS = 256         # 视口距窗口头 ≤N 行 → 触发向上回读
    PRELOAD_DOWN_ROWS = 256       # 视口距窗口尾 ≤N 行 → 触发向下回读
    READ_BATCH_LINES = 500        # 每次磁盘回读行数
    READ_CHUNK_BYTES = 65536      # 磁盘读取块大小（字节）
    PRELOAD_COOLDOWN_MS = 200     # 回读冷却

    # 渲染调度
    RESIZE_DEBOUNCE_MS = 120      # resize 去抖
    LOOKBACK_ROWS = 16            # bisect 定位垫背回退行数（估算 prefix 误差兜底）
    RELAYOUT_BATCH = 200          # 后台排版链每批行数（idle 分帧，不阻塞 UI）

    # 布局
    MARGIN_X = 12
    MARGIN_Y = 8
    LINE_SPACING = 2

    # token 类型（内部）
    _T_WORD, _T_SPACE, _T_CJK = 0, 1, 2
    _CJK_MIN = 0x2E80  # CJK/全角区起点（含注音、CJK 标点等）

    def __init__(self, master, log_file, wrap=True, window_lines=None, classify=None):
        self.master = master
        self._log_file = log_file
        self._wrap = wrap
        self._classify = classify or (lambda line: ('txt', None))  # 行分类回调（tag, parts）
        self._window_lines = window_lines or self.WINDOW_DEFAULT_LINES

        # 行记录 5 元组: (base_color, parts, fstart, fend, plain)
        # parts=None 表示无 ANSI 的纯色行；fstart/fend 为该行在日志文件中的字节区间
        self._lines = deque()
        self._char_count = 0
        self._line_heights = deque()
        self._line_y_prefix = [self.MARGIN_Y]  # 【根源重构】初始值为 MARGIN_Y
        self._visible_items = {}  # row -> [canvas item id, ...]（视觉行×段展平）
        self._scroll_offset = 0
        self._auto_scroll = True

        # 排版引擎状态
        self._layouts = {}           # row -> {'vlines': [[seg...],...], 'gen': g, 'max_w': px}
        self._width_gen = 0          # 宽度代际：resize/切 wrap 时 +1，全部缓存失效
        self._pending_layout = set() # 未精排行号集合（估算占位中，后台链渐进收敛）
        self._content_max_w = 0      # nowrap 模式内容总宽（驱动横向 scrollregion）
        self._char_widths = {}       # 非ASCII字符 -> 像素宽缓存（Font.measure 首见）
        self._cjk_w = None           # CJK 代表宽度（粗估用，精排走 _char_widths）
        # [修改·根源修复] 唯一权威字体度量：tkfont.Font 实例（_build_ui 前创建）。
        #        Font 实例与 create_text 共用 Tk 字体引擎，measure() 返回精确 advance，
        #        渲染/装箱/高亮三路度量从此零偏差（原 FONT_MONO 是 ('Consolas',10) 元组
        #        无 .measure 方法；原 bbox 法含每侧 padding 系统性虚高）
        self._font_obj = tkfont.Font(family=FONT_MONO[0], size=FONT_MONO[1])
        self._cw = 8                 # ASCII 等宽基准（_measure_font 校准）

        # 滑动窗口状态
        self._file_read_pos = 0      # 窗口头行字节偏移（向上回读锚点）
        self._file_exhausted = False # 文件头已读尽
        self._tail_anchor = None     # 窗口尾被驱逐后的回读锚点（None=内存尾即文件尾）
        self._file_end_pos = 0       # 文件当前末尾（消息流持续更新）
        self._pend_text = ''         # 流式累积：未成行的字符
        self._pend_start = None      # 流式累积：该行起始字节偏移

        self._loading = False        # 向上回读进行中
        self._loading_tail = False   # 向下回读进行中
        self._last_preload_time = 0

        # 调度句柄
        self._debounce_job = None    # resize 去抖
        self._relayout_job = None    # 后台排版链
        self._render_job = None      # 渲染请求合并
        self._refresh_depth = 0      # 二次收敛渲染防重入

        # 文本选择（字符级：(row, char_idx) 对）
        self._sel_anchor = None
        self._sel_active = None
        self._sel_items = []
        self._tag_colors = {
            'ts': TXT2, 'act': BLUE, 'txt': TXT, 'ok': GREEN, 'warn': YELLOW,
            'err': RED, 'http': '#484f58', 'prompt': CYAN, 'banner': PURPLE,
        }

        self._canvas_width = 800
        self._canvas_height = 600

        self._build_ui()
        self._measure_font()
        self._load_initial_from_file()

    def _build_ui(self):
        # [修改] grid 布局：canvas + 纵向滚动条 + 横向滚动条（nowrap 模式启用）
        self.master.rowconfigure(0, weight=1)
        self.master.columnconfigure(0, weight=1)
        self.canvas = tk.Canvas(self.master, bg=BG, bd=0, highlightthickness=0,
                                cursor='arrow', takefocus=1)  # [修改] Tab 可达（焦点修复配套）
        self.canvas.grid(row=0, column=0, sticky='nsew')
        self.scrollbar = tk.Scrollbar(self.master, command=self._on_scroll, bg=BTN,
                                      troughcolor=BG, bd=0, activebackground=BTN_H)
        self.scrollbar.grid(row=0, column=1, sticky='ns')
        # [新增] 横向滚动条：仅 nowrap 且内容超宽时显示（grid_remove/grid 动态切换）
        self.xscrollbar = tk.Scrollbar(self.master, orient='horizontal', command=self.canvas.xview,
                                       bg=BTN, troughcolor=BG, bd=0, activebackground=BTN_H)
        # [新增] Canvas x 方向托管给 xview/scrollregion；y 方向仍手动 offset
        # （scrollregion 高度锁死为视口高，Canvas 不会自行纵向滚动）
        self.canvas.configure(xscrollcommand=self._on_xscroll_update)

        self.canvas.bind('<Configure>', self._on_resize)
        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        # [新增] Shift+滚轮 → 横向滚动（nowrap 模式）
        self.canvas.bind('<Shift-MouseWheel>', lambda e: self.canvas.xview_scroll(-e.delta // 120 * 3, 'units'))
        self.canvas.bind('<Button-4>', lambda e: self._scroll_delta(-60))
        self.canvas.bind('<Button-5>', lambda e: self._scroll_delta(60))
        self.canvas.bind('<Button-1>', self._on_click)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Control-c>', self._copy_selection)
        self.canvas.bind('<Control-C>', self._copy_selection)  # [新增] Ctrl+Shift+C 变体
        self.canvas.bind('<Leave>', self._on_release)

    def _measure_font(self):
        """[重构] 度量改走 Font 权威接口（原 bbox 法含 padding 系统性虚高）"""
        self._cw = self._font_obj.measure('M')  # ASCII 等宽 advance（精确）
        self._line_height = self._font_obj.metrics('linespace') + self.LINE_SPACING
        self._cjk_w = self._font_obj.measure('中')  # CJK 代表宽（粗估路径用）
        self._update_scrollregion()

    # ========== 宽度计算（零 Tk item 调用路径） ==========

    def _char_w(self, ch):
        """非 ASCII 单字符像素宽（Font.measure 精确 advance，首见缓存）"""
        w = self._char_widths.get(ch)
        if w is None:
            w = self._font_obj.measure(ch)
            self._char_widths[ch] = w
        return w

    def _text_w(self, s):
        """精排路径宽度：ASCII 全等宽快速路径 + 非 ASCII 逐字符缓存查询"""
        if s.isascii():
            return len(s) * self._cw
        total = 0
        for ch in s:
            total += self._cw if ord(ch) < 0x80 else self._char_w(ch)
        return total

    def _fast_w(self, s):
        """估算路径宽度：ASCII 快速路径，非 ASCII 用 CJK 代表宽粗估（误差由后台排版链收敛）"""
        if s.isascii():
            return len(s) * self._cw
        total = 0
        for ch in s:
            total += self._cw if ord(ch) < 0x80 else self._cjk_w
        return total

    def _estimate_height(self, plain):
        """未排版行的高度占位估算：总宽 ÷ 行宽向上取整（误差≤数行，渲染/后台链精排校正）"""
        if not self._wrap or self._get_wrap_width() <= 0 or not plain:
            return self._line_height
        w = self._fast_w(plain)
        ww = self._get_wrap_width()
        return max(self._line_height, -(-w // ww) * self._line_height)

    # ========== 排版引擎（tokenize + word-wrap 装箱） ==========

    def _tokenize(self, seg_input):
        """颜色段 [(text, color),...] → token 流 [(text, color, char_off, kind)]
        kind: _T_WORD=连续词(不可分，超宽除外) / _T_SPACE=空格run / _T_CJK=单字(任意断点)
        char_off: token 首字符在逻辑行中的偏移（供字符级选择映射）"""
        tokens = []
        off = 0
        wbuf, woff, wcolor = [], 0, None   # word 累积
        sbuf, soff, scolor = [], 0, None   # space 累积

        def flush_word():
            nonlocal wbuf, woff, wcolor
            if wbuf:
                tokens.append((''.join(wbuf), wcolor, woff, self._T_WORD))
                wbuf, woff, wcolor = [], 0, None

        def flush_space():
            nonlocal sbuf, soff, scolor
            if sbuf:
                tokens.append((''.join(sbuf), scolor, soff, self._T_SPACE))
                sbuf, soff, scolor = [], 0, None

        for text, color in seg_input:
            for ch in text:
                if ch == ' ':
                    flush_word()
                    if scolor is None:
                        scolor, soff = color, off
                    elif scolor != color:
                        flush_space()
                        scolor, soff = color, off
                    sbuf.append(ch)
                elif ord(ch) >= self._CJK_MIN:
                    flush_word(); flush_space()
                    tokens.append((ch, color, off, self._T_CJK))
                else:
                    flush_space()
                    if wcolor is None:
                        wcolor, woff = color, off
                    elif wcolor != color:
                        flush_word()
                        wcolor, woff = color, off
                    wbuf.append(ch)
                off += 1
        flush_word(); flush_space()
        return tokens

    def _layout_segments(self, seg_input, wrap_w):
        """装箱核心：token 流 → 视觉行列表。
        断行规则对齐 Tk 惯例：空格断词、换行后行首空格丢弃、CJK 任意断、
        超行宽无空格词逐字符硬切。相邻同色段合并（每视觉行段数最小化）。
        返回 (vlines, max_w)；vlines[i] = [[x, text, color, char_off], ...]"""
        tokens = self._tokenize(seg_input)
        vlines = []
        # nowrap：单视觉行，全 token 合并
        if not wrap_w or wrap_w <= 0:
            segs, x = [], 0
            for t, color, off, _kind in tokens:
                tw = self._text_w(t)
                if segs and segs[-1][2] == color:
                    segs[-1][1] += t
                else:
                    segs.append([x, t, color, off])
                x += tw
            return ([segs] if segs else [[]]), x
        cur = []  # [(token元组, 宽), ...]
        cur_w = 0
        skip_space = True  # 换行后跳过行首空格
        nonlocal_max = [0]

        def flush():
            nonlocal cur, cur_w, skip_space
            segs, x = [], 0
            for (t, color, off, _k), tw in cur:
                if segs and segs[-1][2] == color:
                    segs[-1][1] += t  # 同色相邻段合并
                else:
                    segs.append([x, t, color, off])
                x += tw
            vlines.append(segs)
            nonlocal_max[0] = max(nonlocal_max[0], x)
            cur, cur_w, skip_space = [], 0, True

        i, n = 0, len(tokens)
        while i < n:
            t, color, off, kind = tokens[i]
            tw = self._text_w(t)
            if kind == self._T_SPACE:
                if not skip_space and cur_w + tw <= wrap_w:  # 空格悬挂行尾（Tk 行为近似）
                    cur.append((tokens[i], tw)); cur_w += tw
                # 行首空格 / 超宽空格：丢弃
                i += 1
                continue
            if cur_w + tw <= wrap_w:
                cur.append((tokens[i], tw)); cur_w += tw
                skip_space = False
                i += 1
                continue
            # 放不下
            if cur and tw > wrap_w:
                # 词超整行宽且当前行已有内容 → 先换行再硬切
                flush()
            if tw > wrap_w:
                # 硬切：逐字符填满行宽（char_off 随切片推进）
                rem, rem_off = t, off
                while rem:
                    avail = wrap_w - cur_w
                    acc, cut = 0, 0
                    for ci, ch in enumerate(rem):
                        cw = self._cw if ord(ch) < 0x80 else self._char_w(ch)
                        if acc + cw > avail and cut > 0:
                            break
                        acc += cw
                        cut = ci + 1
                    cur.append(((rem[:cut], color, rem_off, self._T_WORD), acc))
                    cur_w += acc
                    flush()
                    rem, rem_off = rem[cut:], rem_off + cut
                i += 1
                continue
            if cur:
                flush()  # 普通换行（行首空格由 skip_space 丢弃）
                continue
            # cur 为空仍放不下（行宽极小的极端防御）：强制放行
            cur.append((tokens[i], tw)); cur_w += tw
            skip_space = False
            i += 1
        if cur:
            flush()
        # 【修复】空行保底一个视觉行（否则高度为 0，空行视觉上消失）
        if not vlines:
            vlines = [[]]
        return vlines, nonlocal_max[0]

    def _get_layout(self, row):
        """取行排版结果（缓存 + 代际校验，未命中则排版并缓存）"""
        lay = self._layouts.get(row)
        if lay is None or lay['gen'] != self._width_gen:
            lay = self._do_layout(row)
        return lay

    def _do_layout(self, row):
        color, parts, _fs, _fe, plain = self._lines[row]
        seg_input = parts if parts else [(plain, color)]
        vlines, mw = self._layout_segments(seg_input, self._get_wrap_width())
        lay = {'vlines': vlines, 'gen': self._width_gen, 'max_w': mw}
        self._layouts[row] = lay
        self._pending_layout.discard(row)
        if mw > self._content_max_w:  # nowrap 横向滚动范围扩大
            self._content_max_w = mw
            self._update_scrollregion()
        return lay

    # ========== 行构造与窗口维护 ==========

    def _make_record(self, text, fstart, fend):
        """原始行文本 → (行记录, 估算高度)。classify 回调做 tag/ANSI 分类"""
        tag, parts = self._classify(text)
        base_color = self._tag_colors.get(tag, TXT)
        plain = text if parts is None else ''.join(p[0] for p in parts)
        self._char_count += len(plain)
        return (base_color, parts, fstart, fend, plain), self._estimate_height(plain)

    def _append_raw_line(self, text, fstart, fend):
        """成行入口（流式累积 / gap 补读 / 向下回读 共用）：尾部追加 + 窗口满驱逐头部"""
        rec, h = self._make_record(text, fstart, fend)
        self._lines.append(rec)
        self._line_heights.append(h)
        self._line_y_prefix.append(self._line_y_prefix[-1] + h)  # O(1) 增量
        self._pending_layout.add(len(self._lines) - 1)
        if len(self._lines) > self._window_lines:
            self._evict_head()
        self._request_render()
        if self._auto_scroll and self._tail_anchor is None:
            self._scroll_to_bottom()

    def prepend_lines(self, raw_lines):
        """向上回读：[(text, fstart, fend)]（旧→新）插入窗口头，视口锚定不跳"""
        if not raw_lines:
            return 0
        n = len(raw_lines)
        insert_h = 0
        for text, fs, fe in reversed(raw_lines):  # 新→旧逐条 appendleft
            rec, h = self._make_record(text, fs, fe)
            self._lines.appendleft(rec)
            self._line_heights.appendleft(h)
            insert_h += h
        self._rebuild_prefix()
        # 行索引整体 +n：item/layout/pending/选择锚 全部重映射（Canvas item 复用不重建）
        self._visible_items = {r + n: v for r, v in self._visible_items.items()}
        self._layouts = {r + n: v for r, v in self._layouts.items()}
        # 【修复】_pending_layout 是 set，无 items() 方法，改用集合推导式
        self._pending_layout = {r + n for r in self._pending_layout}
        self._pending_layout.update(range(n))
        if self._sel_anchor is not None:
            self._sel_anchor = (self._sel_anchor[0] + n, self._sel_anchor[1])
        if self._sel_active is not None:
            self._sel_active = (self._sel_active[0] + n, self._sel_active[1])
        # 窗口头锚 = 新首行起点
        if self._lines[0][2] is not None:
            self._file_read_pos = self._lines[0][2]
        # 窗口满 → 驱逐尾部（最新端去盘，滚到底时回读）
        if len(self._lines) > self._window_lines:
            self._evict_tail()
        # 【锚定】内容整体下移 insert_height，offset 同步平移保持视口内容不变；驱逐后统一夹取
        self._scroll_offset += insert_h
        total = self._get_total_height()
        vis = self._canvas_height
        self._scroll_offset = max(0, min(self._scroll_offset, max(0, total - vis)))
        self._request_render()
        return n

    def _evict_head(self):
        """窗口头驱逐（实时流刷屏时滑动窗口上移）：全部索引敏感结构重映射"""
        target = self._window_lines - self.EVICT_MARGIN_LINES
        if len(self._lines) <= target:
            return
        removed, removed_h = 0, 0
        while len(self._lines) > target:
            rec = self._lines.popleft()
            self._char_count -= len(rec[4])
            removed_h += self._line_heights.popleft()
            removed += 1
        # 窗口头锚 = 新首行起点
        if self._lines and self._lines[0][2] is not None:
            self._file_read_pos = self._lines[0][2]
        # 先销毁被删行残留 item，再重映射（否则残影/错位）
        for r in [r for r in self._visible_items if r < removed]:
            for item in self._visible_items[r]:
                self.canvas.delete(item)
            del self._visible_items[r]
        self._visible_items = {r - removed: v for r, v in self._visible_items.items()}
        self._layouts = {r - removed: v for r, v in self._layouts.items() if r >= removed}
        self._pending_layout = {r - removed for r in self._pending_layout if r >= removed}
        if self._sel_anchor is not None:
            self._sel_anchor = ((self._sel_anchor[0] - removed, self._sel_anchor[1])
                                if self._sel_anchor[0] >= removed else None)
        if self._sel_active is not None:
            self._sel_active = ((self._sel_active[0] - removed, self._sel_active[1])
                                if self._sel_active[0] >= removed else None)
        self._rebuild_prefix()
        # 视口锚定：offset 同步上移，用户看历史时后台刷日志无跳变
        self._scroll_offset = max(0, self._scroll_offset - removed_h)
        self._update_scrollbar()

    def _evict_tail(self):
        """窗口尾驱逐（向上加载历史时最新端去盘）：设置 _tail_anchor 供向下回读"""
        target = self._window_lines - self.EVICT_MARGIN_LINES
        if len(self._lines) <= target:
            return
        removed = 0
        while len(self._lines) > target:
            rec = self._lines.pop()
            if removed == 0 and rec[2] is not None:
                self._tail_anchor = rec[2]  # 首个被驱逐行的起点 = 向下回读锚
            self._char_count -= len(rec[4])
            self._line_heights.pop()
            removed += 1
        new_len = len(self._lines)
        for r in [r for r in self._visible_items if r >= new_len]:
            for item in self._visible_items[r]:
                self.canvas.delete(item)
            del self._visible_items[r]
        self._layouts = {r: v for r, v in self._layouts.items() if r < new_len}
        self._pending_layout = {r for r in self._pending_layout if r < new_len}
        if self._sel_anchor is not None and self._sel_anchor[0] >= new_len:
            self._sel_anchor = None
        if self._sel_active is not None and self._sel_active[0] >= new_len:
            self._sel_active = None
        self._rebuild_prefix()
        # 视口若在被删区，夹取到底
        total = self._get_total_height()
        self._scroll_offset = max(0, min(self._scroll_offset, max(0, total - self._canvas_height)))
        self._update_scrollbar()

    # ========== 实时消息流（server 推送 → 行累积 → 窗口） ==========

    def on_file_append(self, stream_name, text, fstart, fend):
        """server _LogWriter 每次写入的回调入口（消息含字节偏移区间）"""
        if fend is not None and fend > (self._file_end_pos or 0):
            self._file_end_pos = fend
        if not text:
            return
        # 尾部驱逐态：文本不进内存（只推进文件末尾），滚到底时 load_more_tail 从盘回读
        if self._tail_anchor is not None:
            return
        # 衔接检查（启动竞态/漏读防御）：内存尾与消息起点之间有 gap → 正读补齐
        if self._lines and not self._pend_text:
            last_fend = self._lines[-1][3]
            if fstart is not None and last_fend is not None and fstart > last_fend:
                self._fill_gap(last_fend, fstart)
        self._consume_stream(text, fstart)

    def _consume_stream(self, text, fstart):
        """流式行累积器：write 消息可能为半行（print 分两次 write），按 \n 成行。
        成行时字节区间 = [行起点, 换行后)，与文件精确对齐"""
        if self._pend_start is None:
            self._pend_start = fstart
        pieces = text.split('\n')
        pend = self._pend_text
        for piece in pieces[:-1]:
            line_text = pend + piece
            lf_start, lf_end = self._pend_start, None
            if self._pend_start is not None:
                lf_end = self._pend_start + len(line_text.encode('utf-8')) + 1  # +1 含 LF
            self._append_raw_line(line_text, lf_start, lf_end)
            self._pend_start = lf_end
            pend = ''
        self._pend_text = pend + pieces[-1]

    def _fill_gap(self, from_pos, to_pos):
        """补读内存尾与消息流之间的磁盘 gap：完整行入内存，残段并入流累积器"""
        if to_pos <= from_pos:
            return
        try:
            lines, next_pos, tail, _eof = self._read_lines_forwards(
                from_pos, self._window_lines, stop_at=to_pos)
            for text, fs, fe in lines:
                self._append_raw_line(text, fs, fe)
            if tail:
                # gap 尾部无换行的半行 → 并入累积器，与消息流无缝拼接
                self._pend_text = self._pend_text + tail.rstrip(b'\r').decode('utf-8', errors='replace')
                self._pend_start = next_pos
        except Exception as e:
            print(f'[Agent] 补读日志 gap 失败: {e}')

    # ========== 磁盘 IO（二进制 + 字节级行边界对齐） ==========

    def _read_lines_backwards(self, read_pos, max_chars, max_lines=None):
        """倒序读：返回 (行列表[旧→新], 新字节偏移, 是否读尽, 尾部半行(text, start))。
        修复三个历史失真源：文本模式 tell() 误用 / 块边界腰斩行 / UTF-8 多字节撕裂。
        【本版修复】1) buf 以 \n 结尾时的幽灵空段剔除（否则文件尾多出一行）
        2) 行起点偏移补上跨块半行长度（否则块边界切在行中间时区间系统性偏小）
        3) 跨轮保留的半行连同行尾 \n 一起保留（否则空行在块间传递时蒸发）
        4) 批次提前结束时回退到最后收集行起点（保证批边界行对齐，无腰斩行）
        尾部半行仅在初始加载（read_pos==文件末尾）时提取，供流累积器衔接"""
        lines = []  # 新→旧（最后统一 reverse）
        read_chars = 0
        pos = read_pos
        buf = b''
        tail_partial = None
        stop_pos = read_pos  # 实际推进到的行边界（= 最后收集行起点）
        try:
            file_size = os.path.getsize(self._log_file)
            with open(self._log_file, 'rb') as f:
                first = True
                while pos > 0 and read_chars < max_chars and (max_lines is None or len(lines) < max_lines):
                    read_size = min(self.READ_CHUNK_BYTES, pos)
                    pos -= read_size
                    f.seek(pos)
                    buf = f.read(read_size) + buf  # 半行在字节层前向拼接
                    segs = buf.split(b'\n')
                    had_nl = len(segs) > 1  # buf 含换行 → segs[0] 所在行的行尾已确认
                    if first:
                        first = False
                        # 文件尾无换行 → segs[-1] 是写入中的半行，移交流累积器（不当作完整行）
                        if read_pos == file_size and segs and segs[-1] != b'':
                            half = segs.pop()
                            tail_partial = (half.decode('utf-8', errors='replace'), read_pos - len(half))
                        if segs and segs[-1] == b'':
                            segs.pop()  # buf 以 \n 结尾的幽灵空段（\n 之后无内容）
                    if pos > 0:
                        head = segs[0] if segs else b''
                        complete = segs[1:]  # segs[0] 是跨块半行，留待下一块拼接
                        # 【修复】head 连同行尾 \n 一起保留：空 head 的空行/行尾信息否则蒸发
                        buf = (head + b'\n') if had_nl else head
                        base = pos + len(head) + 1 if had_nl else pos + len(head)
                    else:
                        complete = segs
                        buf = b''
                        base = pos  # = 0
                    # 每行在 buf 中的字节偏移表（供区间计算）
                    offsets = [0]
                    for s in complete:
                        offsets.append(offsets[-1] + len(s) + 1)
                    for k in range(len(complete) - 1, -1, -1):  # 新→旧收集
                        raw = complete[k]
                        line_start = base + offsets[k]
                        text = raw.rstrip(b'\r').decode('utf-8', errors='replace')
                        lines.append((text, line_start, line_start + len(raw) + 1))
                        stop_pos = line_start
                        read_chars += len(text)
                        if max_lines is not None and len(lines) >= max_lines:
                            break
        except Exception as e:
            print(f'[Agent] 读取日志文件失败: {e}')
        lines.reverse()  # 统一为旧→新
        exhausted = (pos <= 0 and not buf)
        return lines, stop_pos, exhausted, tail_partial

    def _read_lines_forwards(self, pos, max_lines, stop_at=None):
        """正序读：返回 (行列表[旧→新], 下一读取位置, 残段字节, 是否EOF)。
        stop_at 限定读取上界（gap 补读用）；残段 = 已收集行末尾之后、stop_at 之前的原始字节
        （可能为半行，供流累积器衔接）。
        【本版修复】残段用 consumed 字节精确追踪：stop_at 切在行中间时，
        行前缀字节不再被丢弃（原实现直接跳到下一块边界，行前缀丢失导致 gap 拼接错位）"""
        lines = []
        buf = b''
        buf_start = pos
        limit = stop_at if stop_at is not None else float('inf')
        try:
            with open(self._log_file, 'rb') as f:
                f.seek(pos)
                while True:
                    chunk = f.read(self.READ_CHUNK_BYTES)
                    if not chunk:
                        break  # EOF
                    buf += chunk
                    segs = buf.split(b'\n')
                    offsets = [0]
                    for s in segs:
                        offsets.append(offsets[-1] + len(s) + 1)
                    collected = False
                    consumed = 0  # buf 中已被完整行占用的字节数
                    for k in range(len(segs) - 1):
                        raw = segs[k]
                        line_start = buf_start + offsets[k]
                        line_end = line_start + len(raw) + 1
                        if line_end > limit:
                            break  # 跨 stop_at 的行属于消息流，不收集
                        text = raw.rstrip(b'\r').decode('utf-8', errors='replace')
                        lines.append((text, line_start, line_end))
                        consumed = offsets[k + 1]
                        if len(lines) >= max_lines:
                            collected = True
                            break
                    if collected:
                        # 批次已满：残段丢弃（下次从最后完整行末尾重读，无损失）
                        last_end = lines[-1][2]
                        return lines, last_end, b'', False
                    # 残段 = 未消费部分整体保留（跨 stop_at 行的前缀不丢失）
                    buf = buf[consumed:]
                    buf_start += consumed
                    if f.tell() >= limit:
                        # 已读到 stop_at：残段截断到 limit（之后的内容属于消息流）
                        tail = buf[:max(0, int(limit) - buf_start)]
                        return lines, buf_start, tail, False
        except Exception as e:
            print(f'[Agent] 正读日志失败: {e}')
        return lines, buf_start, buf, True  # EOF：buf 为文件尾残段

    def _load_initial_from_file(self):
        """冷启动：倒读装入窗口尾部行（窗口行数上限），尾部半行移交流累积器"""
        if not os.path.exists(self._log_file):
            return
        try:
            file_size = os.path.getsize(self._log_file)
            if file_size == 0:
                self._file_read_pos = 0
                self._file_exhausted = True
                self._file_end_pos = 0
                return
            lines, pos, exhausted, tail_partial = self._read_lines_backwards(
                file_size, 1024 ** 3, max_lines=self._window_lines)  # 字符不设限，行数窗口即上限
            self._file_read_pos = pos
            self._file_exhausted = exhausted
            self._file_end_pos = file_size
            if tail_partial:
                self._pend_text, self._pend_start = tail_partial
            if lines:
                self.prepend_lines(lines)
                self._scroll_to_bottom()
                self._schedule_relayout()
        except Exception as e:
            print(f'[Agent] 加载日志文件失败: {e}')

    def load_more_history(self, batch_size=None):
        """向上回读（滚到顶部附近触发）：倒读一批 prepend"""
        if self._loading or self._file_exhausted:
            return 0
        if batch_size is None:
            batch_size = self.READ_BATCH_LINES
        if not os.path.exists(self._log_file) or self._file_read_pos <= 0:
            self._file_exhausted = True
            return 0
        try:
            self._loading = True
            lines, pos, exhausted, _tp = self._read_lines_backwards(
                self._file_read_pos, 1024 ** 3, max_lines=batch_size)
            self._file_read_pos = pos
            self._file_exhausted = exhausted
            if lines:
                return self.prepend_lines(lines)
            return 0
        except Exception as e:
            print(f'[Agent] 加载历史失败: {e}')
            return 0
        finally:
            self._loading = False

    def load_more_tail(self, batch_size=None):
        """向下回读（尾部被驱逐后滚到底触发）：从 _tail_anchor 正读一批 append"""
        if self._loading_tail or self._tail_anchor is None:
            return 0
        if batch_size is None:
            batch_size = self.READ_BATCH_LINES
        try:
            self._loading_tail = True
            lines, next_pos, tail, eof = self._read_lines_forwards(self._tail_anchor, batch_size)
            for text, fs, fe in lines:
                self._append_raw_line(text, fs, fe)
            if eof:
                # 读到文件当前末尾：恢复实时流模式；文件尾半行移交流累积器
                self._tail_anchor = None
                if tail:
                    self._pend_text = tail.rstrip(b'\r').decode('utf-8', errors='replace')
                    self._pend_start = next_pos
            elif lines:
                self._tail_anchor = next_pos
            # append 内部已触发渲染；auto_scroll 时已贴底
            return len(lines)
        except Exception as e:
            print(f'[Agent] 向下回读失败: {e}')
            return 0
        finally:
            self._loading_tail = False

    # ========== 滚动与定位 ==========

    def _scroll_to_bottom(self):
        total = self._get_total_height()
        self._scroll_offset = max(0, total - self._canvas_height)

    def _get_total_height(self):
        # 【根源重构】底部额外增加一个 MARGIN_Y，防止贴底裁剪
        return (self._line_y_prefix[-1] + self.MARGIN_Y) if self._line_y_prefix else 0

    def _get_wrap_width(self):
        if not self._wrap:
            return 0
        return self._canvas_width - self.MARGIN_X * 2

    def _find_first_visible_row(self):
        if not self._line_y_prefix:
            return 0
        idx = bisect.bisect_right(self._line_y_prefix, self._scroll_offset)
        return max(0, idx - 1)

    def _on_scroll(self, action, value, units=None):
        total = self._get_total_height()
        visible = self._canvas_height
        if action == 'moveto':
            self._scroll_offset = float(value) * total
        elif action == 'scroll':
            self._scroll_offset += int(value) * self._line_height * 3
        self._scroll_offset = max(0, min(self._scroll_offset, max(0, total - visible)))
        self._auto_scroll = self._is_at_bottom()
        self._check_preload()
        self._refresh_visible()

    def _on_mousewheel(self, event):
        delta = -event.delta // 120 * self._line_height * 2
        self._scroll_delta(delta)

    def _scroll_delta(self, delta):
        total = self._get_total_height()
        self._scroll_offset = max(0, min(self._scroll_offset + delta,
                                         max(0, total - self._canvas_height)))
        self._auto_scroll = self._is_at_bottom()
        self._check_preload()
        self._refresh_visible()

    def _check_preload(self):
        """双向预读：距窗口头近 → 向上倒读；距窗口尾近且尾部被驱逐 → 向下正读"""
        import time
        if self._loading or self._loading_tail:
            return
        now = time.time() * 1000
        if now - self._last_preload_time < self.PRELOAD_COOLDOWN_MS:
            return
        n = len(self._lines)
        if n == 0:
            return
        first = self._find_first_visible_row()
        up = (first <= self.PRELOAD_UP_ROWS and not self._file_exhausted)
        down = (self._tail_anchor is not None and
                n - first <= self.PRELOAD_DOWN_ROWS + self._canvas_height // self._line_height)
        if up or down:
            self._last_preload_time = now
            if up:
                self.master.after(1, self._async_preload_up)
            if down:
                self.master.after(1, self._async_preload_down)

    def _async_preload_up(self):
        if not self._loading and not self._file_exhausted:
            self.load_more_history()

    def _async_preload_down(self):
        if not self._loading_tail and self._tail_anchor is not None:
            self.load_more_tail()

    def _is_at_bottom(self):
        total = self._get_total_height()
        visible = self._canvas_height
        if total <= visible:
            return True
        return self._scroll_offset >= total - visible - 5

    def _update_scrollbar(self):
        total = self._get_total_height()
        visible = self._canvas_height
        if total <= visible or visible <= 0:
            self.scrollbar.set(0, 1)
        else:
            self.scrollbar.set(self._scroll_offset / total,
                               (self._scroll_offset + visible) / total)

    # ========== 布局重排（resize / 换行切换） ==========

    def _on_resize(self, event):
        self._canvas_width = event.width
        self._canvas_height = event.height
        # [修改] scrollregion 高度即时跟随（防 xview 错位），宽度重排走去抖
        self._update_scrollregion()
        if self._wrap:
            if self._debounce_job is not None:
                self.master.after_cancel(self._debounce_job)
            self._debounce_job = self.master.after(self.RESIZE_DEBOUNCE_MS, self._begin_relayout)
        self._request_render()  # 高度变化仅需重渲染

    def _begin_relayout(self):
        """宽度代际推进：全部排版缓存失效，高度重置为估算占位，
        可见区即时精排校正，其余交后台 idle 链收敛"""
        self._debounce_job = None
        self._width_gen += 1
        self._layouts.clear()
        self._content_max_w = 0
        self._pending_layout = set(range(len(self._lines)))
        self._line_heights.clear()
        for rec in self._lines:
            self._line_heights.append(self._estimate_height(rec[4]))
        self._rebuild_prefix()
        self._refresh_visible(force_rebuild=True)
        self._schedule_relayout()
        self._update_scrollregion()

    def _schedule_relayout(self):
        if self._relayout_job is None:
            self._relayout_job = self.master.after_idle(self._relayout_step)

    def _relayout_step(self):
        """后台排版链：idle 分帧推进未精排行（纯 Python，每批 ~10ms 级），拖拽期间自动让路"""
        self._relayout_job = None
        if self._debounce_job is not None:
            return  # 拖拽中：去抖结束时 _begin_relayout 会重新调度全量
        if not self._pending_layout:
            return
        batch = sorted(self._pending_layout)[:self.RELAYOUT_BATCH]
        dirty_min = None
        for row in batch:
            self._pending_layout.discard(row)
            if row >= len(self._lines):
                continue
            lay = self._get_layout(row)
            h = len(lay['vlines']) * self._line_height
            if h != self._line_heights[row]:
                self._line_heights[row] = h
                dirty_min = row if dirty_min is None else dirty_min
        if dirty_min is not None:
            self._rebuild_prefix_from(dirty_min)
            self._request_render()
        if self._pending_layout:
            self._relayout_job = self.master.after_idle(self._relayout_step)

    def _rebuild_prefix(self):
        self._line_y_prefix = [self.MARGIN_Y]
        for h in self._line_heights:
            self._line_y_prefix.append(self._line_y_prefix[-1] + h)

    def _rebuild_prefix_from(self, row):
        """从 row 起就地修正 suffix（高度校正的增量路径，避免全量重建）"""
        y = self._line_y_prefix[row]
        for i in range(row, len(self._line_heights)):
            y += self._line_heights[i]
            self._line_y_prefix[i + 1] = y

    # ========== 渲染核心 ==========

    def _request_render(self):
        """渲染请求合并：同一帧内多次 append/scroll 只触发一次实际渲染"""
        if self._render_job is None:
            self._render_job = self.master.after_idle(self._flush_render)

    def _flush_render(self):
        self._render_job = None
        self._refresh_visible()

    def _refresh_visible(self, force_rebuild=False):
        # 【防重入】高度校正后的二次收敛渲染只允许一层
        if self._refresh_depth >= 1:
            return
        if not self._lines or self._canvas_height <= 0:
            self._update_scrollbar()
            return
        if force_rebuild:
            # 强制重建（relayout 后 item 的排版已失效），先销毁全部旧 item
            for items in self._visible_items.values():
                for item in items:
                    self.canvas.delete(item)
            self._visible_items.clear()
        # 【定位】prefix 含估算占位，bisect 可能有偏差，回退 LOOKBACK_ROWS 垫背（兼作预渲染）
        start_row = max(0, self._find_first_visible_row() - self.LOOKBACK_ROWS)
        y = self._line_y_prefix[start_row] - self._scroll_offset
        end_row = start_row
        dirty_min = None
        run_to_end = self._auto_scroll  # 贴底模式推进到最后一行（实测链末端校正 offset）
        while end_row < len(self._lines):
            # 【渲染即排版】行进入视野立即精排（缓存），高度即时校正
            lay = self._get_layout(end_row)
            h = len(lay['vlines']) * self._line_height
            if h != self._line_heights[end_row]:
                self._line_heights[end_row] = h
                dirty_min = end_row if dirty_min is None else dirty_min
            # item 管理：已有 → coords 摆位（x 逻辑坐标恒定，xview 平移由 Canvas 承担）
            items = self._visible_items.get(end_row)
            if items is None:
                items = []
                for vi, segs in enumerate(lay['vlines']):
                    for seg in segs:
                        if not seg[1]:
                            continue
                        items.append(self.canvas.create_text(
                            self.MARGIN_X + seg[0], y + vi * self._line_height,
                            text=seg[1], fill=seg[2], font=self._font_obj, anchor='nw'))
                self._visible_items[end_row] = items
            else:
                idx = 0
                for vi, segs in enumerate(lay['vlines']):
                    for seg in segs:
                        if not seg[1]:
                            continue
                        if idx < len(items):
                            self.canvas.coords(items[idx], self.MARGIN_X + seg[0],
                                               y + vi * self._line_height)
                        idx += 1
            y += h
            end_row += 1
            # 【修复】y 已是窗口坐标（初始已减 offset），直接与视口高度比较；
            # 原实现再减一次 offset，深滚时每帧多渲染 offset/行高 数量级的额外行
            if not run_to_end and y > self._canvas_height + 2 * self._line_height:
                break
        # 清理移出渲染范围的 item
        for r in [r for r in self._visible_items if r < start_row or r >= end_row]:
            for item in self._visible_items[r]:
                self.canvas.delete(item)
            del self._visible_items[r]
        # 【贴底校正】y 此刻 = 实测渲染链末端的屏幕坐标；+ offset 还原绝对坐标后锚定 offset
        # 【修复】target 漏加 offset 会把屏幕坐标当绝对坐标，深滚时被 max(0,...) 截到 0，
        # auto_scroll 视图会被拉回顶部
        if run_to_end and end_row >= len(self._lines):
            target = max(0, y + self._scroll_offset + self.MARGIN_Y - self._canvas_height)
            if abs(target - self._scroll_offset) > 0.5:
                self._scroll_offset = target
                dirty_min = dirty_min if dirty_min is not None else len(self._lines)
        if dirty_min is not None:
            self._rebuild_prefix_from(dirty_min)
            self._refresh_depth += 1
            try:
                self._refresh_visible()  # 二次收敛：基于校正后 prefix/offset 重新摆位
            finally:
                self._refresh_depth -= 1
        # 【修复】选择高亮挂渲染管线尾部：每次渲染后同步重建（原 bug：选择矩形不随滚动/布局移动）
        self._update_selection()
        self._update_scrollbar()

    def _update_scrollregion(self):
        """nowrap：scrollregion 宽 = 内容最大宽（驱动横向滚动）；wrap：锁定视口宽（禁用横滚）"""
        if self._wrap:
            self.canvas.configure(scrollregion=(0, 0, self._canvas_width, self._canvas_height))
            self.canvas.xview_moveto(0)
        else:
            w = max(self._content_max_w + self.MARGIN_X * 2, self._canvas_width)
            self.canvas.configure(scrollregion=(0, 0, w, self._canvas_height))

    def _on_xscroll_update(self, first, last):
        """xscrollcommand 回调：内容不超宽时隐藏横向滚动条"""
        try:
            f, l = float(first), float(last)
        except (ValueError, TypeError):
            return
        if f <= 0.0 and l >= 1.0:
            self.xscrollbar.grid_remove()
        else:
            self.xscrollbar.grid(row=1, column=0, sticky='ew')
            self.xscrollbar.set(first, last)

    # ========== 文本选择（字符级） ==========

    def _hit_test(self, x, y):
        """逻辑坐标 (x, y) → (row, char_idx)。x 已含 xview 偏移（canvasx 转换）"""
        if not self._line_y_prefix or not self._lines:
            return None
        idx = bisect.bisect_right(self._line_y_prefix, y)
        row = max(0, min(idx - 1, len(self._lines) - 1))
        lay = self._get_layout(row)
        plain = self._lines[row][4]
        if not lay['vlines']:
            return (row, 0)
        y_in = max(0, y - self._line_y_prefix[row])
        # 【修复】// 结果可能为 float（offset 来自 moveto），作索引需显式取整
        vidx = min(len(lay['vlines']) - 1, int(y_in // self._line_height))
        segs = lay['vlines'][vidx]
        lx = x - self.MARGIN_X
        for seg in segs:
            seg_w = self._text_w(seg[1])
            if lx <= seg[0] + seg_w:
                acc = seg[0]
                for ci, ch in enumerate(seg[1]):
                    cw = self._cw if ord(ch) < 0x80 else self._char_w(ch)
                    if lx <= acc + cw / 2:
                        return (row, seg[3] + ci)
                    acc += cw
                return (row, seg[3] + len(seg[1]))
        return (row, len(plain))

    def _seg_char_x(self, seg, char_in_seg):
        """段内第 char_in_seg 个字符的 x 像素（段起点坐标 + 前缀字符宽累加）"""
        acc = seg[0]
        for ci in range(min(char_in_seg, len(seg[1]))):
            ch = seg[1][ci]
            acc += self._cw if ord(ch) < 0x80 else self._char_w(ch)
        return acc

    def _on_click(self, event):
        # [修复·根源] Canvas 默认无键盘焦点（takefocus 空），<Control-c> 绑定从不触发
        #        （复制失灵的根源）。点击即聚焦，Ctrl+C 事件从此路由到 canvas
        self.canvas.focus_set()
        hit = self._hit_test(self.canvas.canvasx(event.x), event.y + self._scroll_offset)
        if hit:
            self._sel_anchor = hit
            self._sel_active = hit
            self._update_selection()

    def _on_drag(self, event):
        hit = self._hit_test(self.canvas.canvasx(event.x), event.y + self._scroll_offset)
        if hit:
            self._sel_active = hit
            self._update_selection()

    def _on_release(self, event=None):
        pass

    def _sel_range(self):
        """选区排序 → ((r0, c0), (r1, c1))，无效返回 None"""
        if self._sel_anchor is None or self._sel_active is None:
            return None
        a, b = self._sel_anchor, self._sel_active
        if (a[0], a[1]) > (b[0], b[1]):
            a, b = b, a
        n = len(self._lines)
        if a[0] >= n or b[0] >= n:
            return None
        return a, b

    def _update_selection(self):
        """选择高亮重建（渲染管线尾部统一调用，天然跟随滚动/布局/回读）"""
        for item in self._sel_items:
            self.canvas.delete(item)
        self._sel_items = []
        rng = self._sel_range()
        if rng is None:
            return
        (r0, c0), (r1, c1) = rng
        for row in range(r0, r1 + 1):
            lay = self._get_layout(row)
            plain = self._lines[row][4]
            cs = c0 if row == r0 else 0
            ce = min(c1 if row == r1 else len(plain) + 1, len(plain))
            row_y = self._line_y_prefix[row] - self._scroll_offset
            if row_y > self._canvas_height or row_y + len(lay['vlines']) * self._line_height < 0:
                continue  # 视口外不画
            for vi, segs in enumerate(lay['vlines']):
                y = row_y + vi * self._line_height
                for seg in segs:
                    s0, s1 = seg[3], seg[3] + len(seg[1])
                    if s1 <= cs or s0 >= ce:
                        continue  # 段与选区无交集
                    # 交集区间 → 像素矩形
                    x0 = self._seg_char_x(seg, max(0, cs - s0))
                    x1 = self._seg_char_x(seg, min(len(seg[1]), ce - s0))
                    if x1 <= x0:
                        continue
                    rect = self.canvas.create_rectangle(
                        self.MARGIN_X + x0, y, self.MARGIN_X + x1, y + self._line_height,
                        fill='#264f78', outline='')
                    self.canvas.tag_lower(rect)
                    self._sel_items.append(rect)

    def _copy_selection(self, event=None):
        """字符级复制：选区内各行纯文本切片拼接（ANSI 已剥离）"""
        rng = self._sel_range()
        if rng is None:
            return
        (r0, c0), (r1, c1) = rng
        out = []
        for row in range(r0, r1 + 1):
            plain = self._lines[row][4]
            cs = c0 if row == r0 else 0
            ce = min(c1 if row == r1 else len(plain), len(plain))
            out.append(plain[max(0, cs):max(0, ce)])
        text = '\n'.join(out)
        if text:
            self.canvas.clipboard_clear()
            self.canvas.clipboard_append(text)

    # ========== 公开接口 ==========

    def set_wrap(self, wrap):
        if self._wrap != wrap:
            self._wrap = wrap
            if self._debounce_job is not None:
                self.master.after_cancel(self._debounce_job)
                self._debounce_job = None
            self._begin_relayout()  # 统一走 relayout 管线

    def clear(self):
        """清屏：窗口重置为 [清屏时刻, 最新)，历史仍在盘上（向上滚可回读）"""
        for items in self._visible_items.values():
            for item in items:
                self.canvas.delete(item)
        for item in self._sel_items:
            self.canvas.delete(item)
        self._lines.clear()
        self._line_heights.clear()
        self._line_y_prefix = [self.MARGIN_Y]
        self._visible_items.clear()
        self._layouts.clear()
        self._pending_layout.clear()
        self._sel_items.clear()
        self._sel_anchor = None
        self._sel_active = None
        self._scroll_offset = 0
        self._char_count = 0
        self._pend_text = ''
        self._pend_start = None
        self._tail_anchor = None
        try:
            size = os.path.getsize(self._log_file) if os.path.exists(self._log_file) else 0
        except OSError:
            size = 0
        self._file_read_pos = size  # 向上回读从清屏时刻的历史开始
        self._file_end_pos = size
        self._file_exhausted = (size <= 0)
        self._update_scrollbar()

    def destroy(self):
        """取消所有 pending 调度，防止关窗后回调打到已销毁的控件"""
        for attr in ('_debounce_job', '_relayout_job', '_render_job'):
            job = getattr(self, attr)
            if job is not None:
                try:
                    self.master.after_cancel(job)
                except Exception:
                    pass
                setattr(self, attr, None)


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 点击音效
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class ClickPlayer:
    def __init__(self, samplerate=18000, blocksize=128, max_voices=32):
        import numpy as np
        import sounddevice as sd
        self._np = np
        self.samplerate = samplerate
        self.max_voices = max_voices
        n = int(samplerate * 0.012)
        t = np.arange(n, dtype=np.float32) / samplerate
        rng = np.random.default_rng(0)
        click = rng.standard_normal(n).astype(np.float32)
        click *= np.exp(-t * 450).astype(np.float32)
        peak = np.max(np.abs(click))
        if peak > 0:
            click /= peak
        click *= 0.45
        self.click = click
        self.trigger_queue = queue.SimpleQueue()
        self.pos = np.zeros(max_voices, dtype=np.int32)
        self.active = np.zeros(max_voices, dtype=np.bool)
        self.next_voice = 0
        self.stream = sd.OutputStream(
            samplerate=samplerate, channels=2, dtype='float32',
            blocksize=blocksize, latency='low', callback=self._audio_callback)
        self.stream.start()

    def trigger(self):
        self.trigger_queue.put(1)

    def _audio_callback(self, outdata, frames, time_info, status):
        np = self._np
        while True:
            try:
                self.trigger_queue.get_nowait()
            except queue.Empty:
                break
            v = self.next_voice
            self.next_voice = (self.next_voice + 1) % self.max_voices
            self.pos[v] = 0
            self.active[v] = True
        mix = np.zeros(frames, dtype=np.float32)
        click = self.click
        click_len = len(click)
        for v in range(self.max_voices):
            if not self.active[v]:
                continue
            p = int(self.pos[v])
            remaining = click_len - p
            if remaining <= 0:
                self.active[v] = False
                continue
            take = min(frames, remaining)
            mix[:take] += click[p:p + take]
            p += take
            if p >= click_len:
                self.active[v] = False
            self.pos[v] = p
        np.clip(mix, -0.99, 0.99, out=mix)
        outdata[:, 0] = mix
        outdata[:, 1] = mix

    def stop(self):
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flask 服务器线程
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class _ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server = None
        self._ready = threading.Event()

    def run(self):
        try:
            self.server = make_server('127.0.0.1', 9966, agent_server.app, threaded=True)
            self._ready.set()
            self.server.serve_forever()
        except Exception as e:
            self._ready.set()
            print(f'[Agent] 服务线程异常: {e}')

    def shutdown(self):
        if self.server:
            self.server.shutdown()
            self.server.server_close()


# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# GUI 主类
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AgentGUI:
    def __init__(self):
        self.root = tk.Tk()
        self.root.title("PokerAgent")
        self.root.configure(bg=BG)
        self.root.minsize(780, 480)

        gui_cfg = self._load_gui_config()
        if gui_cfg and 'window_geometry' in gui_cfg:
            try:
                self.root.geometry(gui_cfg['window_geometry'])
            except tk.TclError:
                self.root.geometry('1020x660')
        else:
            self.root.geometry('1020x660')

        self._cli_mode = False
        self._server = None
        self._left_width = gui_cfg.get('left_panel_width', 220) if gui_cfg else 220
        self._right_width = gui_cfg.get('right_panel_width', 220) if gui_cfg else 220
        self._log_wrap = gui_cfg.get('log_wrap', True) if gui_cfg else True
        self._window_lines = gui_cfg.get('log_window_lines') if gui_cfg else None  # [新增]
        self._fade_jobs = {}

        try:
            self._click_player = ClickPlayer()
        except Exception:
            self._click_player = None

        self._build_ui()

        # [重构] 日志消费：删除后台线程 + 跨线程 event_generate（Tk 线程安全的灰色地带），
        # 改主线程 15ms after 拉取（Tk 无跨线程唤醒原语，此为唯一稳妥方案，无感级延迟）
        self._log_queue = queue.Queue()
        agent_server.set_gui_log_queue(self._log_queue)
        self.root.after(LOG_POLL_MS, self._drain_log_queue)

        agent_server.permission_mgr.set_callback(self._make_permission_callback())
        agent_server._push_config()

        self._apply_dark_titlebar()
        self._start_server()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _drain_log_queue(self):
        """[新增] 主线程拉取日志队列：单帧限量防长卡（高吞吐时帧间分摊），消息含字节偏移"""
        n = 0
        while n < 2000:
            try:
                msg = self._log_queue.get_nowait()
            except queue.Empty:
                break
            if len(msg) >= 4:
                stream_name, text, fstart, fend = msg[0], msg[1], msg[2], msg[3]
            else:  # 旧格式防御
                stream_name, text, fstart, fend = msg[0], msg[1], None, None
            self.log_canvas.on_file_append(stream_name, text, fstart, fend)
            n += 1
        self.root.after(LOG_POLL_MS, self._drain_log_queue)

    def _toggle_cli(self):
        self._cli_mode = not self._cli_mode
        if self._cli_mode:
            self.cli_frame.pack(fill=tk.X, side=tk.BOTTOM)
            self.btn_cli.configure(text="🖥 转到图形面板模式")
            self.right_title.configure(text="⌨ 命令行模式")
            self.cli_entry.focus_set()
            print('[Agent] 已切换到命令行窗口模式 — 可直接输入指令')
        else:
            self.cli_frame.pack_forget()
            self.btn_cli.configure(text="⌨ 转到命令行窗口模式")
            self.right_title.configure(text="📋 控制台日志")
            print('[Agent] 已切换到图形面板模式')

    def _select_dir(self):
        path = filedialog.askdirectory(initialdir=agent_server.WORK_DIR, title="选择工作目录")
        if path:
            agent_server.WORK_DIR = path
            agent_server.TRASH_DIR = os.path.join(path, '.agent_trash')
            self.lbl_dir.configure(text=path)
            agent_server._push_config()
            print(f'[Agent] 工作目录已更改为: {path}')

    def _toggle_permission(self):
        enabled = self.var_perm.get()
        agent_server.permission_mgr.enabled = enabled
        agent_server._push_config()
        print(f'[Agent] 目录限制{"已启用" if enabled else "已禁用"}')

    def _clear_always_allow(self):
        agent_server.permission_mgr.reset_session()
        self.lbl_allow_count.configure(text="始终允许: 0 条")
        print('[Agent] 已清除始终允许列表')

    def _toggle_clipboard(self):
        agent_server.clipboard_mode = self.var_clipboard.get()
        agent_server._push_config()
        print(f'[Agent] 剪贴板读取模式{"已启用" if agent_server.clipboard_mode else "已禁用"}')

    def _toggle_exec(self):
        agent_server.exec_enabled = self.var_exec.get()
        agent_server._push_config()
        print(f'[Agent] 系统命令执行{"已启用" if agent_server.exec_enabled else "已禁用"}')

    def _toggle_shell(self):
        agent_server.shell_type = self.var_shell.get()
        agent_server._push_config()
        print(f'[Agent] exec 终端已切换为: {agent_server.shell_type}')

    def _make_permission_callback(self):
        gui_ref = self

        def callback(cmd, filepath):
            event = threading.Event()
            result = [False]

            def ask():
                dialog = tk.Toplevel(gui_ref.root)
                dialog.title("⚠ 高危系统命令拦截" if cmd == '高危命令拦截' else "⚠ 路径权限请求")
                dialog.configure(bg=BG)
                dialog.resizable(False, False)
                try:
                    dialog.attributes('-topmost', True)
                except tk.TclError:
                    pass
                dialog.lift()
                dialog.focus_force()
                gui_ref.root.update_idletasks()
                dw, dh = 440, 260
                rx = gui_ref.root.winfo_x() + (gui_ref.root.winfo_width() - dw) // 2
                ry = gui_ref.root.winfo_y() + (gui_ref.root.winfo_height() - dh) // 2
                dialog.geometry(f'{dw}x{dh}+{rx}+{ry}')

                tk.Label(dialog, text="⚠", bg=BG, fg=YELLOW,
                         font=('Microsoft YaHei UI', 28)).pack(pady=(14, 2))
                info = tk.Frame(dialog, bg=HEADER)
                info.pack(fill=tk.X, padx=16, pady=8)
                if cmd == '高危命令拦截':
                    tk.Label(dialog, text="即将执行高危系统命令", bg=BG, fg=TXT, font=FONT_UI_B).pack()
                    tk.Label(info, text="拦截命令:", bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='w').pack(fill=tk.X, padx=10, pady=(6, 0))
                    tk.Label(info, text=f"{filepath}", bg=HEADER, fg=RED, font=FONT_MONO, anchor='w', wraplength=400).pack(fill=tk.X, padx=10, pady=(0, 6))
                else:
                    tk.Label(dialog, text="路径超出工作目录", bg=BG, fg=TXT, font=FONT_UI_B).pack()
                    tk.Label(info, text=f"指令: {cmd}", bg=HEADER, fg=RED, font=FONT_MONO, anchor='w').pack(fill=tk.X, padx=10, pady=(2, 2))
                    tk.Label(info, text=f"目标: {filepath}", bg=HEADER, fg=TXT, font=FONT_MONO, anchor='w', wraplength=400).pack(fill=tk.X, padx=10)
                    tk.Label(info, text=f"工作目录: {agent_server.WORK_DIR}", bg=HEADER, fg=TXT2, font=('Consolas', 9), anchor='w').pack(fill=tk.X, padx=10, pady=(2, 6))

                bf = tk.Frame(dialog, bg=BG)
                bf.pack(fill=tk.X, padx=16, pady=(0, 14))

                def close(val):
                    result[0] = val
                    event.set()
                    dialog.destroy()

                tk.Button(bf, text="✕ 拒绝", command=lambda: close(False), bg='#3d1f1f', fg=RED,
                          activebackground='#4d2525', activeforeground=RED, font=FONT_UI, bd=0,
                          padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
                tk.Button(bf, text="✓ 允许一次", command=lambda: close(True), bg='#1f3d1f', fg=GREEN,
                          activebackground='#254d25', activeforeground=GREEN, font=FONT_UI, bd=0,
                          padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
                if cmd != '高危命令拦截':
                    tk.Button(bf, text="✓ 始终允许", command=lambda: close('always'), bg='#2a2a1a', fg=BLUE,
                              activebackground='#3a3a2a', activeforeground=BLUE, font=FONT_UI, bd=0,
                              padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
                dialog.protocol("WM_DELETE_WINDOW", lambda: close(False))

            gui_ref.root.after(1, ask)
            event.wait(timeout=120)
            if event.is_set():
                count = len(agent_server.permission_mgr._always_allow)
                gui_ref.root.after_idle(lambda: gui_ref.lbl_allow_count.configure(text=f"始终允许: {count} 条"))
            return result[0]

        return callback

    def _load_gui_config(self):
        if not os.path.exists(GUI_CONFIG_FILE):
            return None
        try:
            with open(GUI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _save_gui_config(self):
        config = {
            'window_geometry': self.root.geometry(),
            'left_panel_width': self._left_width,
            'right_panel_width': self._right_width,
            'log_wrap': self._log_wrap,
            'log_window_lines': self.log_canvas._window_lines,  # [新增] 日志内存窗口行数
        }
        try:
            with open(GUI_CONFIG_FILE, 'w', encoding='utf-8') as f:
                json.dump(config, f, ensure_ascii=False, indent=2)
        except Exception as e:
            print(f'[Agent] GUI 配置保存失败: {e}')

    def _apply_dark_titlebar(self):
        try:
            import ctypes
            self.root.update_idletasks()
            hwnd = ctypes.windll.user32.GetParent(self.root.winfo_id())
            value = ctypes.c_int(2)
            ctypes.windll.dwmapi.DwmSetWindowAttribute(hwnd, 20, ctypes.byref(value), ctypes.sizeof(value))
        except Exception:
            pass

    def _on_close(self):
        self._save_gui_config()
        if self._server:
            self._server.shutdown()
        if self._click_player:
            self._click_player.stop()
        self.log_canvas.destroy()  # [新增] 取消 LogCanvas pending 调度（去抖/排版链/渲染合并）
        self.root.destroy()

    def _build_ui(self):
        self._build_status_bar()
        self.left = tk.Frame(self.root, bg=PANEL, width=self._left_width)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)
        self.left_grip = tk.Frame(self.root, bg=BORDER, width=3, cursor='sb_h_double_arrow')
        self.left_grip.pack(side=tk.LEFT, fill=tk.Y)
        self.center = tk.Frame(self.root, bg=BG)
        self.center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.right_grip = tk.Frame(self.root, bg=BORDER, width=3, cursor='sb_h_double_arrow')
        self.right_grip.pack(side=tk.LEFT, fill=tk.Y)
        self.right_panel = tk.Frame(self.root, bg=PANEL, width=self._right_width)
        self.right_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.right_panel.pack_propagate(False)
        self._build_left()
        self._build_center()
        self._build_right_panel()
        self._bind_grips()

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=HEADER, height=26)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)
        self.status_dot = tk.Label(bar, text="●", bg=HEADER, fg=GREEN, font=FONT_MONO)
        self.status_dot.pack(side=tk.LEFT, padx=(10, 4))
        self.status_text = tk.Label(bar, text="服务运行中", bg=HEADER, fg=TXT2,
                                    font=FONT_MONO, anchor='w')
        self.status_text.pack(side=tk.LEFT)
        self.port_text = tk.Label(bar, text="http://127.0.0.1:9966", bg=HEADER, fg=TXT2,
                                  font=FONT_MONO, anchor='e')
        self.port_text.pack(side=tk.RIGHT, padx=10)

    def _build_left(self):
        f = self.left
        tk.Frame(f, bg=BLUE, height=2).pack(fill=tk.X)
        tk.Label(f, text="⚙ 控制面板", bg=PANEL, fg=TXT, font=FONT_TITLE).pack(anchor='w', padx=16, pady=(18, 4))
        self._sep(f)
        self.btn_cli = self._btn(f, "⌨ 转到命令行窗口模式", self._toggle_cli, fg=BLUE, bold=True)
        self.btn_cli.pack(fill=tk.X, padx=12, pady=(2, 4))
        self._sep(f)
        tk.Label(f, text="📂 工作目录", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.lbl_dir = tk.Label(f, text=agent_server.WORK_DIR, bg=PANEL, fg=TXT,
                                font=('Consolas', 9), wraplength=180, justify='left', anchor='w')
        self.lbl_dir.pack(anchor='w', padx=16, pady=(0, 6))
        self._btn(f, "选择工作目录...", self._select_dir).pack(fill=tk.X, padx=12, pady=(0, 4))
        self._sep(f)
        tk.Label(f, text="🔧 服务", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 6))
        self._btn(f, "重启服务", self._restart_server).pack(fill=tk.X, padx=12, pady=2)
        self._btn(f, "清空日志", self._clear_log).pack(fill=tk.X, padx=12, pady=2)
        self._sep(f)
        tk.Label(f, text="🔒 权限控制", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_perm = tk.BooleanVar(value=agent_server.permission_mgr.enabled)
        self.chk_perm = tk.Checkbutton(f, text="启用目录限制", variable=self.var_perm,
                                       bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL,
                                       activeforeground=TXT, font=FONT_UI,
                                       command=self._wrap_cmd(self._toggle_permission))
        self.chk_perm.pack(anchor='w', padx=20)
        self._btn(f, "清除始终允许列表", self._clear_always_allow).pack(fill=tk.X, padx=12, pady=2)
        self.lbl_allow_count = tk.Label(f, text="", bg=PANEL, fg=TXT2, font=('Consolas', 9), anchor='w')
        self.lbl_allow_count.pack(anchor='w', padx=20, pady=(0, 4))
        self._sep(f)
        tk.Label(f, text="📋 文件读取", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_clipboard = tk.BooleanVar(value=agent_server.clipboard_mode)
        self.chk_clipboard = tk.Checkbutton(f, text="读取文件时使用剪贴板API", variable=self.var_clipboard,
                                            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL,
                                            activeforeground=TXT, font=FONT_UI,
                                            command=self._wrap_cmd(self._toggle_clipboard))
        self.chk_clipboard.pack(anchor='w', padx=20)
        self.var_exec = tk.BooleanVar(value=agent_server.exec_enabled)
        self.chk_exec = tk.Checkbutton(f, text="允许执行系统命令", variable=self.var_exec,
                                       bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL,
                                       activeforeground=TXT, font=FONT_UI,
                                       command=self._wrap_cmd(self._toggle_exec))
        self.chk_exec.pack(anchor='w', padx=20)
        tk.Label(f, text="exec 终端类型", bg=PANEL, fg=TXT2, font=('Consolas', 9)).pack(anchor='w', padx=20, pady=(6, 0))
        self.var_shell = tk.StringVar(value=agent_server.shell_type)
        shell_frame = tk.Frame(f, bg=PANEL)
        shell_frame.pack(anchor='w', padx=20, pady=(0, 2))
        tk.Radiobutton(shell_frame, text="PowerShell", variable=self.var_shell, value='powershell',
                       bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
                       font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)).pack(side=tk.LEFT)
        tk.Radiobutton(shell_frame, text="CMD", variable=self.var_shell, value='cmd',
                       bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
                       font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)).pack(side=tk.LEFT, padx=(10, 0))

    def _build_center(self):
        f = self.center
        hdr = tk.Frame(f, bg=BG, height=38)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self.right_title = tk.Label(hdr, text="📋 控制台日志", bg=BG, fg=TXT,
                                    font=('Microsoft YaHei UI', 11), anchor='w', padx=12)
        self.right_title.pack(side=tk.LEFT, fill=tk.Y)
        self.btn_wrap = tk.Label(hdr, text="🔁 换行: 开", bg=BG, fg=BLUE if self._log_wrap else TXT2,
                                 font=('Microsoft YaHei UI', 9), cursor='hand2', padx=8)
        self.btn_wrap.pack(side=tk.RIGHT, padx=(0, 12))
        self.btn_wrap.bind('<Button-1>', lambda e: self._toggle_log_wrap())
        log_frame = tk.Frame(f, bg=BG)
        log_frame.pack(fill=tk.BOTH, expand=True)
        # [修改] classify 注入 + 窗口行数可配置（gui_config.json 的 log_window_lines）
        self.log_canvas = LogCanvas(log_frame, log_file=agent_server.LOG_FILE,
                                    wrap=self._log_wrap, window_lines=self._window_lines,
                                    classify=classify_log_line)
        self.cli_frame = tk.Frame(f, bg=HEADER)
        self.cli_prompt = tk.Label(self.cli_frame, text=" Agent > ", bg=HEADER, fg=BLUE,
                                   font=FONT_MONO_B, padx=8)
        self.cli_prompt.pack(side=tk.LEFT)
        self.cli_entry = tk.Entry(self.cli_frame, bg=HEADER, fg=TXT, font=FONT_MONO, bd=0,
                                  insertbackground=TXT, highlightthickness=0)
        self.cli_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=7)
        self.cli_entry.bind('<Return>', self._on_cli_enter)

    def _build_right_panel(self):
        f = self.right_panel
        tk.Frame(f, bg=RED, height=2).pack(fill=tk.X)
        tk.Label(f, text="⚡ 任务控制", bg=PANEL, fg=TXT, font=FONT_TITLE).pack(anchor='w', padx=16, pady=(18, 4))
        self._sep(f)
        self.btn_kill_discard = self._make_momentary_btn(f, "⛔ 终止当前任务\n并丢弃", self._on_kill_discard)
        self.btn_kill_discard.pack(fill=tk.X, padx=12, pady=4)
        self.btn_kill_done = self._make_momentary_btn(f, "⛔ 终止当前任务\n并返回 done", self._on_kill_done)
        self.btn_kill_done.pack(fill=tk.X, padx=12, pady=4)
        self._sep(f)
        self.btn_pause = self._make_toggle_btn(f, "⏸ 暂停任务队列", self._on_pause_on, self._on_pause_off)
        self.btn_pause.pack(fill=tk.X, padx=12, pady=4)

    def _bind_grips(self):
        self.left_grip.bind('<B1-Motion>', self._on_left_grip_drag)
        self.right_grip.bind('<B1-Motion>', self._on_right_grip_drag)

    def _on_left_grip_drag(self, event):
        new_w = event.x_root - self.root.winfo_rootx()
        new_w = max(160, min(new_w, 400))
        self.left.configure(width=new_w)
        self._left_width = new_w

    def _on_right_grip_drag(self, event):
        win_right = self.root.winfo_rootx() + self.root.winfo_width()
        new_w = win_right - event.x_root() - 3
        new_w = max(160, min(new_w, 400))
        self.right_panel.configure(width=new_w)
        self._right_width = new_w

    def _make_momentary_btn(self, parent, text, command):
        btn = tk.Button(parent, text=text, bg=BTN, fg=TXT, activebackground=RED,
                        activeforeground=TXT, font=FONT_UI, bd=0, padx=12, pady=10,
                        cursor='hand2', justify='center')
        btn.bind('<ButtonPress-1>', lambda e, b=btn, c=command: self._on_momentary_press(b, c))
        btn.bind('<ButtonRelease-1>', lambda e, b=btn: self._on_momentary_release(b))
        return btn

    def _on_momentary_press(self, btn, command):
        self._play_click()
        btn.configure(bg=RED)
        command()

    def _on_momentary_release(self, btn):
        self._fade_bg(btn, RED, BTN)

    def _make_toggle_btn(self, parent, text, cmd_on, cmd_off):
        btn = tk.Button(parent, text=text, bg=BTN, fg=TXT, activebackground=BTN_H,
                        activeforeground=TXT, font=FONT_UI, bd=0, padx=12, pady=10, cursor='hand2')
        btn._locked = False
        btn.bind('<Button-1>', lambda e, b=btn, on=cmd_on, off=cmd_off: self._on_toggle_click(b, on, off))
        return btn

    def _on_toggle_click(self, btn, cmd_on, cmd_off):
        self._play_click()
        btn._locked = not btn._locked
        if btn._locked:
            btn.configure(bg=RED)
            cmd_on()
        else:
            cmd_off()
            self._fade_bg(btn, RED, BTN)

    def _fade_bg(self, widget, from_color, to_color, duration_ms=300, steps=10):
        key = id(widget)
        if key in self._fade_jobs:
            for j in self._fade_jobs[key]:
                self.root.after_cancel(j)
        jobs = []
        interval = max(1, duration_ms // steps)
        for i in range(steps + 1):
            t = i / steps
            c = _lerp_color(from_color, to_color, t)
            jobs.append(self.root.after(i * interval, lambda c=c, w=widget: w.configure(bg=c)))
        self._fade_jobs[key] = jobs
        self.root.after(duration_ms + 50, lambda k=key: self._fade_jobs.pop(k, None))

    def _play_click(self):
        if self._click_player:
            self._click_player.trigger()

    def _wrap_cmd(self, fn):
        return lambda: (self._play_click(), fn())

    def _on_kill_discard(self):
        if agent_server.request_kill('discard'):
            print('[Agent] ⛔ 已请求终止当前任务并丢弃')
        else:
            print('[Agent] ⛔ 当前没有正在执行的任务')

    def _on_kill_done(self):
        if agent_server.request_kill('done'):
            print('[Agent] ⛔ 已请求终止当前任务并返回已有输出')
        else:
            print('[Agent] ⛔ 当前没有正在执行的任务')

    def _on_pause_on(self):
        agent_server.request_pause()
        print('[Agent] ⏸ 任务队列已暂停')

    def _on_pause_off(self):
        agent_server.request_resume()
        print('[Agent] ▶ 任务队列已恢复')

    def _sep(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=10)

    def _btn(self, parent, text, command, fg=TXT, bold=False, disabled=False):
        weight = 'bold' if bold else 'normal'
        state = tk.DISABLED if disabled else tk.NORMAL
        cursor = 'arrow' if disabled else 'hand2'
        btn_fg = DISABLED_FG if disabled else fg
        btn = tk.Button(parent, text=text,
                        command=self._wrap_cmd(command) if not disabled else command,
                        bg=BTN, fg=btn_fg, activebackground=BTN_H, activeforeground=btn_fg,
                        font=('Microsoft YaHei UI', 10, weight), bd=0, padx=12, pady=8,
                        anchor='w', cursor=cursor, state=state)
        if not disabled:
            btn.bind('<Enter>', lambda e, b=btn: b.configure(bg=BTN_H))
            btn.bind('<Leave>', lambda e, b=btn: b.configure(bg=BTN))
        return btn

    # ========== 日志系统 ==========

    def _toggle_log_wrap(self):
        self._log_wrap = not self._log_wrap
        self.log_canvas.set_wrap(self._log_wrap)
        if self._log_wrap:
            self.btn_wrap.configure(text="🔁 换行: 开", fg=BLUE)
            print('[Agent] 日志换行已开启')
        else:
            self.btn_wrap.configure(text="🔁 换行: 关", fg=TXT2)
            print('[Agent] 日志换行已关闭')

    # ========== 服务器管理 ==========

    def _start_server(self):
        try:
            self._server = _ServerThread()
            self._server.start()
            self._server._ready.wait(timeout=5)
            if self._server._ready.is_set():
                self.status_dot.configure(fg=GREEN)
                self.status_text.configure(text="服务运行中")
                print(f'[Agent] 服务已启动: http://127.0.0.1:9966')
                print(f'[Agent] 工作目录: {agent_server.WORK_DIR}')
            else:
                self.status_dot.configure(fg=YELLOW)
                self.status_text.configure(text="启动超时")
        except OSError as e:
            self.status_dot.configure(fg=RED)
            err = str(e).lower()
            if 'already in use' in err or '10048' in err:
                self.status_text.configure(text="端口 9966 已被占用")
                print('[Agent] 端口 9966 已被占用，请先关闭 agent_server.py')
            else:
                self.status_text.configure(text="启动失败")
                print(f'[Agent] 启动失败: {e}')
        except Exception as e:
            self.status_dot.configure(fg=RED)
            self.status_text.configure(text="启动失败")
            print(f'[Agent] 启动失败: {e}')

    def _restart_server(self):
        if messagebox.askyesno("重启服务", "确定要重启 Agent 服务吗？"):
            print('[Agent] 正在暴力重启服务...')
            old_server = self._server
            self._server = None
            print('[Agent] 正在关闭旧服务...')
            if old_server:
                try:
                    old_server.shutdown()
                    print('[Agent] 旧服务已发送关闭信号')
                except Exception as e:
                    print(f'[Agent] 关闭旧服务时发生异常: {e}')
            self._start_server()

    def _clear_log(self):
        self.log_canvas.clear()
        print('[Agent] 日志已清空')

    def _on_cli_enter(self, event):
        cmd = self.cli_entry.get().strip()
        if not cmd:
            return
        # [修改] 回显走标准日志管道（持久化 + 字节偏移 + 窗口化一体化），
        # 不再本地直接 append（本地行无文件区间，会破坏窗口偏移链）
        print(f'Agent > {cmd}')
        self.cli_entry.delete(0, tk.END)
        result = agent_server.execute_line(cmd)
        if result:
            print(result)
        else:
            print('（空指令或注释）')

    def run(self):
        self.root.mainloop()


if __name__ == '__main__':
    gui = AgentGUI()
    gui.run()

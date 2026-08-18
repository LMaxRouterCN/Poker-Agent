"""PokerAgent - GUI 控制台
用法：python agent_gui.py（不要和 agent_server.py 同时运行）
依赖：flask, flask-cors, werkzeug, numpy, sounddevice（与 agent_server.py 相同）
"""
import tkinter as tk
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
# LogCanvas
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class LogCanvas:
    MAX_CHARS = 500 * 1024
    PRELOAD_THRESHOLD_RATIO = 0.15
    LOAD_BATCH_LINES = 50
    PRELOAD_COOLDOWN_MS = 200
    MARGIN_X = 12
    MARGIN_Y = 8
    LINE_SPACING = 2

    def __init__(self, master, log_file, wrap=True):
        self.master = master
        self._log_file = log_file
        self._wrap = wrap
        self._font = FONT_MONO
        self._font_bold = FONT_MONO_B
        self._line_height = 18
        self._char_width = 8
        self._lines = deque()
        self._char_count = 0
        self._line_heights = deque()
        self._line_y_prefix = [self.MARGIN_Y]  # 【根源重构】初始值为 MARGIN_Y
        self._visible_items = {}
        self._scroll_offset = 0
        self._auto_scroll = True
        self._file_read_pos = 0
        self._file_exhausted = False
        self._loading = False
        self._last_preload_time = 0
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
        self.scrollbar = tk.Scrollbar(self.master, command=self._on_scroll, bg=BTN, troughcolor=BG, bd=0, activebackground=BTN_H)
        self.scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.canvas = tk.Canvas(self.master, bg=BG, bd=0, highlightthickness=0, cursor='arrow')
        self.canvas.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)
        self.canvas.bind('<Configure>', self._on_resize)
        self.canvas.bind('<MouseWheel>', self._on_mousewheel)
        self.canvas.bind('<Button-4>', lambda e: self._scroll_delta(-60))
        self.canvas.bind('<Button-5>', lambda e: self._scroll_delta(60))
        self.canvas.bind('<Button-1>', self._on_click)
        self.canvas.bind('<B1-Motion>', self._on_drag)
        self.canvas.bind('<ButtonRelease-1>', self._on_release)
        self.canvas.bind('<Control-c>', self._copy_selection)
        self.canvas.bind('<Leave>', self._on_release)

    def _measure_font(self):
        item = self.canvas.create_text(0, 0, text='Mg中文', font=self._font, anchor='nw')
        bbox = self.canvas.bbox(item)
        self.canvas.delete(item)
        if bbox:
            self._char_width = (bbox[2] - bbox[0]) / 4
            self._line_height = bbox[3] - bbox[1] + self.LINE_SPACING
        else:
            self._char_width = 8
            self._line_height = 18

    def _on_resize(self, event):
        self._canvas_width = event.width
        self._canvas_height = event.height
        self._recalculate_all_heights()
        # 【修复】窗口大小改变，必须强制重建以应用新的换行宽度
        self._refresh_visible(force_rebuild=True)

    def _recalculate_all_heights(self):
        self._line_heights.clear()
        width = self._get_wrap_width()
        for text, _, _ in self._lines:
            h = self._calc_line_height(text, width)
            self._line_heights.append(h)
        self._rebuild_y_prefix()

    def _calc_line_height(self, text, width):
        if not self._wrap or width <= 0:
            return self._line_height
        chars_per_line = max(1, width // self._char_width)
        lines_needed = max(1, (len(text) + chars_per_line - 1) // chars_per_line)
        return lines_needed * self._line_height

    def _rebuild_y_prefix(self):
        # 【根源重构】将 MARGIN_Y 纳入逻辑坐标系，第一行的起始 Y 即为 MARGIN_Y
        self._line_y_prefix = [self.MARGIN_Y]
        for h in self._line_heights:
            self._line_y_prefix.append(self._line_y_prefix[-1] + h)

    def _get_wrap_width(self):
        if not self._wrap:
            return 0
        return self._canvas_width - self.MARGIN_X * 2

    def append_line(self, text, tag='txt', parts=None):
        base_color = self._tag_colors.get(tag, TXT)
        self._lines.append((text, base_color, parts))
        self._char_count += len(text)
        width = self._get_wrap_width()
        h = self._calc_line_height(text, width)
        self._line_heights.append(h)
        self._line_y_prefix.append(self._line_y_prefix[-1] + h)
        self._trim_top()
        self._update_scrollbar()
        if self._auto_scroll:
            self._scroll_to_bottom()
        self._refresh_visible()

    def prepend_lines(self, lines_data):
        if not lines_data:
            return 0
        insert_height = 0
        width = self._get_wrap_width()
        for text, tag, parts in reversed(lines_data):
            base_color = self._tag_colors.get(tag, TXT)
            self._lines.appendleft((text, base_color, parts))
            self._char_count += len(text)
            h = self._calc_line_height(text, width)
            self._line_heights.appendleft(h)
            insert_height += h
        self._rebuild_y_prefix()
        self._trim_bottom()
        self._scroll_offset += insert_height
        # 【根源修复】清空字典前，必须先删除 Canvas 上的实际 Item，否则会产生残影
        for items in self._visible_items.values():
            for item in items:
                self.canvas.delete(item)
        self._visible_items.clear()
        self._refresh_visible()
        return len(lines_data)

    def _trim_top(self):
        while self._char_count > self.MAX_CHARS and len(self._lines) > 0:
            text, _, _ = self._lines.popleft()
            self._char_count -= len(text)
            if self._line_heights:
                self._line_heights.popleft()
            self._rebuild_y_prefix()

    def _trim_bottom(self):
        while self._char_count > self.MAX_CHARS and len(self._lines) > 0:
            text, _, _ = self._lines.pop()
            self._char_count -= len(text)
            if self._line_heights:
                self._line_heights.pop()
            self._rebuild_y_prefix()
        self._update_scrollbar()

    def _load_initial_from_file(self):
        if not os.path.exists(self._log_file):
            return
        try:
            file_size = os.path.getsize(self._log_file)
            if file_size == 0:
                return
            self._file_read_pos = file_size
            target_chars = self.MAX_CHARS
            read_chars = 0
            lines_data = []
            with open(self._log_file, 'r', encoding='utf-8', errors='replace') as f:
                f.seek(0, 2)
                pos = f.tell()
                chunk_size = 4096
                while pos > 0 and read_chars < target_chars:
                    read_size = min(chunk_size, pos)
                    pos -= read_size
                    f.seek(pos)
                    chunk = f.read(read_size)
                    lines = chunk.split('\n')
                    for line in reversed(lines):
                        if not line:
                            continue
                        line = line.rstrip('\r')
                        if line:
                            parts = None
                            if '\x1b[' in line:
                                parts = _parse_ansi_to_parts(line)
                            lines_data.append((line, 'txt', parts))
                            read_chars += len(line)
                        if read_chars >= target_chars:
                            break
            if lines_data:
                self.prepend_lines(lines_data)
                self._file_exhausted = (pos <= 0)
        except Exception as e:
            print(f'[Agent] 加载日志文件失败: {e}')

    def load_more_history(self, batch_size=None):
        if self._loading or self._file_exhausted:
            return 0
        if batch_size is None:
            batch_size = self.LOAD_BATCH_LINES
        if not os.path.exists(self._log_file):
            self._file_exhausted = True
            return 0
        try:
            file_size = os.path.getsize(self._log_file)
            if self._file_read_pos <= 0:
                self._file_exhausted = True
                return 0
            self._loading = True
            with open(self._log_file, 'r', encoding='utf-8', errors='replace') as f:
                read_pos = max(0, self._file_read_pos - 16384)
                if read_pos == 0:
                    f.seek(0)
                else:
                    f.seek(read_pos)
                chunk = f.read(self._file_read_pos - read_pos)
                lines = chunk.split('\n')
                lines_data = []
                lines_to_take = min(batch_size, len(lines))
                for i, line in enumerate(reversed(lines)):
                    if i >= lines_to_take:
                        break
                    line = line.rstrip('\r')
                    if line:
                        parts = None
                        if '\x1b[' in line:
                            parts = _parse_ansi_to_parts(line)
                        lines_data.append((line, 'txt', parts))
                if lines_data:
                    self._file_read_pos = read_pos
                    count = self.prepend_lines(lines_data)
                    self._file_exhausted = (self._file_read_pos <= 0)
                    return count
                else:
                    self._file_read_pos = read_pos
                    self._file_exhausted = (self._file_read_pos <= 0)
                    return 0
        except Exception as e:
            print(f'[Agent] 加载历史失败: {e}')
            return 0
        finally:
            self._loading = False

    def _scroll_to_bottom(self):
        total = self._get_total_height()
        visible = self._canvas_height
        self._scroll_offset = max(0, total - visible)

    def _get_total_height(self):
        # 【根源重构】底部额外增加一个 MARGIN_Y，防止贴底裁剪
        return (self._line_y_prefix[-1] + self.MARGIN_Y) if self._line_y_prefix else 0

    def _find_first_visible_row(self):
        if not self._line_y_prefix:
            return 0
        # 【优化】bisect_right 找到第一个 Y > offset 的行，其前一行即为可见行
        idx = bisect.bisect_right(self._line_y_prefix, self._scroll_offset)
        return max(0, idx - 1)

    def _refresh_visible(self, force_rebuild=False):
        if not self._lines:
            return
        visible_height = self._canvas_height
        if visible_height <= 0:
            return

        # 【新增】如果强制重建（如窗口Resize/切换换行），先清空所有旧Item
        if force_rebuild:
            for items in self._visible_items.values():
                for item in items:
                    self.canvas.delete(item)
            self._visible_items.clear()

        start_row = self._find_first_visible_row()
        end_row = start_row
        while end_row < len(self._lines):
            y_start = self._line_y_prefix[end_row] if end_row < len(self._line_y_prefix) else 0
            if y_start > self._scroll_offset + visible_height:
                break
            end_row += 1

        # 1. 清理移出视野的 Item
        to_remove = [r for r in self._visible_items if r < start_row or r >= end_row]
        for r in to_remove:
            for item in self._visible_items[r]:
                self.canvas.delete(item)
            del self._visible_items[r]

        wrap_width = self._get_wrap_width()

        # 2. 处理视野内的 Item
        for i in range(start_row, min(end_row, len(self._lines))):
            if i >= len(self._line_y_prefix):
                continue
            text, base_color, parts = self._lines[i]
            # 【根源重构】坐标系已统一，直接相减，彻底消除魔法数字
            y = self._line_y_prefix[i] - self._scroll_offset

            if i in self._visible_items and not force_rebuild:
                # 【根源修复】已存在的 Item，直接移动坐标，而不是跳过！
                for item in self._visible_items[i]:
                    self.canvas.coords(item, self.MARGIN_X, y)
            else:
                # 不存在，或者强制重建，则创建新 Item
                items = []
                if parts:
                    item = self.canvas.create_text(
                        self.MARGIN_X, y,
                        text=''.join(p[0] for p in parts),
                        font=self._font,
                        fill=parts[0][1] if parts else base_color,
                        anchor='nw',
                        width=wrap_width if wrap_width else 0
                    )
                    items.append(item)
                else:
                    item = self.canvas.create_text(
                        self.MARGIN_X, y,
                        text=text,
                        font=self._font,
                        fill=base_color,
                        anchor='nw',
                        width=wrap_width if wrap_width else 0
                    )
                    items.append(item)
                self._visible_items[i] = items

        self._update_scrollbar()

    def _on_scroll(self, action, value, units=None):
        total = self._get_total_height()
        visible = self._canvas_height
        if action == 'moveto':
            self._scroll_offset = float(value) * total
        elif action == 'scroll':
            self._scroll_offset += int(value) * self._line_height * 3
        self._scroll_offset = max(0, min(self._scroll_offset, total - visible))
        self._auto_scroll = self._is_at_bottom()
        self._check_preload()
        self._refresh_visible()

    def _on_mousewheel(self, event):
        delta = -event.delta // 120 * self._line_height * 2
        self._scroll_delta(delta)

    def _scroll_delta(self, delta):
        total = self._get_total_height()
        visible = self._canvas_height
        self._scroll_offset = max(0, min(self._scroll_offset + delta, total - visible))
        self._auto_scroll = self._is_at_bottom()
        self._check_preload()
        self._refresh_visible()

    def _check_preload(self):
        if self._loading or self._file_exhausted:
            return
        import time
        now = time.time() * 1000
        if now - self._last_preload_time < self.PRELOAD_COOLDOWN_MS:
            return
        total = self._get_total_height()
        if total <= 0:
            return
        ratio = self._scroll_offset / total if total > 0 else 0
        if ratio < self.PRELOAD_THRESHOLD_RATIO:
            self._last_preload_time = now
            self.master.after(1, self._async_preload)

    def _async_preload(self):
        if not self._loading and not self._file_exhausted:
            self.load_more_history()

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
            self.scrollbar.set(self._scroll_offset / total, (self._scroll_offset + visible) / total)

    def _on_click(self, event):
        self._sel_anchor = self._y_to_row(event.y + self._scroll_offset)
        self._sel_active = self._sel_anchor
        self._update_selection()

    def _on_drag(self, event):
        row = self._y_to_row(event.y + self._scroll_offset)
        if row is not None:
            self._sel_active = row
            self._update_selection()

    def _on_release(self, event=None):
        pass

    def _y_to_row(self, y):
        if not self._line_y_prefix:
            return None
        # 【根源重构】将 Canvas 相对坐标转换为绝对逻辑坐标
        idx = bisect.bisect_right(self._line_y_prefix, y)
        return max(0, min(idx - 1, len(self._lines) - 1))

    def _update_selection(self):
        for item in self._sel_items:
            self.canvas.delete(item)
        self._sel_items = []
        if self._sel_anchor is None or self._sel_active is None:
            return
        start = min(self._sel_anchor, self._sel_active)
        end = max(self._sel_anchor, self._sel_active)
        for i in range(start, end + 1):
            if i >= len(self._line_y_prefix) or i >= len(self._line_heights):
                continue
            # 【根源重构】统一坐标系
            y = self._line_y_prefix[i] - self._scroll_offset
            h = self._line_heights[i]
            w = self._canvas_width - self.MARGIN_X * 2
            rect = self.canvas.create_rectangle(self.MARGIN_X, y, self.MARGIN_X + w, y + h, fill='#264f78', outline='')
            self.canvas.tag_lower(rect)
            self._sel_items.append(rect)

    def _copy_selection(self, event=None):
        if self._sel_anchor is None or self._sel_active is None:
            return
        start = min(self._sel_anchor, self._sel_active)
        end = max(self._sel_anchor, self._sel_active)
        lines = []
        for i in range(start, end + 1):
            if i < len(self._lines):
                lines.append(self._lines[i][0])
        text = '\n'.join(lines)
        if text:
            self.canvas.clipboard_clear()
            self.canvas.clipboard_append(text)

    def set_wrap(self, wrap):
        if self._wrap != wrap:
            self._wrap = wrap
            self._recalculate_all_heights()
            # 【修复】换行模式改变，必须强制重建
            self._refresh_visible(force_rebuild=True)

    def clear(self):
        for items in self._visible_items.values():
            for item in items:
                self.canvas.delete(item)
        for item in self._sel_items:
            self.canvas.delete(item)
        self._lines.clear()
        self._line_heights.clear()
        self._line_y_prefix = [self.MARGIN_Y]  # 【根源重构】保持一致
        self._visible_items.clear()
        self._sel_items.clear()
        self._scroll_offset = 0
        self._char_count = 0
        self._sel_anchor = None
        self._sel_active = None
        self._update_scrollbar()

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
        self.active = np.zeros(max_voices, dtype=bool)
        self.next_voice = 0
        self.stream = sd.OutputStream(
            samplerate=samplerate,
            channels=2,
            dtype='float32',
            blocksize=blocksize,
            latency='low',
            callback=self._audio_callback,
        )
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
        self._fade_jobs = {}
        self._log_buffer = []
        try:
            self._click_player = ClickPlayer()
        except Exception:
            self._click_player = None
        self._build_ui()
        # [新增] 注入日志队列给 server，启用事件驱动
        self._log_queue = queue.Queue()
        agent_server.set_gui_log_queue(self._log_queue)
        # [新增] 注册虚拟事件
        self.root.bind('<<LogArrived>>', self._process_log_queue)
        # [新增] 启动后台日志消费者线程
        self._log_consumer_thread = threading.Thread(target=self._log_consumer_loop, daemon=True)
        self._log_consumer_thread.start()
        agent_server.permission_mgr.set_callback(self._make_permission_callback())
        agent_server._push_config()
        self._apply_dark_titlebar()
        self._start_server()
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    def _log_consumer_loop(self):
        """后台线程：阻塞等待日志队列，收到后唤醒主线程"""
        while True:
            try:
                stream_name, text = self._log_queue.get()
                self._log_buffer.append((stream_name, text))
                self.root.event_generate('<<LogArrived>>', when='tail')
            except Exception:
                pass

    def _process_log_queue(self, event=None):
        """主线程：批量处理累积的日志"""
        try:
            while True:
                stream_name, text = self._log_buffer.pop(0)
                for line in text.split('\n'):
                    line = line.rstrip()
                    if line:
                        self._append_parsed(line)
        except IndexError:
            pass

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
                tk.Label(dialog, text="⚠", bg=BG, fg=YELLOW, font=('Microsoft YaHei UI', 28)).pack(pady=(14, 2))
                info = tk.Frame(dialog, bg=HEADER)
                info.pack(fill=tk.X, padx=16, pady=8)
                if cmd == '高危命令拦截':
                    tk.Label(dialog, text="即将执行高危系统命令", bg=BG, fg=TXT, font=FONT_UI_B).pack()
                    tk.Label(info, text="拦截命令:", bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='w').pack(fill=tk.X, padx=10, pady=(6, 0))
                    tk.Label(info, text=f"{filepath}", bg=HEADER, fg=RED, font=FONT_MONO, anchor='w', wraplength=400).pack(fill=tk.X, padx=10, pady=(0, 6))
                else:
                    tk.Label(dialog, text="路径超出工作目录", bg=BG, fg=TXT, font=FONT_UI_B).pack()
                    tk.Label(info, text=f"指令: {cmd}", bg=HEADER, fg=RED, font=FONT_MONO, anchor='w').pack(fill=tk.X, padx=10, pady=(6, 2))
                    tk.Label(info, text=f"目标: {filepath}", bg=HEADER, fg=TXT, font=FONT_MONO, anchor='w', wraplength=400).pack(fill=tk.X, padx=10)
                    tk.Label(info, text=f"工作目录: {agent_server.WORK_DIR}", bg=HEADER, fg=TXT2, font=('Consolas', 9), anchor='w').pack(fill=tk.X, padx=10, pady=(2, 6))
                bf = tk.Frame(dialog, bg=BG)
                bf.pack(fill=tk.X, padx=16, pady=(0, 14))
                def close(val):
                    result[0] = val
                    event.set()
                    dialog.destroy()
                tk.Button(bf, text="✕ 拒绝", command=lambda: close(False), bg='#3d1f1f', fg=RED, activebackground='#4d2525', activeforeground=RED, font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))
                tk.Button(bf, text="✓ 允许一次", command=lambda: close(True), bg='#1f3d1f', fg=GREEN, activebackground='#254d25', activeforeground=GREEN, font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)
                if cmd != '高危命令拦截':
                    tk.Button(bf, text="✓ 始终允许", command=lambda: close('always'), bg='#2a2a1a', fg=BLUE, activebackground='#3a3a2a', activeforeground=BLUE, font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2').pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))
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
        self.status_text = tk.Label(bar, text="服务运行中", bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='w')
        self.status_text.pack(side=tk.LEFT)
        self.port_text = tk.Label(bar, text="http://127.0.0.1:9966", bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='e')
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
        self.lbl_dir = tk.Label(f, text=agent_server.WORK_DIR, bg=PANEL, fg=TXT, font=('Consolas', 9), wraplength=180, justify='left', anchor='w')
        self.lbl_dir.pack(anchor='w', padx=16, pady=(0, 6))
        self._btn(f, "选择工作目录...", self._select_dir).pack(fill=tk.X, padx=12, pady=(0, 4))
        self._sep(f)
        tk.Label(f, text="🔧 服务", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 6))
        self._btn(f, "重启服务", self._restart_server).pack(fill=tk.X, padx=12, pady=2)
        self._btn(f, "清空日志", self._clear_log).pack(fill=tk.X, padx=12, pady=2)
        self._sep(f)
        tk.Label(f, text="🔒 权限控制", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_perm = tk.BooleanVar(value=agent_server.permission_mgr.enabled)
        self.chk_perm = tk.Checkbutton(f, text="启用目录限制", variable=self.var_perm, bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT, font=FONT_UI, command=self._wrap_cmd(self._toggle_permission))
        self.chk_perm.pack(anchor='w', padx=20)
        self._btn(f, "清除始终允许列表", self._clear_always_allow).pack(fill=tk.X, padx=12, pady=2)
        self.lbl_allow_count = tk.Label(f, text="", bg=PANEL, fg=TXT2, font=('Consolas', 9), anchor='w')
        self.lbl_allow_count.pack(anchor='w', padx=20, pady=(0, 4))
        self._sep(f)
        tk.Label(f, text="📋 文件读取", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_clipboard = tk.BooleanVar(value=agent_server.clipboard_mode)
        self.chk_clipboard = tk.Checkbutton(f, text="读取文件时使用剪贴板API", variable=self.var_clipboard, bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT, font=FONT_UI, command=self._wrap_cmd(self._toggle_clipboard))
        self.chk_clipboard.pack(anchor='w', padx=20)
        self.var_exec = tk.BooleanVar(value=agent_server.exec_enabled)
        self.chk_exec = tk.Checkbutton(f, text="允许执行系统命令", variable=self.var_exec, bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT, font=FONT_UI, command=self._wrap_cmd(self._toggle_exec))
        self.chk_exec.pack(anchor='w', padx=20)
        tk.Label(f, text="exec 终端类型", bg=PANEL, fg=TXT2, font=('Consolas', 9)).pack(anchor='w', padx=20, pady=(6, 0))
        self.var_shell = tk.StringVar(value=agent_server.shell_type)
        shell_frame = tk.Frame(f, bg=PANEL)
        shell_frame.pack(anchor='w', padx=20, pady=(0, 2))
        tk.Radiobutton(shell_frame, text="PowerShell", variable=self.var_shell, value='powershell', bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT, font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)).pack(side=tk.LEFT)
        tk.Radiobutton(shell_frame, text="CMD", variable=self.var_shell, value='cmd', bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT, font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)).pack(side=tk.LEFT, padx=(10, 0))

    def _build_center(self):
        f = self.center
        hdr = tk.Frame(f, bg=BG, height=38)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self.right_title = tk.Label(hdr, text="📋 控制台日志", bg=BG, fg=TXT, font=('Microsoft YaHei UI', 11), anchor='w', padx=12)
        self.right_title.pack(side=tk.LEFT, fill=tk.Y)
        self.btn_wrap = tk.Label(hdr, text="🔁 换行: 开", bg=BG, fg=BLUE if self._log_wrap else TXT2, font=('Microsoft YaHei UI', 9), cursor='hand2', padx=8)
        self.btn_wrap.pack(side=tk.RIGHT, padx=(0, 12))
        self.btn_wrap.bind('<Button-1>', lambda e: self._toggle_log_wrap())
        log_frame = tk.Frame(f, bg=BG)
        log_frame.pack(fill=tk.BOTH, expand=True)
        self.log_canvas = LogCanvas(log_frame, log_file=agent_server.LOG_FILE, wrap=self._log_wrap)
        self.cli_frame = tk.Frame(f, bg=HEADER)
        self.cli_prompt = tk.Label(self.cli_frame, text=" Agent > ", bg=HEADER, fg=BLUE, font=FONT_MONO_B, padx=8)
        self.cli_prompt.pack(side=tk.LEFT)
        self.cli_entry = tk.Entry(self.cli_frame, bg=HEADER, fg=TXT, font=FONT_MONO, bd=0, insertbackground=TXT, highlightthickness=0)
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
        new_w = win_right - event.x_root - 3
        new_w = max(160, min(new_w, 400))
        self.right_panel.configure(width=new_w)
        self._right_width = new_w

    def _make_momentary_btn(self, parent, text, command):
        btn = tk.Button(parent, text=text, bg=BTN, fg=TXT, activebackground=RED, activeforeground=TXT, font=FONT_UI, bd=0, padx=12, pady=10, cursor='hand2', justify='center')
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
        btn = tk.Button(parent, text=text, bg=BTN, fg=TXT, activebackground=BTN_H, activeforeground=TXT, font=FONT_UI, bd=0, padx=12, pady=10, cursor='hand2')
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
        btn = tk.Button(parent, text=text, command=self._wrap_cmd(command) if not disabled else command, bg=BTN, fg=btn_fg, activebackground=BTN_H, activeforeground=btn_fg, font=('Microsoft YaHei UI', 10, weight), bd=0, padx=12, pady=8, anchor='w', cursor=cursor, state=state)
        if not disabled:
            btn.bind('<Enter>', lambda e, b=btn: b.configure(bg=BTN_H))
            btn.bind('<Leave>', lambda e, b=btn: b.configure(bg=BTN))
        return btn

    # ========== 日志系统 ==========
    def _append_log(self, text, tag='txt', parts=None):
        self.log_canvas.append_line(text, tag, parts)

    def _toggle_log_wrap(self):
        self._log_wrap = not self._log_wrap
        self.log_canvas.set_wrap(self._log_wrap)
        if self._log_wrap:
            self.btn_wrap.configure(text="🔁 换行: 开", fg=BLUE)
            print('[Agent] 日志换行已开启')
        else:
            self.btn_wrap.configure(text="🔁 换行: 关", fg=TXT2)
            print('[Agent] 日志换行已关闭')

    def _append_parsed(self, line):
        if '\x1b[' in line:
            parts = _parse_ansi_to_parts(line)
            self._append_log(line, 'txt', parts)
            return
        m = re.match(r'^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\])\s+(\S+)(?:\s*\|\s*(.*))?$', line)
        if m:
            ts, action, detail = m.groups()
            self._append_log(f"{ts} {action} | {detail}" if detail else f"{ts} {action}", 'txt')
            return
        if re.match(r'^\d+\.\d+\.\d+\.\d+\s', line):
            self._append_log(line, 'http')
            return
        stripped = line.strip()
        if (stripped.startswith('===') or stripped.startswith('PokerAgent') or stripped.startswith('监听') or stripped.startswith('工作') or stripped.startswith('帮助') or stripped.startswith('操作') or stripped.startswith('[Agent]')):
            self._append_log(line, 'banner')
            return
        low = line.lower()
        if 'traceback' in low or line.startswith(' File '):
            self._append_log(line, 'err')
            return
        self._append_log(line, 'txt')

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
        self._append_log(f"Agent > {cmd}", 'prompt')
        self.cli_entry.delete(0, tk.END)
        result = agent_server.execute_line(cmd)
        if result:
            for rline in result.split('\n'):
                if '\x1b[' in rline:
                    parts = _parse_ansi_to_parts(rline)
                    self._append_log(rline, 'txt', parts)
                else:
                    self._append_log(rline, 'txt')
        else:
            self._append_log("（空指令或注释）", 'warn')

    def run(self):
        self.root.mainloop()

if __name__ == '__main__':
    gui = AgentGUI()
    gui.run()

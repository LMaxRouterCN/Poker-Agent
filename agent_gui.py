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
import logging
import json

# 导入核心引擎（复用 execute_line、app, KNOWN_CMDS 等）
try:
    import agent_server
except ImportError as e:
    print(f'[Agent] 无法导入 agent_server.py: {e}')
    print('[Agent] 请确认 agent_server.py 与本文件在同一目录下')
    sys.exit(1)

from werkzeug.serving import make_server

# [新增] GUI 专属配置文件（窗口位置/大小等纯前端状态，与后端 agent_config.json 分离）
GUI_CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'gui_config.json')

# 静默 werkzeug 日志
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 颜色方案 — 黑灰白黄红橙绿 (匹配 PokerAgent.js)
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BG = '#0a0a0a'
PANEL = '#1a1a1a'
HEADER = '#1a1a1a'
BTN = '#222222'   # [修改] 原 #1a1a1a，比面板亮一档，让按钮可辨识
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

# [新增] ANSI SGR 颜色码 → 十六进制（用于 GUI 渲染 exec 输出）
_ANSI_FG = {
    30: '#000000', 31: '#cd3131', 32: '#0dbc79', 33: '#e5e510',
    34: '#2472c8', 35: '#bc3fbc', 36: '#11a8cd', 37: '#e5e5e5',
    90: '#666666', 91: '#f14c4c', 92: '#23d18b', 93: '#f5f543',
    94: '#3b8eea', 95: '#d670d6', 96: '#29b8db', 97: '#ffffff',
}
_ANSI_BG = {
    40: '#000000', 41: '#cd3131', 42: '#0dbc79', 43: '#e5e510',
    44: '#2472c8', 45: '#bc3fbc', 46: '#11a8cd', 47: '#e5e5e5',
}

# [新增] 颜色线性插值，用于按钮渐隐动画
def _lerp_color(c1, c2, t):
    """t=0 返回 c1，t=1 返回 c2"""
    r = int(int(c1[1:3], 16) + (int(c2[1:3], 16) - int(c1[1:3], 16)) * t)
    g = int(int(c1[3:5], 16) + (int(c2[3:5], 16) - int(c1[3:5], 16)) * t)
    b = int(int(c1[5:7], 16) + (int(c2[5:7], 16) - int(c1[5:7], 16)) * t)
    return f'#{r:02x}{g:02x}{b:02x}'

# [新增] 常驻低延迟音频流 + 多路混音点击音效
# 按钮点击时只做一个 queue.put(1)，播放在音频回调里完成，UI 零阻塞
class ClickPlayer:
    def __init__(self, samplerate=18000, blocksize=128, max_voices=32):
        import numpy as np
        import sounddevice as sd
        self._np = np
        self.samplerate = samplerate
        self.max_voices = max_voices

        # 预生成咔哒声（白噪声 + 指数衰减，12ms）
        n = int(samplerate * 0.012)
        t = np.arange(n, dtype=np.float32) / samplerate
        rng = np.random.default_rng(0)
        click = rng.standard_normal(n).astype(np.float32)
        click *= np.exp(-t * 450).astype(np.float32)
        peak = np.max(np.abs(click))
        if peak > 0:
            click /= peak
        click *= 0.45  # 单路音量，防多路叠加爆音
        self.click = click

        # 触发队列：UI 线程只往这里放事件
        self.trigger_queue = queue.SimpleQueue()

        # 每个 voice 的播放位置和激活状态
        self.pos = np.zeros(max_voices, dtype=np.int32)
        self.active = np.zeros(max_voices, dtype=bool)
        self.next_voice = 0

        # 常驻音频流
        self.stream = sd.OutputStream(
            samplerate=samplerate, channels=2, dtype='float32',
            blocksize=blocksize, latency='low',
            callback=self._audio_callback,
        )
        self.stream.start()

    def trigger(self):
        """按钮按下时调用，极轻量"""
        self.trigger_queue.put(1)

    def _audio_callback(self, outdata, frames, time_info, status):
        np = self._np
        # 消费本次音频块期间的所有触发，分配 voice
        while True:
            try:
                self.trigger_queue.get_nowait()
            except queue.Empty:
                break
            v = self.next_voice
            self.next_voice = (self.next_voice + 1) % self.max_voices
            self.pos[v] = 0       # 抢断最旧的 voice，不排队
            self.active[v] = True

        mix = np.zeros(frames, dtype=np.float32)
        click = self.click
        click_len = len(click)

        # 混合所有正在播放的 voice
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

        np.clip(mix, -0.99, 0.99, out=mix)  # 限幅防爆音
        outdata[:, 0] = mix
        outdata[:, 1] = mix

    def stop(self):
        """关闭音频流"""
        try:
            self.stream.stop()
            self.stream.close()
        except Exception:
            pass

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 日志桥接（stdout/stderr -> GUI 日志队列）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_log_q = queue.Queue()

class _StreamBridge:
    """将 print 输出桥接到 GUI 日志队列"""
    def __init__(self, name):
        self.name = name
        self._orig = sys.stdout if name == 'out' else sys.stderr

    def write(self, s):
        if s:
            _log_q.put((self.name, s))
            self._orig.write(s)

    def flush(self):
        self._orig.flush()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# Flask 服务器线程（可优雅关闭/重启）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class _ServerThread(threading.Thread):
    def __init__(self):
        super().__init__(daemon=True)
        self.server = None
        self._ready = threading.Event()

    def run(self):
        try:
            self.server = make_server(
                '127.0.0.1', 9966, agent_server.app, threaded=True
            )
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

        # [修改] 优先从 gui_config.json 恢复窗口位置/大小，不存在则居中默认尺寸
        gui_cfg = self._load_gui_config()
        if gui_cfg and 'window_geometry' in gui_cfg:
            try:
                self.root.geometry(gui_cfg['window_geometry'])
            except tk.TclError:
                # geometry 格式损坏，回退默认
                self._default_geometry()
        else:
            self._default_geometry()
        
        self._cli_mode = False
        self._server = None
        self._ansi_tags = set()  # [新增] ANSI 样式 tag 缓存，避免重复创建
        # [新增] 面板宽度（从配置恢复，默认220）
        self._left_width = gui_cfg.get('left_panel_width', 220) if gui_cfg else 220
        self._right_width = gui_cfg.get('right_panel_width', 220) if gui_cfg else 220
        self._fade_jobs = {}  # [新增] 渐隐动画任务追踪，防止同一控件动画叠加
        # [新增] 初始化点击音效播放器（常驻音频流）
        try:
            self._click_player = ClickPlayer()
        except Exception:
            self._click_player = None  # 没有音频设备或依赖缺失时静默降级
        self._build_ui()
        
        agent_server.permission_mgr.set_callback(self._make_permission_callback())
        agent_server._push_config()
        
        self._apply_dark_titlebar()
        self._apply_dark_titlebar()
        self._start_log_redirect()
        self._start_server()
        self._poll_log()
        
        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ========== 这些方法在 __init__ 阶段被调用，必须放在前面 ==========
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
        path = filedialog.askdirectory(
            initialdir=agent_server.WORK_DIR,
            title="选择工作目录"
        )
        if path:
            agent_server.WORK_DIR = path
            agent_server.TRASH_DIR = os.path.join(path, '.agent_trash')
            self.lbl_dir.configure(text=path)
            agent_server._push_config()  # 持久化 + 通知前端配置变更
            print(f'[Agent] 工作目录已更改为: {path}')

    def _toggle_permission(self):
        enabled = self.var_perm.get()
        agent_server.permission_mgr.enabled = enabled
        agent_server._push_config()
        status = "已启用" if enabled else "已禁用"
        print(f'[Agent] 目录限制{status}')

    def _clear_always_allow(self):
        agent_server.permission_mgr.reset_session()
        self.lbl_allow_count.configure(text="始终允许: 0 条")
        print('[Agent] 已清除始终允许列表')

    def _toggle_clipboard(self):
        agent_server.clipboard_mode = self.var_clipboard.get()
        agent_server._push_config()
        status = "已启用" if agent_server.clipboard_mode else "已禁用"
        print(f'[Agent] 剪贴板读取模式{status}')

    def _toggle_exec(self):
        agent_server.exec_enabled = self.var_exec.get()
        agent_server._push_config()
        status = "已启用" if agent_server.exec_enabled else "已禁用"
        print(f'[Agent] 系统命令执行{status}')

    # [新增] Shell 类型切换
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
                
                if cmd == '高危命令拦截':
                    dialog.title("⚠ 高危系统命令拦截")
                else:
                    dialog.title("⚠ 路径权限请求")
                    
                dialog.configure(bg=BG)
                dialog.resizable(False, False)
                dialog.transient(gui_ref.root)
                dialog.grab_set()
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
                    tk.Label(info, text=f"拦截命令:", bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='w').pack(fill=tk.X, padx=10, pady=(6, 0))
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

                tk.Button(
                    bf, text="✕ 拒绝", command=lambda: close(False),
                    bg='#3d1f1f', fg=RED, activebackground='#4d2525', activeforeground=RED,
                    font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2'
                ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))

                tk.Button(
                    bf, text="✓ 允许一次", command=lambda: close(True),
                    bg='#1f3d1f', fg=GREEN, activebackground='#254d25', activeforeground=GREEN,
                    font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2'
                ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)

                if cmd != '高危命令拦截':
                    tk.Button(
                        bf, text="✓ 始终允许", command=lambda: close('always'),
                        bg='#2a2a1a', fg=BLUE, activebackground='#3a3a2a', activeforeground=BLUE,
                        font=FONT_UI, bd=0, padx=10, pady=6, cursor='hand2'
                    ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

            gui_ref.root.after(1, ask)
            event.wait(timeout=120)
            
            if event.is_set():
                count = len(agent_server.permission_mgr._always_allow)
                gui_ref.root.after_idle(
                    lambda: gui_ref.lbl_allow_count.configure(
                        text=f"始终允许: {count} 条"))
                return result[0]
            return False
        return callback

    # ========== [新增] GUI 配置持久化 ==========
    def _default_geometry(self):
        """默认窗口尺寸：1020x660 屏幕居中"""
        w, h = 1020, 660
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f'{w}x{h}+{x}+{y}')

    def _load_gui_config(self):
        """读取 gui_config.json，不存在或损坏返回 None"""
        if not os.path.exists(GUI_CONFIG_FILE):
            return None
        try:
            with open(GUI_CONFIG_FILE, 'r', encoding='utf-8') as f:
                return json.load(f)
        except Exception:
            return None

    def _save_gui_config(self):
        """关闭窗口时保存窗口位置/大小及面板宽度"""
        config = {
            'window_geometry': self.root.geometry(),
            'left_panel_width': self._left_width,    # [新增]
            'right_panel_width': self._right_width,  # [新增]
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
            ctypes.windll.dwmapi.DwmSetWindowAttribute(
                hwnd, 20, ctypes.byref(value), ctypes.sizeof(value)
            )
        except Exception:
            pass

    def _on_close(self):
        self._save_gui_config()  # [新增] 关闭时持久化窗口位置/大小
        if self._server:
            self._server.shutdown()
        # [新增] 关闭音频流
        if self._click_player:
            self._click_player.stop()
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        self.root.destroy()

    # ========== UI 构建方法 ==========
    def _build_ui(self):
        self._build_status_bar()

        # [修改] 三栏布局：左控制面板 | 中间日志区 | 右操作面板
        self.left = tk.Frame(self.root, bg=PANEL, width=self._left_width)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        # [新增] 左侧拖拽条
        self.left_grip = tk.Frame(self.root, bg=BORDER, width=3, cursor='sb_h_double_arrow')
        self.left_grip.pack(side=tk.LEFT, fill=tk.Y)

        # [修改] 原 self.right → self.center
        self.center = tk.Frame(self.root, bg=BG)
        self.center.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # [新增] 右侧拖拽条
        self.right_grip = tk.Frame(self.root, bg=BORDER, width=3, cursor='sb_h_double_arrow')
        self.right_grip.pack(side=tk.LEFT, fill=tk.Y)

        # [新增] 右侧操作面板
        self.right_panel = tk.Frame(self.root, bg=PANEL, width=self._right_width)
        self.right_panel.pack(side=tk.LEFT, fill=tk.Y)
        self.right_panel.pack_propagate(False)

        self._build_left()
        self._build_center()       # [修改] 原 _build_right
        self._build_right_panel()  # [新增]
        self._bind_grips()         # [新增]

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
        
        # 顶部黄色强调线
        tk.Frame(f, bg=BLUE, height=2).pack(fill=tk.X)

        tk.Label(f, text="⚙ 控制面板", bg=PANEL, fg=TXT, font=FONT_TITLE).pack(anchor='w', padx=16, pady=(18, 4))
        self._sep(f)

        # ── 命令行模式（重要，顶部醒目） ──
        self.btn_cli = self._btn(f, "⌨ 转到命令行窗口模式", self._toggle_cli, fg=BLUE, bold=True)
        self.btn_cli.pack(fill=tk.X, padx=12, pady=(2, 4))
        self._sep(f)

        # ── 工作目录 ──
        tk.Label(f, text="📂 工作目录", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.lbl_dir = tk.Label(f, text=agent_server.WORK_DIR, bg=PANEL, fg=TXT, font=('Consolas', 9), wraplength=180, justify='left', anchor='w')
        self.lbl_dir.pack(anchor='w', padx=16, pady=(0, 6))
        self._btn(f, "选择工作目录...", self._select_dir).pack(fill=tk.X, padx=12, pady=(0, 4))
        self._sep(f)

        # ── 服务控制 ──
        tk.Label(f, text="🔧 服务", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 6))
        self._btn(f, "重启服务", self._restart_server).pack(fill=tk.X, padx=12, pady=2)
        self._btn(f, "清空日志", self._clear_log).pack(fill=tk.X, padx=12, pady=2)
        self._sep(f)

        # ── 权限控制 ──
        tk.Label(f, text="🔒 权限控制", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_perm = tk.BooleanVar(value=agent_server.permission_mgr.enabled)
        self.chk_perm = tk.Checkbutton(
            f, text="启用目录限制", variable=self.var_perm,
            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._wrap_cmd(self._toggle_permission)  # [修改] 包一层音效
        )
        self.chk_perm.pack(anchor='w', padx=20)
        self._btn(f, "清除始终允许列表", self._clear_always_allow).pack(fill=tk.X, padx=12, pady=2)
        self.lbl_allow_count = tk.Label(f, text="", bg=PANEL, fg=TXT2, font=('Consolas', 9), anchor='w')
        self.lbl_allow_count.pack(anchor='w', padx=20, pady=(0, 4))
        self._sep(f)

        # ── 文件读取 ──
        tk.Label(f, text="📋 文件读取", bg=PANEL, fg=TXT2, font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_clipboard = tk.BooleanVar(value=agent_server.clipboard_mode)
        self.chk_clipboard = tk.Checkbutton(
            f, text="读取文件时使用剪贴板API", variable=self.var_clipboard,
            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._wrap_cmd(self._toggle_clipboard)  # [修改] 包一层音效
        )
        self.chk_clipboard.pack(anchor='w', padx=20)
        
        self.var_exec = tk.BooleanVar(value=agent_server.exec_enabled)
        self.chk_exec = tk.Checkbutton(
            f, text="允许执行系统命令", variable=self.var_exec,
            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._wrap_cmd(self._toggle_exec)  # [修改] 包一层音效
        )
        self.chk_exec.pack(anchor='w', padx=20)

        # [新增] Shell 类型选择（exec 指令使用的终端）
        tk.Label(f, text="exec 终端类型", bg=PANEL, fg=TXT2, font=('Consolas', 9)).pack(anchor='w', padx=20, pady=(6, 0))
        self.var_shell = tk.StringVar(value=agent_server.shell_type)
        shell_frame = tk.Frame(f, bg=PANEL)
        shell_frame.pack(anchor='w', padx=20, pady=(0, 2))
        tk.Radiobutton(
            shell_frame, text="PowerShell", variable=self.var_shell, value='powershell',
            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)  # [修改] 包一层音效
        ).pack(side=tk.LEFT)
        tk.Radiobutton(
            shell_frame, text="CMD", variable=self.var_shell, value='cmd',
            bg=PANEL, fg=TXT, selectcolor=BTN, activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._wrap_cmd(self._toggle_shell)  # [修改] 包一层音效
        ).pack(side=tk.LEFT, padx=(10, 0))

    # [修改] 原 _build_right → _build_center
    def _build_center(self):
        f = self.center  # [修改] 原 self.right

        # 头部标题栏
        hdr = tk.Frame(f, bg=BG, height=38)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)
        self.right_title = tk.Label(hdr, text="📋 控制台日志", bg=BG, fg=TXT, font=('Microsoft YaHei UI', 11), anchor='w', padx=12)
        self.right_title.pack(side=tk.LEFT, fill=tk.Y)

        # 日志文本区域
        log_frame = tk.Frame(f, bg=BG)
        log_frame.pack(fill=tk.BOTH, expand=True)

        self.log_text = tk.Text(
            log_frame, bg=BG, fg=TXT, font=FONT_MONO,
            bd=0, padx=12, pady=8, wrap=tk.WORD,
            state=tk.DISABLED, cursor='arrow',
            insertbackground=TXT, selectbackground='#264f78',
            highlightthickness=0, spacing1=2, spacing3=2,
        )
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview, bg=BTN, troughcolor=BG, bd=0, activebackground=BTN_H)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 日志颜色标签
        self.log_text.tag_configure('ts', foreground=TXT2)
        self.log_text.tag_configure('act', foreground=BLUE)    # 主要动作使用黄色强调
        self.log_text.tag_configure('txt', foreground=TXT)
        self.log_text.tag_configure('ok', foreground=GREEN)
        self.log_text.tag_configure('warn', foreground=YELLOW)
        self.log_text.tag_configure('err', foreground=RED)
        self.log_text.tag_configure('http', foreground='#484f58')
        self.log_text.tag_configure('prompt', foreground=CYAN)
        self.log_text.tag_configure('banner', foreground=PURPLE) # 横幅使用橙色

        # CLI 输入栏（默认隐藏）
        self.cli_frame = tk.Frame(f, bg=HEADER)
        self.cli_prompt = tk.Label(self.cli_frame, text=" Agent > ", bg=HEADER, fg=BLUE, font=FONT_MONO_B, padx=8)
        self.cli_prompt.pack(side=tk.LEFT)
        
        self.cli_entry = tk.Entry(self.cli_frame, bg=HEADER, fg=TXT, font=FONT_MONO, bd=0, insertbackground=TXT, highlightthickness=0, highlightcolor=BLUE)
        self.cli_entry.pack(side=tk.LEFT, fill=tk.X, expand=True, padx=(0, 8), pady=7)
        self.cli_entry.bind('<Return>', self._on_cli_enter)

    # [新增] 右侧操作面板
    def _build_right_panel(self):
        f = self.right_panel

        # 顶部红色强调线（区别于左侧黄色）
        tk.Frame(f, bg=RED, height=2).pack(fill=tk.X)

        tk.Label(f, text="⚡ 任务控制", bg=PANEL, fg=TXT, font=FONT_TITLE).pack(anchor='w', padx=16, pady=(18, 4))
        self._sep(f)

        # 终止当前任务并丢弃（自复位：按下触发+变红，松开渐隐）
        self.btn_kill_discard = self._make_momentary_btn(
            f, "⛔ 终止当前任务\n并丢弃", self._on_kill_discard
        )
        self.btn_kill_discard.pack(fill=tk.X, padx=12, pady=4)

        # 终止当前任务并返回done（自复位）
        self.btn_kill_done = self._make_momentary_btn(
            f, "⛔ 终止当前任务\n并返回 done", self._on_kill_done
        )
        self.btn_kill_done.pack(fill=tk.X, padx=12, pady=4)

        self._sep(f)

        # 暂停任务队列（自锁：点击切换，锁定时红色）
        self.btn_pause = self._make_toggle_btn(
            f, "⏸ 暂停任务队列", self._on_pause_on, self._on_pause_off
        )
        self.btn_pause.pack(fill=tk.X, padx=12, pady=4)

    # [新增] 面板宽度拖拽调节
    def _bind_grips(self):
        self.left_grip.bind('<B1-Motion>', self._on_left_grip_drag)
        self.right_grip.bind('<B1-Motion>', self._on_right_grip_drag)

    def _on_left_grip_drag(self, event):
        # 鼠标屏幕x - 窗口左边缘 = 面板新宽度
        new_w = event.x_root - self.root.winfo_rootx()
        new_w = max(160, min(new_w, 400))  # 限制 160~400
        self.left.configure(width=new_w)
        self._left_width = new_w

    def _on_right_grip_drag(self, event):
        # 窗口右边缘 - 鼠标屏幕x - grip宽度 = 面板新宽度
        win_right = self.root.winfo_rootx() + self.root.winfo_width()
        new_w = win_right - event.x_root - 3
        new_w = max(160, min(new_w, 400))
        self.right_panel.configure(width=new_w)
        self._right_width = new_w

    # [新增] 自复位按钮：按下瞬间变红并触发回调，松开后红色渐隐
    def _make_momentary_btn(self, parent, text, command):
        btn = tk.Button(
            parent, text=text,
            bg=BTN, fg=TXT, activebackground=RED, activeforeground=TXT,
            font=FONT_UI, bd=0, padx=12, pady=10, cursor='hand2',
            justify='center',
        )
        btn.bind('<ButtonPress-1>', lambda e, b=btn, c=command: self._on_momentary_press(b, c))
        btn.bind('<ButtonRelease-1>', lambda e, b=btn: self._on_momentary_release(b))
        return btn

    def _on_momentary_press(self, btn, command):
        self._play_click()     # [新增]
        btn.configure(bg=RED)  # 按下立即变红
        command()              # 触发回调

    def _on_momentary_release(self, btn):
        self._fade_bg(btn, RED, BTN)  # 松开后红色渐隐

    # [新增] 自锁按钮：点击切换锁定/解锁，锁定时背景红色
    def _make_toggle_btn(self, parent, text, cmd_on, cmd_off):
        btn = tk.Button(
            parent, text=text,
            bg=BTN, fg=TXT, activebackground=BTN_H, activeforeground=TXT,
            font=FONT_UI, bd=0, padx=12, pady=10, cursor='hand2',
        )
        btn._locked = False  # 附加锁定状态
        btn.bind('<Button-1>', lambda e, b=btn, on=cmd_on, off=cmd_off: self._on_toggle_click(b, on, off))
        return btn

    def _on_toggle_click(self, btn, cmd_on, cmd_off):
        self._play_click()     # [新增]
        btn._locked = not btn._locked
        if btn._locked:
            btn.configure(bg=RED)
            cmd_on()
        else:
            cmd_off()
            self._fade_bg(btn, RED, BTN)  # 解锁时红色渐隐

    # [新增] 背景色渐隐动画（from_color → to_color，默认300ms / 10帧）
    def _fade_bg(self, widget, from_color, to_color, duration_ms=300, steps=10):
        key = id(widget)
        # 取消此控件上一次未完成的渐隐，防止动画叠加
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
        # 动画结束后清理追踪记录
        self.root.after(duration_ms + 50, lambda k=key: self._fade_jobs.pop(k, None))

    # [修改] 触发 ClickPlayer，UI 线程只做一个 queue.put
    def _play_click(self):
        if self._click_player:
            self._click_player.trigger()

    # [新增] 包装回调：执行前先播放点击音效
    def _wrap_cmd(self, fn):
        return lambda: (self._play_click(), fn())

    # [修改] 任务控制回调 → 接入后端
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

    # ──────── UI 辅助 ────────
    def _sep(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=10)

    def _btn(self, parent, text, command, fg=TXT, bold=False, disabled=False):
        weight = 'bold' if bold else 'normal'
        state = tk.DISABLED if disabled else tk.NORMAL
        cursor = 'arrow' if disabled else 'hand2'
        btn_fg = DISABLED_FG if disabled else fg

        btn = tk.Button(
            parent, text=text,
            command=self._wrap_cmd(command) if not disabled else command,  # [修改] 包一层音效
            bg=BTN, fg=btn_fg, activebackground=BTN_H, activeforeground=btn_fg,
            font=('Microsoft YaHei UI', 10, weight),
            bd=0, padx=12, pady=8, anchor='w', cursor=cursor, state=state,
        )
        if not disabled:
            btn.bind('<Enter>', lambda e, b=btn: b.configure(bg=BTN_H))
            btn.bind('<Leave>', lambda e, b=btn: b.configure(bg=BTN))
        return btn

    # ──────── 日志系统 ────────
    def _append_raw(self, text, tag='txt'):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.insert(tk.END, text + '\n', tag)
        self.log_text.see(tk.END)
        self.log_text.configure(state=tk.DISABLED)

    # [新增] ANSI 颜色渲染：解析转义码，映射为 tkinter Text tag
    def _append_ansi(self, text):
        w = self.log_text
        w.configure(state=tk.NORMAL)
        # 按 ANSI SGR 序列切割文本
        parts = re.split(r'(\x1b\[[0-9;]*m)', text)
        fg = bg = None
        bold = False
        for part in parts:
            if not part:
                continue
            m = re.match(r'\x1b\[([0-9;]*)m', part)
            if m:
                codes = [int(c) for c in m.group(1).split(';') if c] if m.group(1) else [0]
                for code in codes:
                    if code == 0:
                        fg, bg, bold = None, None, False
                    elif code == 1:
                        bold = True
                    elif code == 22:
                        bold = False
                    elif code == 39:
                        fg = None
                    elif code == 49:
                        bg = None
                    elif code in _ANSI_FG:
                        fg = _ANSI_FG[code]
                    elif code in _ANSI_BG:
                        bg = _ANSI_BG[code]
            else:
                if fg or bg or bold:
                    tag_key = f'ansi_{fg}_{bg}_{bold}'
                    if tag_key not in self._ansi_tags:
                        kw = {}
                        if fg: kw['foreground'] = fg
                        if bg: kw['background'] = bg
                        if bold: kw['font'] = FONT_MONO_B
                        w.tag_configure(tag_key, **kw)
                        self._ansi_tags.add(tag_key)
                    w.insert(tk.END, part, tag_key)
                else:
                    w.insert(tk.END, part, 'txt')
        w.insert(tk.END, '\n')
        w.see(tk.END)
        w.configure(state=tk.DISABLED)

    def _append_parsed(self, line):
        """解析日志行，分色显示"""
        m = re.match(
            r'^(\[\d{4}-\d{2}-\d{2} \d{2}:\d{2}:\d{2}\])\s+(\S+)(?:\s*\|\s*(.*))?$',
            line
        )
        if m:
            ts, action, detail = m.groups()
            w = self.log_text
            w.configure(state=tk.NORMAL)
            w.insert(tk.END, ts + ' ', 'ts')
            if action == 'RESULT':
                w.insert(tk.END, action, 'ok')
            elif 'ERROR' in action or action == 'TRACE':
                w.insert(tk.END, action, 'err')
            else:
                w.insert(tk.END, action, 'act')
            if detail:
                w.insert(tk.END, ' | ' + detail, 'txt')
            w.insert(tk.END, '\n')
            w.see(tk.END)
            w.configure(state=tk.DISABLED)
            return

        # HTTP 请求日志
        if re.match(r'^\d+\.\d+\.\d+\.\d+\s', line):
            self._append_raw(line, 'http')
            return

        # 启动横幅 / Agent 自身日志
        stripped = line.strip()
        if (stripped.startswith('===') or
            stripped.startswith('PokerAgent') or
            stripped.startswith('监听') or
            stripped.startswith('工作') or
            stripped.startswith('帮助') or
            stripped.startswith('操作') or
            stripped.startswith('[Agent]')):
            self._append_raw(line, 'banner')
            return

        # 错误堆栈
        low = line.lower()
        if 'traceback' in low or line.startswith('  File '):
            self._append_raw(line, 'err')
            return

        # 默认
        self._append_raw(line, 'txt')

    def _poll_log(self):
        try:
            while True:
                _, text = _log_q.get_nowait()
                for line in text.split('\n'):
                    line = line.rstrip()
                    if line:
                        self._append_parsed(line)
        except queue.Empty:
            pass
        self.root.after(80, self._poll_log)

    def _start_log_redirect(self):
        self._orig_stdout = sys.stdout
        self._orig_stderr = sys.stderr
        sys.stdout = _StreamBridge('out')
        sys.stderr = _StreamBridge('err')

    # ──────── 服务器管理 ────────
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
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state=tk.DISABLED)

    def _on_cli_enter(self, event):
        cmd = self.cli_entry.get().strip()
        if not cmd:
            return
        self._append_raw(f"Agent > {cmd}", 'prompt')
        self.cli_entry.delete(0, tk.END)
        result = agent_server.execute_line(cmd)
        if result:
            for rline in result.split('\n'):
                # [修改] 含 ANSI 转义码时渲染颜色，否则普通显示
                if '\x1b[' in rline:
                    self._append_ansi(rline)
                else:
                    self._append_raw(rline, 'txt')
        else:
            self._append_raw("（空指令或注释）", 'warn')

    def run(self):
        self.root.mainloop()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == '__main__':
    gui = AgentGUI()
    gui.run()
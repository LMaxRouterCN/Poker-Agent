"""低配版Agent - GUI 控制台
用法：python agent_gui.py（不要和 agent_server.py 同时运行）
依赖：flask, flask-cors, werkzeug（与 agent_server.py 相同）
"""

import tkinter as tk
from tkinter import filedialog, messagebox
import threading
import sys
import os
import queue
import re
import logging

# 导入核心引擎（复用 execute_line、app、KNOWN_CMDS 等）
try:
    import agent_server
except ImportError as e:
    print(f'[Agent] 无法导入 agent_server.py: {e}')
    print('[Agent] 请确认 agent_server.py 与本文件在同一目录下')
    sys.exit(1)

from werkzeug.serving import make_server

# 静默 werkzeug 日志
logging.getLogger('werkzeug').setLevel(logging.ERROR)

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  颜色方案 — 暗黑科技风
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
BG      = '#0d1117'
PANEL   = '#161b22'
HEADER  = '#1c2128'
BTN     = '#21262d'
BTN_H   = '#30363d'
BORDER  = '#30363d'
TXT     = '#e6edf3'
TXT2    = '#8b949e'
BLUE    = '#58a6ff'
GREEN   = '#3fb950'
YELLOW  = '#d29922'
RED     = '#f85149'
PURPLE  = '#bc8cff'
CYAN    = '#39d2c0'
DISABLED_FG = '#484f58'

FONT_UI     = ('Microsoft YaHei UI', 10)
FONT_UI_B   = ('Microsoft YaHei UI', 10, 'bold')
FONT_TITLE  = ('Microsoft YaHei UI', 14, 'bold')
FONT_MONO   = ('Consolas', 10)
FONT_MONO_B = ('Consolas', 10, 'bold')

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  日志桥接（stdout/stderr -> GUI 日志面板）
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
#  Flask 服务器线程（可优雅关闭/重启）
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
#  GUI 主类
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class AgentGUI:

    def __init__(self):
        self.root = tk.Tk()
        self.root.title("低配版Agent")
        self.root.configure(bg=BG)
        self.root.minsize(780, 480)

        w, h = 1020, 660
        x = (self.root.winfo_screenwidth() - w) // 2
        y = (self.root.winfo_screenheight() - h) // 2
        self.root.geometry(f'{w}x{h}+{x}+{y}')

        self._cli_mode = False
        self._server = None

        self._build_ui()
        agent_server.permission_mgr.set_callback(self._make_permission_callback())
        agent_server._push_config()
        self._apply_dark_titlebar()
        self._apply_dark_titlebar()
        self._start_log_redirect()
        self._start_server()
        self._poll_log()

        self.root.protocol("WM_DELETE_WINDOW", self._on_close)

    # ──────── 构建界面 ────────

    def _build_ui(self):
        self._build_status_bar()

        self.left = tk.Frame(self.root, bg=PANEL, width=220)
        self.left.pack(side=tk.LEFT, fill=tk.Y)
        self.left.pack_propagate(False)

        self.right = tk.Frame(self.root, bg=BG)
        self.right.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        self._build_left()
        self._build_right()

    def _build_status_bar(self):
        bar = tk.Frame(self.root, bg=HEADER, height=26)
        bar.pack(side=tk.BOTTOM, fill=tk.X)
        bar.pack_propagate(False)

        self.status_dot = tk.Label(bar, text="●", bg=HEADER, fg=GREEN,
                                    font=FONT_MONO)
        self.status_dot.pack(side=tk.LEFT, padx=(10, 4))

        self.status_text = tk.Label(bar, text="服务运行中", bg=HEADER, fg=TXT2,
                                     font=FONT_MONO, anchor='w')
        self.status_text.pack(side=tk.LEFT)

        self.port_text = tk.Label(bar, text="http://127.0.0.1:9966",
                                   bg=HEADER, fg=TXT2, font=FONT_MONO, anchor='e')
        self.port_text.pack(side=tk.RIGHT, padx=10)

    def _build_left(self):
        f = self.left

        # 顶部蓝色强调线
        tk.Frame(f, bg=BLUE, height=2).pack(fill=tk.X)

        # 标题
        tk.Label(f, text="⚙  控制面板", bg=PANEL, fg=TXT,
                 font=FONT_TITLE).pack(anchor='w', padx=16, pady=(18, 4))
        self._sep(f)

        # ── 命令行模式（重要，顶部醒目） ──
        self.btn_cli = self._btn(f, "⌨  转到命令行窗口模式",
                                  self._toggle_cli, fg=BLUE, bold=True)
        self.btn_cli.pack(fill=tk.X, padx=12, pady=(2, 4))

        self._sep(f)

        # ── 工作目录 ──
        tk.Label(f, text="📂 工作目录", bg=PANEL, fg=TXT2,
                 font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.lbl_dir = tk.Label(f, text=agent_server.WORK_DIR, bg=PANEL, fg=TXT,
                                 font=('Consolas', 9), wraplength=180,
                                 justify='left', anchor='w')
        self.lbl_dir.pack(anchor='w', padx=16, pady=(0, 6))
        self._btn(f, "选择工作目录...", self._select_dir).pack(
            fill=tk.X, padx=12, pady=(0, 4))

        self._sep(f)

        # ── 服务控制 ──
        tk.Label(f, text="🔧 服务", bg=PANEL, fg=TXT2,
                 font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 6))
        self._btn(f, "重启服务", self._restart_server).pack(
            fill=tk.X, padx=12, pady=2)
        self._btn(f, "清空日志", self._clear_log).pack(
            fill=tk.X, padx=12, pady=2)

        self._sep(f)

        # ── 权限控制 ──
        tk.Label(f, text="🔒 权限控制", bg=PANEL, fg=TXT2,
                 font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_perm = tk.BooleanVar(value=True)
        self.chk_perm = tk.Checkbutton(
            f, text="启用目录限制", variable=self.var_perm,
            bg=PANEL, fg=TXT, selectcolor=BTN,
            activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._toggle_permission)
        self.chk_perm.pack(anchor='w', padx=20)
        self._btn(f, "清除始终允许列表",
                  self._clear_always_allow).pack(fill=tk.X, padx=12, pady=2)
        self.lbl_allow_count = tk.Label(
            f, text="", bg=PANEL, fg=TXT2,
            font=('Consolas', 9), anchor='w')
        self.lbl_allow_count.pack(anchor='w', padx=20, pady=(0, 4))

        self._sep(f)

        # ── 文件读取 ──
        tk.Label(f, text="📋 文件读取", bg=PANEL, fg=TXT2,
                 font=FONT_UI).pack(anchor='w', padx=16, pady=(2, 2))
        self.var_clipboard = tk.BooleanVar(value=False)
        self.chk_clipboard = tk.Checkbutton(
            f, text="读取文件时使用剪贴板API", variable=self.var_clipboard,
            bg=PANEL, fg=TXT, selectcolor=BTN,
            activebackground=PANEL, activeforeground=TXT,
            font=FONT_UI, command=self._toggle_clipboard)
        self.chk_clipboard.pack(anchor='w', padx=20)


    def _build_right(self):
        f = self.right

        # 头部标题栏
        hdr = tk.Frame(f, bg=BG, height=38)
        hdr.pack(fill=tk.X)
        hdr.pack_propagate(False)

        self.right_title = tk.Label(hdr, text="📋 控制台日志", bg=BG, fg=TXT,
                                     font=('Microsoft YaHei UI', 11),
                                     anchor='w', padx=12)
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
        scrollbar = tk.Scrollbar(log_frame, command=self.log_text.yview,
                                  bg=BTN, troughcolor=BG, bd=0,
                                  activebackground=BTN_H)
        self.log_text.configure(yscrollcommand=scrollbar.set)
        scrollbar.pack(side=tk.RIGHT, fill=tk.Y)
        self.log_text.pack(side=tk.LEFT, fill=tk.BOTH, expand=True)

        # 日志颜色标签
        self.log_text.tag_configure('ts',     foreground=TXT2)
        self.log_text.tag_configure('act',    foreground=BLUE)
        self.log_text.tag_configure('txt',    foreground=TXT)
        self.log_text.tag_configure('ok',     foreground=GREEN)
        self.log_text.tag_configure('warn',   foreground=YELLOW)
        self.log_text.tag_configure('err',    foreground=RED)
        self.log_text.tag_configure('http',   foreground='#484f58')
        self.log_text.tag_configure('prompt', foreground=CYAN)
        self.log_text.tag_configure('banner', foreground=PURPLE)

        # CLI 输入栏（默认隐藏）
        self.cli_frame = tk.Frame(f, bg=HEADER)

        self.cli_prompt = tk.Label(self.cli_frame, text=" Agent > ",
                                    bg=HEADER, fg=BLUE,
                                    font=FONT_MONO_B, padx=8)
        self.cli_prompt.pack(side=tk.LEFT)

        self.cli_entry = tk.Entry(self.cli_frame, bg=HEADER, fg=TXT,
                                   font=FONT_MONO, bd=0,
                                   insertbackground=TXT,
                                   highlightthickness=0,
                                   highlightcolor=BLUE)
        self.cli_entry.pack(side=tk.LEFT, fill=tk.X, expand=True,
                             padx=(0, 8), pady=7)
        self.cli_entry.bind('<Return>', self._on_cli_enter)

    # ──────── UI 辅助 ────────

    def _sep(self, parent):
        tk.Frame(parent, bg=BORDER, height=1).pack(fill=tk.X, padx=16, pady=10)

    def _btn(self, parent, text, command, fg=TXT, bold=False, disabled=False):
        weight = 'bold' if bold else 'normal'
        state = tk.DISABLED if disabled else tk.NORMAL
        cursor = 'arrow' if disabled else 'hand2'
        btn_fg = DISABLED_FG if disabled else fg

        btn = tk.Button(
            parent, text=text, command=command,
            bg=BTN, fg=btn_fg,
            activebackground=BTN_H, activeforeground=btn_fg,
            font=('Microsoft YaHei UI', 10, weight),
            bd=0, padx=12, pady=8, anchor='w', cursor=cursor,
            state=state,
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
        if (stripped.startswith('===') or stripped.startswith('低配版') or
            stripped.startswith('监听') or stripped.startswith('工作') or
            stripped.startswith('帮助') or stripped.startswith('操作') or
            stripped.startswith('[Agent]')):
            self._append_raw(line, 'banner')
            return

        # 错误堆栈
        low = line.lower()
        if 'traceback' in low or line.startswith('  File ') or line.startswith('    '):
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
            print('[Agent] 正在重启服务...')
            if self._server:
                self._server.shutdown()
                self._server.join(timeout=2)
            self.root.after(800, self._start_server)


    # ──────── 工作目录选择 ────────

    def _select_dir(self):
        path = filedialog.askdirectory(
            initialdir=agent_server.WORK_DIR, title="选择工作目录"
        )
        if path:
            # 修改 agent_server 模块的 WORK_DIR，所有后续指令立即生效
            agent_server.WORK_DIR = path
            self.lbl_dir.configure(text=path)
            print(f'[Agent] 工作目录已更改为: {path}')

    # ──────── 命令行模式 ────────

    def _toggle_cli(self):
        self._cli_mode = not self._cli_mode
        if self._cli_mode:
            self.cli_frame.pack(fill=tk.X, side=tk.BOTTOM)
            self.btn_cli.configure(text="🖥  转到图形面板模式")
            self.right_title.configure(text="⌨ 命令行模式")
            self.cli_entry.focus_set()
            print('[Agent] 已切换到命令行窗口模式 — 可直接输入指令')
        else:
            self.cli_frame.pack_forget()
            self.btn_cli.configure(text="⌨  转到命令行窗口模式")
            self.right_title.configure(text="📋 控制台日志")
            print('[Agent] 已切换到图形面板模式')

    def _on_cli_enter(self, event):
        cmd = self.cli_entry.get().strip()
        if not cmd:
            return
        self._append_raw(f"Agent > {cmd}", 'prompt')
        self.cli_entry.delete(0, tk.END)

        result = agent_server.execute_line(cmd)
        if result:
            for rline in result.split('\n'):
                self._append_raw(rline, 'txt')
        else:
            self._append_raw("（空指令或注释）", 'warn')

    # ──────── 清空日志 ────────

    def _clear_log(self):
        self.log_text.configure(state=tk.NORMAL)
        self.log_text.delete('1.0', tk.END)
        self.log_text.configure(state=tk.DISABLED)

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


    def _make_permission_callback(self):
        gui_ref = self

        def callback(cmd, filepath):
            event = threading.Event()
            result = [False]

            def ask():
                dialog = tk.Toplevel(gui_ref.root)
                dialog.title("⚠ 路径权限请求")
                dialog.configure(bg=BG)
                dialog.resizable(False, False)
                dialog.transient(gui_ref.root)
                dialog.grab_set()

                gui_ref.root.update_idletasks()
                dw, dh = 440, 240
                rx = gui_ref.root.winfo_x() + (gui_ref.root.winfo_width() - dw) // 2
                ry = gui_ref.root.winfo_y() + (gui_ref.root.winfo_height() - dh) // 2
                dialog.geometry(f'{dw}x{dh}+{rx}+{ry}')

                tk.Label(dialog, text="⚠", bg=BG, fg=YELLOW,
                         font=('Microsoft YaHei UI', 28)).pack(pady=(14, 2))
                tk.Label(dialog, text="路径超出工作目录",
                         bg=BG, fg=TXT,
                         font=FONT_UI_B).pack()

                info = tk.Frame(dialog, bg=HEADER)
                info.pack(fill=tk.X, padx=16, pady=8)

                tk.Label(info, text=f"指令: {cmd}",
                         bg=HEADER, fg=RED, font=FONT_MONO,
                         anchor='w').pack(fill=tk.X, padx=10, pady=(6, 2))
                tk.Label(info, text=f"目标: {filepath}",
                         bg=HEADER, fg=TXT, font=FONT_MONO,
                         anchor='w', wraplength=400).pack(fill=tk.X, padx=10)
                tk.Label(info, text=f"工作目录: {agent_server.WORK_DIR}",
                         bg=HEADER, fg=TXT2, font=('Consolas', 9),
                         anchor='w').pack(fill=tk.X, padx=10, pady=(2, 6))

                bf = tk.Frame(dialog, bg=BG)
                bf.pack(fill=tk.X, padx=16, pady=(0, 14))

                def close(val):
                    result[0] = val
                    event.set()
                    dialog.destroy()

                tk.Button(
                    bf, text="✕ 拒绝", command=lambda: close(False),
                    bg='#3d1f1f', fg=RED, activebackground='#4d2525',
                    activeforeground=RED, font=FONT_UI,
                    bd=0, padx=10, pady=6, cursor='hand2'
                ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(0, 4))

                tk.Button(
                    bf, text="✓ 允许一次", command=lambda: close(True),
                    bg='#1f3d1f', fg=GREEN, activebackground='#254d25',
                    activeforeground=GREEN, font=FONT_UI,
                    bd=0, padx=10, pady=6, cursor='hand2'
                ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=4)

                tk.Button(
                    bf, text="✓ 始终允许", command=lambda: close('always'),
                    bg='#1f2d3d', fg=BLUE, activebackground='#253d4d',
                    activeforeground=BLUE, font=FONT_UI,
                    bd=0, padx=10, pady=6, cursor='hand2'
                ).pack(side=tk.LEFT, expand=True, fill=tk.X, padx=(4, 0))

            gui_ref.root.after_idle(ask)
            event.wait(timeout=120)
            if event.is_set():
                count = len(agent_server.permission_mgr._always_allow)
                gui_ref.root.after_idle(
                    lambda: gui_ref.lbl_allow_count.configure(
                        text=f"始终允许: {count} 条"))
                return result[0]
            return False

        return callback


    # ──────── 暗色标题栏 (Win11) ────────

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

    # ──────── 关闭 ────────

    def _on_close(self):
        if self._server:
            self._server.shutdown()
        sys.stdout = self._orig_stdout
        sys.stderr = self._orig_stderr
        self.root.destroy()

    def run(self):
        self.root.mainloop()

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  入口
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
if __name__ == '__main__':
    gui = AgentGUI()
    gui.run()

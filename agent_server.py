"""
PokerAgent - 本地接应服务 (SSE流式版) v43
启动方式： python agent_server.py
默认监听：http://127.0.0.1:9966
"""
from flask import Flask, request, jsonify, Response
from flask_cors import CORS
import os
import subprocess
import urllib.request
import urllib.error
import re
import inspect
import threading
import base64
import difflib  # 用于 -s 模式的模糊匹配策略
import shutil  # 用于移动文件/目录到回收站
import time  # 用于回收站时间戳记录
import locale  # 获取系统默认编码
import platform  # 用于判断操作系统
import uuid
import queue
import json
import sys
app = Flask(__name__)
CORS(app)
# 工作目录：脚本所在目录
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
def get_temp_dir():
    """获取当前工作目录下的临时文件夹路径（动态跟随 WORK_DIR）"""
    return os.path.join(WORK_DIR, '.agent_temp_files')
# 帮助文档路径
HELP_FILE = os.path.join(WORK_DIR, 'commands.md')
# [新增] 专属回收站目录
TRASH_DIR = os.path.join(WORK_DIR, '.agent_trash')
# 配置文件路径（固定在脚本所在目录，不随 WORK_DIR 变化）
CONFIG_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), 'agent_config.json')
# 操作日志
LOG_FILE = os.path.join(WORK_DIR, 'agent_log.txt')
clipboard_mode = False
exec_enabled = True
# [新增] Shell 类型：'powershell'（默认）或 'cmd'，可通过配置文件切换
shell_type = 'powershell'
_config_changed = threading.Event()
# [修改] Windows 的 cmd 默认输出是 GBK，Linux/Mac 是 UTF-8
encoding = 'gbk' if platform.system() == 'Windows' else 'utf-8'
_SYS_ENCODING = locale.getpreferredencoding(False) or 'gbk'
# [新增] 检测系统可用的 PowerShell：优先 pwsh (7+)，回退 powershell (5.x)
def _detect_powershell():
    if shutil.which('pwsh'):
        return 'pwsh'
    if shutil.which('powershell'):
        print('[Agent] ⚠ 未检测到 PowerShell 7+ (pwsh)，已回退到 Windows PowerShell 5.x。'
              '建议更新: https://github.com/PowerShell/PowerShell/releases')
        return 'powershell'
    print('[Agent] ⚠ 未检测到任何 PowerShell，exec 将回退到 cmd。')
    return None
_POWERSHELL_EXE = _detect_powershell()
# ========== 记忆系统配置 ==========
MEMORY_TEMP_INITIAL = 100  # 新记忆初始温度（决定新旧记忆的淘汰压力）。
MEMORY_TEMP_DECAY_RATIO = 0.95  # 每轮衰减比例（保留95%，即衰减5%）。
MEMORY_TEMP_HEAT_RATIO = 0.5  # 被读取时向初始温度回归的比例（极冷数据飙升）。
MEMORY_EXPOSE_WINDOW = 20  # Tag 云暴露的记忆条数（温度Top-N）。
MEMORY_READ_WINDOW = 2  # memory search 上下额外返回的记忆条数。
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 任务队列与 SSE 流式架构
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
task_queue = queue.Queue()
# [新增] 任务控制共享状态（GUI 按钮 → Worker 线程）
_current_process = None  # 当前正在执行的子进程引用
_current_process_lock = threading.Lock()
_pause_event = threading.Event()  # set=运行中, clear=暂停
_pause_event.set()  # 初始为运行状态
_kill_mode = None  # None / 'discard' / 'done'
_kill_mode_lock = threading.Lock()
_current_task_id = None  # 当前正在执行的任务ID
# [新增] 全局中断信号：request_kill 时 set，worker 取新任务前 clear
_abort_event = threading.Event()
# [新增] 任务中断异常：在任何检查点命中时抛出，worker_loop 统一捕获
class TaskAborted(Exception):
    pass
def _check_abort():
    """检查中断信号，命中则抛出 TaskAborted（在耗时操作间调用）"""
    if _abort_event.is_set():
        raise TaskAborted()
sse_clients = []  # 存放所有连接的 SSE 客户端队列
_sse_lock = threading.Lock()  # 保护 sse_clients 的锁
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 任务状态注册表（解决 SSE 晚订阅竞态：新客户端连接时回放历史状态）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_task_registry = {}  # task_id -> {'status':..., 'logs':[...], 'result':...}
_task_registry_lock = threading.Lock()
def emit_task_event(evt):
    """更新任务注册表并推送给所有已连接的 SSE 客户端（SSE 侧自动剥离 ANSI 颜色码）"""
    task_id = evt.get('id')
    if task_id:
        with _task_registry_lock:
            if task_id not in _task_registry:
                _task_registry[task_id] = {'status': 'waiting', 'logs': [], 'result': ''}
            entry = _task_registry[task_id]
            if evt.get('type') == 'status':
                entry['status'] = evt.get('status', entry['status'])
                if 'result' in evt:
                    entry['result'] = strip_ansi(evt['result'])  # [修改] 存储剥离
            elif evt.get('type') == 'log':
                entry['logs'].append(strip_ansi(evt.get('data', '')))  # [修改] 存储剥离
    # [修改] 推送前剥离 ANSI，前端/LLM 拿到干净文本
    if evt.get('type') == 'log' and 'data' in evt:
        evt = dict(evt, data=strip_ansi(evt['data']))
    elif evt.get('type') == 'status' and 'result' in evt:
        evt = dict(evt, result=strip_ansi(evt['result']))
    push_event(evt)
def push_event(data_dict):
    """向所有连接的 SSE 客户端推送事件"""
    msg = f"data: {json.dumps(data_dict, ensure_ascii=False)}\n\n"
    with _sse_lock:
        clients = list(sse_clients)  # 拷贝一份再遍历，避免竞态
        for q in clients:
            q.put(msg)
# [新增] 暴力终止子进程树（跨平台，供 request_kill 和超时逻辑复用）
def _kill_process_tree(proc):
    """kill → taskkill 双保险，确保进程树死透"""
    if proc is None or proc.poll() is not None:
        return
    try:
        proc.kill()  # 先 Python 层 kill
    except Exception:
        pass
    if platform.system() == 'Windows':
        try:
            # /F 强制 /T 杀整棵树（含 daemon 子进程）
            subprocess.run(f'taskkill /F /T /PID {proc.pid}', shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception:
            pass
# [新增] 任务控制接口（供 GUI 调用）
def request_kill(mode):
    """请求终止当前任务。mode: 'discard'=丢弃结果 / 'done'=返回已有输出"""
    global _kill_mode
    if _current_task_id is None:
        return False  # 没有正在执行的任务，忽略
    with _kill_mode_lock:
        _kill_mode = mode
    # 先 set 中断信号，让所有纯 Python 循环立即抛出 TaskAborted
    _abort_event.set()
    # 再暴力杀子进程 + 关闭管道，解除读取线程的 readline 阻塞
    with _current_process_lock:
        proc = _current_process
        if proc:
            _kill_process_tree(proc)  # [修改] 用统一的暴力杀进程函数
            # 主动关闭 stdout 管道，让读取线程的 readline 立即收到异常/EOF
            try:
                if proc.stdout:
                    proc.stdout.close()
            except Exception:
                pass
    return True
def request_pause():
    """暂停任务队列（当前任务继续执行完，不再取新任务）"""
    _pause_event.clear()
def request_resume():
    """恢复任务队列"""
    _pause_event.set()
def worker_loop():
    """后台 Worker 线程：严格串行执行任务"""
    global _current_task_id, _kill_mode
    import datetime
    print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] 🔧 Worker 线程已启动，等待任务...')
    while True:
        try:
            # [新增] 暂停检查：暂停时阻塞，恢复后继续
            _pause_event.wait()
            # [修改] 带超时的 get，确保暂停信号能及时生效（不会卡在无限阻塞的 get 上）
            try:
                task = task_queue.get(timeout=0.5)
            except queue.Empty:
                continue  # 超时回循环顶部，重新检查暂停状态
            if task is None:
                print('[Worker] 收到退出信号，线程结束')
                break
            task_id = task['id']
            cmd_str = task['cmd']
            _abort_event.clear()  # [新增] 新任务开始前清除中断信号
            _current_task_id = task_id  # [新增] 记录当前任务
            print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] ⚙️ Worker 取出任务 {task_id[:8]}: {cmd_str}')
            emit_task_event({'id': task_id, 'type': 'status', 'status': 'running'})
            try:
                result = execute_line_streaming(cmd_str, task_id)
            except TaskAborted:  # [新增] 任务被用户手动中断
                result = None
                print(f'[Worker] ⛔ 任务 {task_id[:8]} 被用户手动中断')
            except Exception as e:
                import traceback
                print(f'[Worker] ❌ 执行异常: {e}')
                traceback.print_exc()
                result = f'执行异常：{e}'
            # [新增] 检查终止模式并重置
            with _kill_mode_lock:
                mode = _kill_mode
                _kill_mode = None
            _current_task_id = None
            _result_str = str(result) if result is not None else ''
            _ts = datetime.datetime.now().strftime("%H:%M:%S")
            if mode == 'discard':
                # [修改] 丢弃：emit killed 状态让前端知道任务已终止
                print(f'[{_ts}] ⛔ 任务 {task_id[:8]} 已终止并丢弃')
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'killed',
                                 'result': '当前任务已被用户手动终止（结果已丢弃）'})
            elif mode == 'done':
                # [修改] 终止但返回已有输出，前面加提示
                print(f'[{_ts}] ⛔ 任务 {task_id[:8]} 已终止，返回已有输出:')
                _notice = '当前任务已被用户手动终止。以下为终止前的已有输出：\n'
                _final = _notice + _result_str if _result_str else _notice + '（无输出）'
                if _result_str:
                    print(_result_str)
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'done', 'result': _final})
            else:
                # 正常完成（多行回执分行显示）
                if '\n' in _result_str:
                    print(f'[{_ts}] ✅ 任务 {task_id[:8]} 完成:')
                    print(_result_str)
                else:
                    print(f'[{_ts}] ✅ 任务 {task_id[:8]} 完成: {_result_str}')
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'done', 'result': result})
        except Exception as e:
            print(f'[Worker] 致命错误: {e}')
def smart_read(filepath):
    """智能读取：优先 UTF-8 (含BOM)，失败回退系统默认编码(如 GBK)，保底 latin-1"""
    _check_abort()  # [新增] 读取前检查中断（覆盖所有调用 smart_read 的指令）
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            return f.read(), 'utf-8-sig'
    except UnicodeDecodeError:
        pass
    try:
        with open(filepath, 'r', encoding=_SYS_ENCODING) as f:
            return f.read(), _SYS_ENCODING
    except (UnicodeDecodeError, LookupError):
        pass
    with open(filepath, 'r', encoding='latin-1') as f:
        return f.read(), 'latin-1'
def smart_write(filepath, content, encoding):
    """智能写入：根据原编码格式写入，但避免给无BOM文件强加BOM"""
    # [修改] 如果原编码是utf-8-sig，检查原文件是否真有BOM
    # smart_read 对有BOM和无BOM的utf-8文件都返回'utf-8-sig'，
    # 因此需要检查原文件是否真有BOM，避免给无BOM文件强加BOM
    if encoding == 'utf-8-sig':
        had_bom = False
        if os.path.exists(filepath):
            try:
                with open(filepath, 'rb') as f:
                    had_bom = f.read(3) == b'\xef\xbb\xbf'
            except Exception:
                pass
        if not had_bom:
            encoding = 'utf-8'
    with open(filepath, 'w', encoding=encoding) as f:
        f.write(content)
def smart_decode(b_str):
    """智能解码：优先UTF-8，失败则回退GBK"""
    if not b_str:
        return ''
    try:
        return b_str.decode('utf-8')
    except UnicodeDecodeError:
        return b_str.decode(encoding, errors='replace')
# [新增] 剥离 ANSI 转义序列（PowerShell 7 默认输出颜色码，GUI/日志无法渲染）
_ANSI_RE = re.compile(r'\x1b\[[0-9;]*[a-zA-Z]')
def strip_ansi(s):
    return _ANSI_RE.sub('', s)
def save_config():
    """将当前运行时配置持久化到 JSON 文件"""
    config = {
        'work_dir': WORK_DIR,
        'clipboard_mode': clipboard_mode,
        'exec_enabled': exec_enabled,
        'shell_type': shell_type,  # [新增]
        'permission_enabled': permission_mgr.enabled,
        'always_allow': list(permission_mgr._always_allow),  # [新增]
        'memory_temp_initial': MEMORY_TEMP_INITIAL,
        'memory_temp_decay_ratio': MEMORY_TEMP_DECAY_RATIO,
        'memory_temp_heat_ratio': MEMORY_TEMP_HEAT_RATIO,
        'memory_expose_window': MEMORY_EXPOSE_WINDOW,
        'memory_read_window': MEMORY_READ_WINDOW,
    }
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[Agent] 配置保存失败: {e}')
def load_config():
    """启动时从 JSON 文件加载配置，文件不存在或损坏则静默使用默认值"""
    global WORK_DIR, TRASH_DIR, clipboard_mode, exec_enabled, shell_type
    global MEMORY_TEMP_INITIAL, MEMORY_TEMP_DECAY_RATIO, MEMORY_TEMP_HEAT_RATIO
    global MEMORY_EXPOSE_WINDOW, MEMORY_READ_WINDOW
    if not os.path.exists(CONFIG_FILE):
        return
    try:
        with open(CONFIG_FILE, 'r', encoding='utf-8') as f:
            config = json.load(f)
        # 工作目录：仅在路径实际存在时才采用
        if 'work_dir' in config and os.path.isdir(config['work_dir']):
            WORK_DIR = config['work_dir']
            TRASH_DIR = os.path.join(WORK_DIR, '.agent_trash')
        if 'clipboard_mode' in config:
            clipboard_mode = bool(config['clipboard_mode'])
        if 'exec_enabled' in config:
            exec_enabled = bool(config['exec_enabled'])
        if 'shell_type' in config and config['shell_type'] in ('powershell', 'cmd'):
            shell_type = config['shell_type']
        if 'permission_enabled' in config:
            permission_mgr.enabled = bool(config['permission_enabled'])
        if 'always_allow' in config:
            permission_mgr._always_allow = set(config['always_allow'])
        if 'memory_temp_initial' in config:
            MEMORY_TEMP_INITIAL = int(config['memory_temp_initial'])
        if 'memory_temp_decay_ratio' in config:
            MEMORY_TEMP_DECAY_RATIO = float(config['memory_temp_decay_ratio'])
        if 'memory_temp_heat_ratio' in config:
            MEMORY_TEMP_HEAT_RATIO = float(config['memory_temp_heat_ratio'])
        if 'memory_expose_window' in config:
            MEMORY_EXPOSE_WINDOW = int(config['memory_expose_window'])
        if 'memory_read_window' in config:
            MEMORY_READ_WINDOW = int(config['memory_read_window'])
        print(f'[Agent] 配置已加载: {CONFIG_FILE}')
    except Exception as e:
        print(f'[Agent] 配置加载失败，使用默认值: {e}')
def _push_config():
    save_config()
    # 每次配置变更时持久化
    _config_changed.set()
def log_action(action, detail=''):
    import datetime
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    line = f'[{timestamp}] {action}'
    if detail:
        line += f' | {detail}'
    line += '\n'
    with open(LOG_FILE, 'a', encoding='utf-8') as f:
        f.write(line)
    print(line.strip())
def safe_path(base, path):
    if os.path.isabs(path):
        return os.path.normpath(path)
    return os.path.normpath(os.path.join(base, path))
def parse_args_with_quotes(s):
    """解析命令参数，支持双引号包裹含空格的参数。"""
    args = []
    current = []
    in_quote = False
    for char in s:
        if char == '"':
            if in_quote:
                in_quote = False
                args.append(''.join(current))
                current = []
            else:
                in_quote = True
        elif char == ' ' and not in_quote:
            if current:
                args.append(''.join(current))
                current = []
        else:
            current.append(char)
    if current:
        args.append(''.join(current))
    return args
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 路径权限管理器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class PermissionManager:
    def __init__(self):
        self._callback = None
        self._always_allow = set()
        self._lock = threading.Lock()
        self.enabled = True
    def set_callback(self, fn):
        self._callback = fn
    def _is_within(self, filepath):
        work = os.path.normpath(WORK_DIR).lower()
        fp = os.path.normpath(filepath).lower()
        return fp == work or fp.startswith(work + os.sep)
    def check(self, cmd, filepath):
        if not self.enabled or not filepath:
            return True
        if self._is_within(filepath):
            return True
        fp_norm = os.path.normpath(filepath).lower()
        with self._lock:
            for allowed in self._always_allow:
                if fp_norm == allowed or fp_norm.startswith(allowed + os.sep):
                    return True
        if self._callback:
            result = self._callback(cmd, filepath)
            if result == 'always':
                with self._lock:
                    self._always_allow.add(fp_norm)
                save_config()  # 新增始终允许条目后持久化
                return True
            return bool(result)
        return False
    def reset_session(self):
        with self._lock:
            self._always_allow.clear()
        save_config()  # 清除始终允许列表后持久化
permission_mgr = PermissionManager()
# [新增] 判断路径是否在回收站内
def _is_trash_path(filepath):
    if not filepath:
        return False
    trash_norm = os.path.normpath(TRASH_DIR).lower()
    fp_norm = os.path.normpath(filepath).lower()
    return fp_norm == trash_norm or fp_norm.startswith(trash_norm + os.sep)
# [新增] 将原路径映射为回收站内的存储路径
def _get_trash_path(filepath):
    norm_work = os.path.normpath(WORK_DIR)
    norm_fp = os.path.normpath(filepath)
    # 如果在工作目录内，保持相对层级
    if norm_fp.lower().startswith(norm_work.lower() + os.sep):
        rel_path = os.path.relpath(norm_fp, norm_work)
        return os.path.join(TRASH_DIR, rel_path)
    # 如果在工作目录外，统一塞进 __external__ 并去掉盘符冒号
    else:
        drive, path_no_drive = os.path.splitdrive(norm_fp)
        drive_clean = drive.replace(':', '') if drive else 'no_drive'
        return os.path.join(TRASH_DIR, '__external__', drive_clean, path_no_drive.strip(os.sep))
# [新增] 从回收站路径反推原始绝对路径
def _get_original_path(trash_path):
    norm_trash = os.path.normpath(TRASH_DIR)
    norm_tp = os.path.normpath(trash_path)
    rel_path = os.path.relpath(norm_tp, norm_trash)
    if rel_path.startswith('__external__'):
        parts = rel_path.split(os.sep)
        if len(parts) < 3:
            return None
        drive = parts[1] + ':'
        return os.path.join(drive, *parts[2:])
    else:
        return os.path.normpath(os.path.join(WORK_DIR, rel_path))
def _match_text_block(file_lines, old_lines, ignore_case=False, ignore_indent=False, normalize_ws=False, fuzzy_threshold=None):
    """
    通用文本块匹配方法，支持组合匹配条件。
    返回匹配的起始索引列表(0-based)。
    """
    def _process(line):
        if ignore_case:
            line = line.lower()
        if ignore_indent:
            line = line.strip()
        if normalize_ws:
            line = re.sub(r'\s+', ' ', line).strip()
        return line
    proc_file = [_process(l) for l in file_lines]
    proc_old = [_process(l) for l in old_lines]
    matches = []
    num_old = len(proc_old)
    if num_old == 0:
        return matches
    for i in range(len(proc_file) - num_old + 1):
        is_match = True
        # 模糊匹配逻辑
        if fuzzy_threshold is not None:
            total_sim = 0.0
            for j in range(num_old):
                # 完全一致直接算1.0，避免计算开销
                if proc_old[j] == proc_file[i + j]:
                    total_sim += 1.0
                else:
                    total_sim += difflib.SequenceMatcher(None, proc_old[j], proc_file[i + j]).ratio()
            avg_sim = total_sim / num_old
            if avg_sim < fuzzy_threshold:
                is_match = False
        # 精确/归一化匹配逻辑
        else:
            for j in range(num_old):
                if proc_old[j] != proc_file[i + j]:
                    is_match = False
                    break
        if is_match:
            matches.append(i)
    return matches
def _check_permission(cmd, *paths):
    # [新增] 拦截对专属回收站的非授权访问
    if cmd not in ('delete', 'restore'):
        for p in paths:
            if p and _is_trash_path(p):
                return f'操作被拒绝：禁止访问专属回收站目录 — {p}'
    for p in paths:
        if p and not permission_mgr.check(cmd, p):
            return f'操作被拒绝：路径超出工作目录 — {p}'
    return None
def _default_permission_callback(cmd, filepath):
    print(f'\n⚠ 路径超出工作目录!')
    print(f'  指令: {cmd}')
    print(f'  目标: {filepath}')
    print(f'  工作目录: {WORK_DIR}')
    while True:
        ans = input('  是否允许? [y=允许/n=拒绝/a=本次会话始终允许]: ').strip().lower()
        if ans in ('y', 'yes'):
            return True
        elif ans in ('n', 'no'):
            return False
        elif ans in ('a', 'always'):
            return 'always'
        else:
            print('  请输入 y, n 或 a')
# 兼容 GUI CLI 模式的壳函数
def execute_line(line):
    return execute_line_streaming(line, 'cli-manual')
def execute_line_streaming(line, task_id):
    """统一执行核心：支持实时推送 exec/run 的日志"""
    global _current_process
    _check_abort()  # [新增] 入口处检查：若中断信号已激活则拒绝执行
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    parts = line.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ''
    arg = arg.replace('"', '"').replace('"', '"')
    cs_idx = arg.find('【CodeSTART】')
    if cs_idx != -1:
        arg = arg[:cs_idx]
    W = WORK_DIR
    if cmd == '@@help':
        # [修改] 智能帮助查询系统：支持 all / fast / [指令名] 三种模式
        if not os.path.exists(HELP_FILE):
            return 'commands.md 文件未找到，请寻找管理员确认它与此脚本在同一目录下。'
        with open(HELP_FILE, 'r', encoding='utf-8') as f:
            help_content = f.read()
        # 解析参数（如果有）
        arg_lower = arg.strip().lower() if arg.strip() else ''
        # 情况1：无参数或 'all' - 返回完整内容（保持原功能）
        if not arg_lower or arg_lower == 'all':
            return help_content
        # 情况2：'fast' - 返回"指令快速预览列表和说明"章节（两个 --- 之间的内容）
        elif arg_lower == 'fast':
            section_title = '## 指令快速预览列表和说明'
            lines = help_content.splitlines(keepends=True)
            title_idx = -1
            # 找到标题行
            for i, ln in enumerate(lines):
                if ln.strip() == section_title:
                    title_idx = i
                    break
            if title_idx == -1:
                return f'在帮助文档中未找到章节：{section_title}'
            # 从标题向下查找第一个 '---'
            first_dash_idx = -1
            for i in range(title_idx + 1, len(lines)):
                if lines[i].strip() == '---':
                    first_dash_idx = i
                    break
            # 从标题向上查找前一个 '---'（或文件开头）
            second_dash_idx = -1
            for i in range(title_idx - 1, -1, -1):
                if lines[i].strip() == '---':
                    second_dash_idx = i
                    break
            # 确定截取范围
            start_idx = second_dash_idx + 1 if second_dash_idx != -1 else 0
            end_idx = first_dash_idx if first_dash_idx != -1 else len(lines)
            # 截取内容
            section_content = ''.join(lines[start_idx:end_idx]).strip()
            if not section_content:
                return f'章节"{section_title}"内容为空。'
            return section_content
        # 情况3：[指令名] - 返回指定指令的详细说明
        else:
            # 获取指令名
            cmd_name = arg.strip().lower()
            # 构建要查找的标题前缀（如 '### replace'）
            # 标题格式通常为：### 指令名 参数说明
            target_prefix = f'### {cmd_name}'
            lines = help_content.splitlines(keepends=True)
            header_idx = -1
            # 查找指令标题行（行开头匹配前缀）
            for i, line in enumerate(lines):
                clean_line = line.strip().lower()
                # 检查是否以目标前缀开头
                if clean_line.startswith(target_prefix):
                    # 为了防止误匹配（例如 'replace' 匹配到 'replaceall'），
                    # 检查前缀后的字符必须是空格或行结束
                    next_char_idx = len(target_prefix)
                    if next_char_idx == len(clean_line) or clean_line[next_char_idx] == ' ':
                        header_idx = i
                        break
            if header_idx == -1:
                return f'未找到指令 "{cmd_name}" 的帮助信息。请检查指令名称是否正确。'
            # 从标题向下查找，直到遇到下一个以 '###' 开头的行或文件结束
            end_idx = len(lines)
            for i in range(header_idx + 1, len(lines)):
                if lines[i].strip().startswith('###'):
                    end_idx = i
                    break
            # 截取指令详细内容
            cmd_detail = ''.join(lines[header_idx:end_idx]).strip()
            if not cmd_detail:
                return f'指令 "{cmd_name}" 的帮助信息为空。'
            return cmd_detail
    # [新增] start 指令：返回后端运行时环境和设置，供 LLM 初始化上下文
    elif cmd == 'start':
        # 现用现查 PowerShell 版本（pwsh 7+ 支持 --version，5.x 不支持需走 -Command）
        if _POWERSHELL_EXE == 'pwsh':
            _ps_cmd = 'pwsh --version'
            try:
                _ps_ver = subprocess.run(
                    ['pwsh', '--version'], capture_output=True, text=True, timeout=5
                ).stdout.strip()
            except Exception:
                _ps_ver = 'pwsh (版本获取失败)'
        elif _POWERSHELL_EXE == 'powershell':
            _ps_cmd = 'powershell -NoProfile -NonInteractive -Command $PSVersionTable.PSVersion.ToString()'
            try:
                _raw = subprocess.run(
                    ['powershell', '-NoProfile', '-NonInteractive', '-Command',
                     '$PSVersionTable.PSVersion.ToString()'],
                    capture_output=True, text=True, timeout=5
                ).stdout.strip()
                _ps_ver = f'Windows PowerShell {_raw}'
            except Exception:
                _ps_ver = 'powershell (版本获取失败)'
        else:
            _ps_cmd = 'pwsh --version'
            _ps_ver = '未检测到 PowerShell'
        # Python 版本（platform 模块直接取，无需起子进程）
        _py_ver = f'Python {platform.python_version()}'
        # 拼接返回：中文标签 + JSON 键值 + 底部实际命令及输出
        _lines = [
            '{',
            f'  当前工作目录        "work_dir": "{WORK_DIR}",',
            f'  剪贴板读取模式      "clipboard_mode": {str(clipboard_mode).lower()},',
            f'  系统命令执行开关    "exec_enabled": {str(exec_enabled).lower()},',
            f'  终端类型            "shell_type": "{shell_type}",',
            f'  目录权限限制开关    "permission_enabled": {str(permission_mgr.enabled).lower()},',
            f'  始终允许列表条目数  "always_allow_count": {len(permission_mgr._always_allow)},',
            f'  操作系统            "platform": "{platform.system()}"',
            '',
            f'>{_ps_cmd}',
            _ps_ver,
            '>python --version',
            _py_ver,
            '}',
        ]
        return '\n'.join(_lines)
    # ========== 记忆系统指令 ==========
    elif cmd == 'remember':
        # 短期记忆：覆盖写入 .agent/remember.md
        # 空参数 = 清空短期记忆
        # [修复] 多行代码块：agent-exec 将其拼接为 "参数\x00代码块"，
        # 原实现未拆分导致 \x00 字符与内容混杂写入记忆文件
        block = ''
        if '\x00' in arg:
            arg, block = arg.split('\x00', 1)
        arg = arg.strip()
        block = block.strip('\n').replace('TICK3', '```')
        content = arg.replace('TICK3', '```').strip()
        if block:
            content = (content + '\n' + block) if content else block
        memory_engine.write_short(content)
        if content:
            return '已更新短期记忆。'
        else:
            return '已清空短期记忆。'
    elif cmd == 'memory':
        # 长期记忆指令：支持多种子命令
        raw_arg = arg.strip()
        # [修复] 多行代码块：拆分 "\x00" 分隔的代码块（原样混入导致记忆内容损坏）。
        # inline 部分承载内容前缀与 tag:/-pin 修饰符，代码块追加到内容末尾
        mem_block = ''
        if '\x00' in raw_arg:
            raw_arg, mem_block = raw_arg.split('\x00', 1)
        raw_arg = raw_arg.strip()
        mem_block = mem_block.strip('\n').replace('TICK3', '```')
        # [修改] 仅 block 无 inline 参数时也放行（原来是直接报缺参数）
        if not raw_arg and not mem_block:
            return '错误：memory 指令缺少参数。发送 @@help memory 获取指令详细用法'
        # ── 子命令：search ──
        if raw_arg.lower().startswith('search'):
            keyword = raw_arg[6:].strip()
            if not keyword:
                return '错误：memory search 需要指定搜索关键词。'
            result = memory_engine.search(keyword)
            return result
        # ── 子命令：del ──
        if raw_arg.lower().startswith('del'):
            id_str = raw_arg[3:].strip()
            ids = memory_engine._parse_ids(id_str)
            if not ids:
                return '错误：memory del 需要指定至少一个记忆ID。'
            deleted = memory_engine.delete_by_ids(ids)
            return f'已删除 {deleted} 条记忆。'
        # ── 子命令：pin ──
        if raw_arg.lower().startswith('pin'):
            id_str = raw_arg[3:].strip()
            ids = memory_engine._parse_ids(id_str)
            if not ids:
                return '错误：memory pin 需要指定至少一个记忆ID。'
            pinned = memory_engine.pin_by_ids(ids)
            return f'已固定 {pinned} 条记忆。'
        # ── 子命令：unpin ──
        if raw_arg.lower().startswith('unpin'):
            id_str = raw_arg[5:].strip()
            ids = memory_engine._parse_ids(id_str)
            if not ids:
                return '错误：memory unpin 需要指定至少一个记忆ID。'
            unpinned = memory_engine.unpin_by_ids(ids)
            return f'已取消固定 {unpinned} 条记忆。'
        # ── 判断是否为覆盖写入 ──
        # 第一个 token 是纯数字 且 该ID已存在 → 覆盖写入
        # 否则 → 新增写入（防止 "memory 23号的改动..." 中的 23 被误识别为ID）
        first_token = raw_arg.split(None, 1)[0] if raw_arg else ''
        if first_token.isdigit():
            mem_id = int(first_token)
            if memory_engine.memory_exists(mem_id):
                # 覆盖写入模式
                rest = raw_arg[len(first_token):].strip()
                if not rest:
                    return '错误：memory <id> 覆盖写入需要指定内容。'
                # [修改] 末尾参数统一走解析器（与新增写入一致，新增 temp:N 支持）
                content, tags, pin, custom_temp = _parse_memory_params(rest)
                content = content.replace('TICK3', '```')
                # [修复] 多行代码块内容并入（空内容判断移到并入之后）
                if mem_block:
                    content = (content + '\n' + mem_block) if content else mem_block
                if not content:
                    return '错误：memory <id> 覆盖写入内容为空。'
                success = memory_engine.overwrite_by_id(mem_id, content, tags, pin, custom_temp)
                if success:
                    return f'已覆盖写入长期记忆，编号 {mem_id:03d}'
                else:
                    return f'错误：未找到编号为 {mem_id:03d} 的记忆。'
            # ID不存在 → fall through 到新增写入模式
        # ── 新增写入模式 ──
        raw = raw_arg.replace('TICK3', '```')
        # [修改] 末尾参数（-pin / temp:N / tag:）统一走解析器，任意顺序组合
        content, tags, pin, custom_temp = _parse_memory_params(raw)
        # [修复] 多行代码块内容并入（空内容判断移到并入之后）
        if mem_block:
            content = (content + '\n' + mem_block) if content else mem_block
        if not content:
            return '错误：memory 指令内容为空。'
        mem_id = memory_engine.write_long(content, tags, pin, custom_temp)
        return f'已存入长期记忆，编号 {mem_id:03d}'
    elif cmd == 'count':
        if not arg.strip():
            return '错误：缺少文件路径。发送 @@help count 获取指令详细用法'
        p_args = parse_args_with_quotes(arg.strip())
        if not p_args:
            return '错误：缺少文件路径。发送 @@help count 获取指令详细用法'
        filepath = safe_path(W, p_args[0])
        err = _check_permission('count', filepath)
        if err:
            return err
        try:
            content, _ = smart_read(filepath)
            lines = content.splitlines()
            chars = len(content)
            words = len(re.findall(r'[\u4e00-\u9fff]|[a-zA-Z0-9]+', content))
            log_action('COUNT', filepath)
            return (
                f'文件统计：{filepath}\n'
                f'  行数：{len(lines)}\n'
                f'  字数（中英文混合）：{words}\n'
                f'  字符数（含空白）：{chars}')
        except Exception as e:
            return f'统计失败：{e}'
    elif cmd == 'find':
        # [重构] 严格按是否包含 \x00 (代码块) 分流：有代码块走内容查找，无代码块走文件名查找
        if '\x00' in arg:
            # --- 模式一：文件内容查找 (路径必须为文件) ---
            opts_str, search_text = arg.split('\x00', 1)
            tokens = parse_args_with_quotes(opts_str.strip())
            if not tokens:
                return '错误：缺少文件路径。发送 @@help find 获取指令详细用法'
            filepath = safe_path(W, tokens[0])
            flags = tokens[1:] if len(tokens) > 1 else []
            # 解析修饰参数
            use_regex = '-r' in flags
            partial = '-p' in flags
            ignore_case = '-i' in flags
            # 清理首尾换行，保留原始缩进
            search_text = search_text.strip('\n')
            if not search_text:
                return '错误：查找内容为空。'
            err = _check_permission('find', filepath)
            if err:
                return err
            if os.path.isdir(filepath):
                return f'错误：内容查找模式下，目标必须是文件，不能是目录 - {filepath}'
            if not os.path.isfile(filepath):
                return f'错误：文件不存在 - {filepath}'
            try:
                content, _ = smart_read(filepath)
                file_lines = content.splitlines()
                search_lines = search_text.split('\n')
                num_search = len(search_lines)
                # 预编译正则表达式（如果开启 -r）
                compiled_patterns = []
                if use_regex:
                    re_flags = re.IGNORECASE if ignore_case else 0
                    for sl in search_lines:
                        try:
                            compiled_patterns.append(re.compile(sl, re_flags))
                        except re.error as e:
                            return f'错误：无效的正则表达式 - {sl} ({e})'
                results = []
                # 遍历文件行，寻找连续匹配的块
                for i in range(len(file_lines) - num_search + 1):
                    if i % 500 == 0:
                        _check_abort()  # [新增] 每 500 行检查一次
                    matched_all = True
                    for j in range(num_search):
                        file_line = file_lines[i + j]
                        search_line = search_lines[j]
                        if use_regex:
                            pat = compiled_patterns[j]
                            m = pat.search(file_line) if partial else pat.fullmatch(file_line)
                            if not m:
                                matched_all = False
                                break
                        else:
                            cmp_file = file_line.lower() if ignore_case else file_line
                            cmp_search = search_line.lower() if ignore_case else search_line
                            if partial:
                                if cmp_search not in cmp_file:
                                    matched_all = False
                                    break
                            else:
                                if cmp_file != cmp_search:
                                    matched_all = False
                                    break
                    if matched_all:
                        start_line_no = i + 1
                        if num_search == 1:
                            results.append((start_line_no, file_lines[i]))
                        else:
                            block_text = '\n'.join(file_lines[i:i + num_search])
                            results.append((start_line_no, block_text))
                if not results:
                    return f'在 {filepath} 中未找到匹配内容'
                output = [f'在 {filepath} 中找到 {len(results)} 处匹配：\n']
                for line_no, line_text in results:
                    if '\n' in line_text:
                        preview = line_text.split('\n')[0]
                        output.append(f'  行 {line_no}: {preview} ... (共 {num_search} 行)')
                    else:
                        output.append(f'  行 {line_no}: {line_text}')
                log_action('FIND', f'{filepath} -> {len(results)} 处')
                return '\n'.join(output)
            except Exception as e:
                return f'查找失败：{e}'
        else:
            # --- 模式二：文件名递归查找 (路径必须为目录) ---
            tokens = parse_args_with_quotes(arg)
            if len(tokens) < 2:
                return '错误：缺少查找内容。发送 @@help find 获取指令详细用法'
            # 提取 flags 和非 flags 参数
            flags = [t for t in tokens if t.startswith('-')]
            non_flags = [t for t in tokens if not t.startswith('-')]
            if len(non_flags) < 2:
                return '错误：缺少文件路径或查找内容。'
            filepath = safe_path(W, non_flags[0])
            filename_pattern = non_flags[-1]
            # 解析修饰参数
            use_regex = '-r' in flags
            partial = '-p' in flags
            ignore_case = '-i' in flags
            err = _check_permission('find', filepath)
            if err:
                return err
            if os.path.isfile(filepath):
                return f'错误：文件名查找模式下，目标必须是目录，不能是文件 - {filepath}'
            if not os.path.isdir(filepath):
                return f'错误：目录不存在 - {filepath}'
            try:
                re_flags = re.IGNORECASE if ignore_case else 0
                if use_regex:
                    try:
                        pattern = re.compile(filename_pattern, re_flags)
                    except re.error as e:
                        return f'错误：无效的正则表达式 - {filename_pattern} ({e})'
                results = []
                for root, dirs, files in os.walk(filepath):
                    _check_abort()  # [新增] 每个目录检查一次
                    for fname in files:
                        if use_regex:
                            m = pattern.search(fname) if partial else pattern.fullmatch(fname)
                            if m:
                                results.append(os.path.join(root, fname))
                        else:
                            cmp_fname = fname.lower() if ignore_case else fname
                            cmp_pattern = filename_pattern.lower() if ignore_case else filename_pattern
                            if partial:
                                if cmp_pattern in cmp_fname:
                                    results.append(os.path.join(root, fname))
                            else:
                                if cmp_fname == cmp_pattern:
                                    results.append(os.path.join(root, fname))
                if not results:
                    return f'在目录 {filepath} 中未找到匹配 "{filename_pattern}" 的文件。'
                output = [f'在目录 {filepath} 中找到 {len(results)} 个匹配 "{filename_pattern}" 的文件：\n']
                for fpath in results:
                    output.append(f'  {fpath}')
                log_action('FIND', f'{filepath} -> {len(results)} 个文件')
                return '\n'.join(output)
            except Exception as e:
                return f'搜索文件失败：{e}'
    elif cmd == 'replace':
        parts = arg.split('\x00')
        if not parts:
            return '错误：缺少参数。发送 @@help replace 获取指令详细用法'
        opts_str = parts[0].strip()
        tokens = parse_args_with_quotes(opts_str)
        if not tokens:
            return '错误：缺少文件路径。发送 @@help replace 获取指令详细用法'
        filepath = safe_path(W, tokens[0])
        flags = tokens[1:] if len(tokens) > 1 else []
        # 解析行号范围 -l
        line_range = None
        for idx_f, flag in enumerate(flags):
            if flag == '-l' and idx_f + 1 < len(flags):
                r_match = re.match(r'^(\d+)(?:-(\d+))?$', flags[idx_f + 1])
                if r_match:
                    start = int(r_match.group(1))
                    end = int(r_match.group(2)) if r_match.group(2) else start
                    line_range = (start, end)
                    break
        if line_range:
            if len(parts) < 2:
                return '错误：行号模式需要提供新文本。发送 @@help replace 获取指令详细用法'
            new_text = parts[1].replace('TICK3', '```')
            old_text = ''
        else:
            if len(parts) < 3:
                return '错误：缺少参数。发送 @@help replace 获取指令详细用法'
            old_text = parts[1].replace('TICK3', '```')
            new_text = parts[2].replace('TICK3', '```')
        ignore_case = '-i' in flags
        replace_all = '-a' in flags
        ignore_indent = '-s' in flags  # 忽略每行首尾空格和缩进
        normalize_ws = '-w' in flags  # 空白归一化
        # 解析模糊匹配参数 -f 或 -f-0.8
        fuzzy_threshold = None
        for flag in flags:
            if flag == '-f':
                fuzzy_threshold = 0.92
            elif flag.startswith('-f-'):
                try:
                    fuzzy_threshold = float(flag[3:])
                except ValueError:
                    return '错误：-f 参数格式不正确，应为 -f-0.92 形式'
        err = _check_permission('replace', filepath)
        if err:
            return err
        try:
            content, file_enc = smart_read(filepath)
            count = 0
            if line_range:
                file_lines = content.split('\n')
                start, end = line_range
                if start < 1 or end > len(file_lines):
                    return f'错误：行号范围 {start}-{end} 超出文件范围 (1-{len(file_lines)})'
                new_lines = new_text.split('\n')
                s_idx = start - 1
                file_lines[s_idx:end] = new_lines
                count = end - start + 1
                new_content = '\n'.join(file_lines)
            else:
                file_lines = content.split('\n')
                old_lines = old_text.split('\n')
                # 调用通用匹配方法
                _check_abort()  # [新增] 匹配前检查中断
                matches = _match_text_block(
                    file_lines, old_lines,
                    ignore_case=ignore_case,
                    ignore_indent=ignore_indent,
                    normalize_ws=normalize_ws,
                    fuzzy_threshold=fuzzy_threshold
                )
                if not matches:
                    # 诊断信息：找出最接近的块
                    best_pos = -1
                    best_avg = 0.0
                    for i in range(len(file_lines) - len(old_lines) + 1):
                        total = 0.0
                        for j in range(len(old_lines)):
                            f_proc = re.sub(r'\s+', ' ', file_lines[i + j].strip()).lower()
                            o_proc = re.sub(r'\s+', ' ', old_lines[j].strip()).lower()
                            total += difflib.SequenceMatcher(None, o_proc, f_proc).ratio()
                        avg = total / len(old_lines)
                        if avg > best_avg:
                            best_avg = avg
                            best_pos = i
                    diag = ['未找到匹配的文本块。']
                    if best_pos >= 0:
                        diag.append(f'最接近的匹配：第 {best_pos + 1} 行起，平均相似度: {best_avg:.2%}')
                        for j in range(len(old_lines)):
                            f_proc = re.sub(r'\s+', ' ', file_lines[best_pos + j].strip()).lower()
                            o_proc = re.sub(r'\s+', ' ', old_lines[j].strip()).lower()
                            if o_proc == f_proc:
                                diag.append(f'  ✓ {repr(o_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
                            else:
                                diag.append(f'  ✗ 旧: {repr(o_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
                                diag.append(f'    文: {repr(f_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
                    return '\n'.join(diag)
                # 非全量替换时，仅保留第一个匹配
                if not replace_all and len(matches) > 1:
                    matches = [matches[0]]
                new_block_lines = new_text.split('\n')
                # 从后往前替换，避免索引错乱
                for idx in reversed(matches):
                    applied_lines = list(new_block_lines)
                    # 如果开启了忽略缩进，替换时继承原文本块第一行的缩进
                    if ignore_indent:
                        indent_match = re.match(r'^(\s*)', file_lines[idx])
                        indent = indent_match.group(1) if indent_match else ''
                        applied_lines = [indent + l if l.strip() else l for l in applied_lines]
                    file_lines[idx:idx + len(old_lines)] = applied_lines
                    count += 1
                new_content = '\n'.join(file_lines)
            if count == 0:
                return '未找到要替换的文本。'
            smart_write(filepath, new_content, file_enc)
            log_action('REPLACE', f'{filepath} ({count} 处)')
            return f'已替换 {filepath} 中的 {count} 处文本。'
        except Exception as e:
            return f'替换失败：{e}'
    elif cmd == 'insert':
        if '\x00' not in arg:
            return '错误：缺少参数。发送 @@help insert 获取指令详细用法'
        sep = arg.split('\x00', 1)
        opts_str = sep[0].strip()
        insert_text = sep[1]
        tokens = parse_args_with_quotes(opts_str)
        if not tokens:
            return '错误：缺少文件路径。发送 @@help insert 获取指令详细用法'
        filepath = safe_path(W, tokens[0])
        opts = ' '.join(tokens[1:]) if len(tokens) > 1 else ''
        m = re.match(r'-(after|before)\s+["\']?(.+?)["\']?\s*$', opts)
        if not m:
            return '错误：选项格式不正确。发送 @@help insert 获取指令详细用法'
        pos_type = m.group(1)
        pos_val = m.group(2)
        err = _check_permission('insert', filepath)
        if err:
            return err
        try:
            content, file_enc = smart_read(filepath)
            lines = content.splitlines(True)
            insert_idx = -1
            if pos_val.isdigit():
                line_no = int(pos_val)
                if line_no < 1 or line_no > len(lines) + 1:
                    return f'错误：行号 {line_no} 超出文件范围 (1-{len(lines) + 1})'
                insert_idx = line_no if pos_type == 'after' else line_no - 1
            else:
                found_idx = -1
                for idx, line in enumerate(lines):
                    if pos_val in line:
                        found_idx = idx
                        break
                if found_idx == -1:
                    return f'未找到定位文本：{pos_val}'
                insert_idx = found_idx + 1 if pos_type == 'after' else found_idx
            insert_text = insert_text.replace('TICK3', '`')
            if not insert_text.endswith('\n'):
                insert_text += '\n'
            lines.insert(insert_idx, insert_text)
            new_content = ''.join(lines)
            smart_write(filepath, new_content, file_enc)
            log_action('INSERT', f'{filepath} 行 {insert_idx + 1}')
            return f'已在 {filepath} 的第 {insert_idx + 1} 行处插入内容。'
        except Exception as e:
            return f'插入失败：{e}'
    elif cmd == 'deleteline':
        if not arg.strip():
            return '错误：缺少参数。发送 @@help deleteline 获取指令详细用法'
        parts = parse_args_with_quotes(arg)
        filepath = safe_path(W, parts[0])
        err = _check_permission('deleteline', filepath)
        if err:
            return err
        if '-l' in parts:
            l_index = parts.index('-l')
            if l_index + 1 >= len(parts):
                return '错误：-l 选项后需要指定行号或范围。发送 @@help deleteline 获取指令详细用法'
            line_spec = parts[l_index + 1]
            if '-' in line_spec:
                start, end = line_spec.split('-', 1)
                try:
                    start = int(start)
                    end = int(end)
                except ValueError:
                    return '错误：行号范围格式不正确，应为 数字-数字'
            else:
                try:
                    start = int(line_spec)
                    end = start
                except ValueError:
                    return '错误：行号格式不正确，应为数字'
            try:
                content, file_enc = smart_read(filepath)
                lines = content.splitlines(True)
                if start < 1 or end > len(lines):
                    return f'错误：行号范围 {start}-{end} 超出文件范围 (1-{len(lines)})'
                del lines[start - 1:end]
                new_content = ''.join(lines)
                smart_write(filepath, new_content, file_enc)
                log_action('DELETELINE', f'{filepath} 行 {start}-{end}')
                return f'已删除 {filepath} 的第 {start} 到 {end} 行'
            except Exception as e:
                return f'删除行失败：{e}'
        else:
            flags = [part for part in parts if part.startswith('-')]
            ignore_case = '-i' in flags
            whole_word = '-w' in flags
            delete_all = '-a' in flags
            if '\x00' in arg:
                opts_str, delete_text = arg.split('\x00', 1)
            else:
                non_flag_parts = [part for part in parts if not part.startswith('-')]
                delete_text = ' '.join(non_flag_parts[1:]) if len(non_flag_parts) > 1 else ''
                opts_str = ' '.join(parts[:1] + [part for part in parts if part.startswith('-')])
            tokens = parse_args_with_quotes(opts_str)
            filepath = safe_path(W, tokens[0])
            flags = tokens[1:] if len(tokens) > 1 else []
            ignore_case = '-i' in flags
            whole_word = '-w' in flags
            delete_all = '-a' in flags
            if not delete_text:
                return '错误：缺少要删除的文本。发送 @@help deleteline 获取指令详细用法'
            try:
                content, file_enc = smart_read(filepath)
                flags_re = re.IGNORECASE if ignore_case else 0
                pattern = r'\b' + re.escape(delete_text) + r'\b' if whole_word else re.escape(delete_text)
                regex = re.compile(pattern, flags_re)
                matches = list(regex.finditer(content))
                if not matches:
                    return f'未找到要删除的文本：{delete_text[:50]}（前50字符）'
                new_content = content
                count = 0
                for match in reversed(matches):
                    if not delete_all and count >= 1:
                        break
                    new_content = new_content[:match.start()] + new_content[match.end():]
                    count += 1
                smart_write(filepath, new_content, file_enc)
                log_action('DELETELINE', f'{filepath} ({count} 处)')
                return f'已删除 {filepath} 中的 {count} 处文本'
            except Exception as e:
                return f'删除文本失败：{e}'
    elif cmd == 'grep':
        # [重构] 标准化 grep：正则匹配 + 标准选项集
        # 用法: grep [选项] "模式" <路径>
        #       grep [选项] -e "模式1" -e "模式2" <路径>
        # 选项: -i 忽略大小写 | -v 反向匹配 | -c 仅计数 | -l 仅文件名
        #       -w 全词匹配 | -r 递归目录 | -s 忽略行首缩进
        #       -e 多模式(可多次) | --include 文件名正则过滤 | --exclude 文件名正则排除
        tokens = parse_args_with_quotes(arg)
        if not tokens:
            return '错误：缺少参数。发送 @@help grep 获取指令详细用法'
        # ── 解析选项与参数 ──
        flag_set = set()  # 单字符标志集合（支持 -ivr 合并写法）
        patterns = []  # -e 显式指定的模式列表
        include_pattern = None  # --include 文件名过滤正则（字符串）
        exclude_pattern = None  # --exclude 文件名排除正则（字符串）
        non_opts = []  # 非选项参数（模式 / 路径）
        ti = 0
        while ti < len(tokens):
            t = tokens[ti]
            if t == '-e' and ti + 1 < len(tokens):
                patterns.append(tokens[ti + 1])  # -e 后紧跟一个模式
                ti += 2
            elif t == '--include' and ti + 1 < len(tokens):
                include_pattern = tokens[ti + 1]
                ti += 2
            elif t == '--exclude' and ti + 1 < len(tokens):
                exclude_pattern = tokens[ti + 1]
                ti += 2
            elif t.startswith('--'):
                return f'错误：未知选项 {t}。发送 @@help grep 获取指令详细用法'
            elif t.startswith('-') and len(t) > 1:
                # 合并短选项拆解：-ivr → {'i','v','r'}
                for ch in t[1:]:
                    flag_set.add(ch)
                ti += 1
            else:
                non_opts.append(t)
                ti += 1
        # ── 标志提取 ──
        ignore_case = 'i' in flag_set  # 忽略大小写
        invert_match = 'v' in flag_set  # 反向匹配（输出不匹配的行）
        count_only = 'c' in flag_set  # 仅输出匹配行数
        files_only = 'l' in flag_set  # 仅输出含匹配的文件名
        whole_word = 'w' in flag_set  # 全词匹配（自动包 \b）
        recursive = 'r' in flag_set  # 递归搜索目录
        strip_indent = 's' in flag_set  # 匹配前去除行首空白（保留原有功能）
        # ── 确定模式与路径 ──
        if patterns:
            # 有 -e：所有 non_opts 视为路径（本工具取第一个）
            if not non_opts:
                return '错误：缺少搜索路径。发送 @@help grep 获取指令详细用法'
            target_str = non_opts[0]
        else:
            # 无 -e：第一个 non_opt 是模式，第二个是路径
            if len(non_opts) < 2:
                return '错误：缺少模式或路径。发送 @@help grep 获取指令详细用法'
            patterns = [non_opts[0]]
            target_str = non_opts[1]
        if not patterns or all(not p for p in patterns):
            return '错误：搜索模式为空。'
        target = safe_path(W, target_str)
        err = _check_permission('grep', target)
        if err:
            return err
        # ── 编译内容搜索正则 ──
        re_flags = re.IGNORECASE if ignore_case else 0
        compiled = []
        for p in patterns:
            expr = rf'\b(?:{p})\b' if whole_word else p  # -w 自动包裹词边界
            try:
                compiled.append(re.compile(expr, re_flags))
            except re.error as e:
                return f'错误：无效的正则表达式 — {p} ({e})'
        # ── 编译文件名过滤正则（--include / --exclude）──
        include_re = None
        exclude_re = None
        if include_pattern:
            try:
                include_re = re.compile(include_pattern, re.IGNORECASE)
            except re.error as e:
                return f'错误：--include 无效的正则表达式 — {include_pattern} ({e})'
        if exclude_pattern:
            try:
                exclude_re = re.compile(exclude_pattern, re.IGNORECASE)
            except re.error as e:
                return f'错误：--exclude 无效的正则表达式 — {exclude_pattern} ({e})'
        def _file_allowed(fname):
            """根据 --include / --exclude 正则判断文件是否参与搜索"""
            if include_re and not include_re.search(fname):
                return False
            if exclude_re and exclude_re.search(fname):
                return False
            return True
        # ── 单文件搜索核心 ──
        def _grep_file(fpath):
            """搜索单个文件，返回 (匹配行数, 格式化结果行列表)"""
            try:
                content, _ = smart_read(fpath)
            except TaskAborted:
                raise  # 不吞中断信号
            except Exception:
                return 0, []  # 二进制/不可读文件静默跳过
            file_lines = content.splitlines()
            hit_count = 0
            out_lines = []
            for idx, line in enumerate(file_lines, 1):
                if idx % 500 == 0:
                    _check_abort()  # 每 500 行检查一次中断
                check = line.lstrip() if strip_indent else line
                matched = any(pat.search(check) for pat in compiled)
                if invert_match:
                    matched = not matched
                if matched:
                    hit_count += 1
                    # -c / -l 模式不需要逐行内容
                    if not count_only and not files_only:
                        out_lines.append(f'{idx}:{line.rstrip()}')
            return hit_count, out_lines
        # ── 执行搜索 ──
        try:
            if os.path.isfile(target):
                hit_count, out_lines = _grep_file(target)
                if count_only:
                    return f'{target}:{hit_count}'
                if files_only:
                    return target if hit_count > 0 else f'{target}: 无匹配'
                if out_lines:
                    return f'{target}:\n' + '\n'.join(out_lines)
                return f'{target}: 无匹配'
            elif os.path.isdir(target):
                # 目录必须显式 -r，避免误将"未递归"当作"无匹配"
                if not recursive:
                    return f'错误："{target_str}" 是目录而非文件，发送 @@help grep 获取指令详细用法'
                total_hits = 0
                all_out = []
                matched_files = []  # [(路径, 命中数), ...]
                for root, dirs, files in os.walk(target):
                    _check_abort()  # 每个目录检查一次中断
                    for fname in sorted(files):
                        if not _file_allowed(fname):
                            continue
                        fpath = os.path.join(root, fname)
                        mc, rl = _grep_file(fpath)
                        if mc > 0:
                            total_hits += mc
                            matched_files.append((fpath, mc))
                            if not count_only and not files_only:
                                for rl_line in rl:
                                    all_out.append(f'{fpath}:{rl_line}')
                if count_only:
                    if matched_files:
                        return '\n'.join(f'{fp}:{cnt}' for fp, cnt in matched_files)
                    return f'在目录 {target} 中无匹配。'
                if files_only:
                    if matched_files:
                        return '\n'.join(fp for fp, _ in matched_files)
                    return f'在目录 {target} 中无匹配。'
                if all_out:
                    return '\n'.join(all_out)
                return f'在目录 {target} 中无匹配。'
            else:
                return f'错误：路径不存在 — {target}'
        except TaskAborted:
            raise  # 中断信号透传给 worker_loop 处理
        except Exception as e:
            return f'搜索失败：{e}'
    elif cmd == 'head':
        parts = parse_args_with_quotes(arg)
        if not parts:
            return '错误：缺少文件路径。发送 @@help head 获取指令详细用法'
        filepath = safe_path(W, parts[0])
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        err = _check_permission('head', filepath)
        if err:
            return err
        try:
            content, _ = smart_read(filepath)
            lines = content.splitlines(True)
            head_lines = [l.rstrip() for l in lines[:n]]
            log_action('HEAD', filepath)
            return '\n'.join(head_lines) if head_lines else '（文件为空）'
        except Exception as e:
            return f'读取失败：{e}'
    elif cmd == 'tail':
        parts = parse_args_with_quotes(arg)
        if not parts:
            return '错误：缺少文件路径。发送 @@help tail 获取指令详细用法'
        filepath = safe_path(W, parts[0])
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        err = _check_permission('tail', filepath)
        if err:
            return err
        try:
            content, _ = smart_read(filepath)
            lines = content.splitlines(True)
            tail_lines = [l.rstrip() for l in lines[-n:]]
            log_action('TAIL', filepath)
            return '\n'.join(tail_lines) if tail_lines else '（文件为空）'
        except Exception as e:
            return f'读取失败：{e}'
    elif cmd == 'create':
        if not arg:
            return '错误：缺少文件路径。发送 @@help create 获取指令详细用法'
        if '\x00' in arg:
            sep = arg.split('\x00', 1)
            p_args = parse_args_with_quotes(sep[0].strip())
            filepath_str = p_args[0] if p_args else sep[0].strip().strip('"')
            filepath = safe_path(W, filepath_str)
            content = sep[1]
        else:
            p_args = parse_args_with_quotes(arg.strip())
            if not p_args:
                return '错误：缺少文件路径。发送 @@help create 获取指令详细用法'
            filepath_str = p_args[0]
            rest = ' '.join(p_args[1:]) if len(p_args) > 1 else ''
            filepath = safe_path(W, filepath_str)
            content = rest
        content = content.replace('TICK3', '```')
        err = _check_permission('create', filepath)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(content)
            log_action('CREATE', filepath)
            return f'已创建文件：{filepath}（{len(content)} 字符）'
        except Exception as e:
            return f'创建失败：{e}'
    elif cmd == 'read':
        if not arg.strip():
            return '错误：缺少文件路径。发送 @@help read 获取指令详细用法'
        parts = parse_args_with_quotes(arg.strip())
        if not parts:
            return '错误：缺少文件路径。发送 @@help read 获取指令详细用法'
        filepath = safe_path(W, parts[0])
        start_line = 0
        end_line = 0
        if len(parts) >= 2:
            try:
                range_str = parts[1]
                if '-' in range_str:
                    s, e = range_str.split('-', 1)
                    start_line = int(s) if s else 1
                    end_line = int(e) if e else -1
                else:
                    start_line = int(range_str)
                    end_line = -1
            except ValueError:
                return '错误：行号格式不正确。发送 @@help read 获取指令详细用法'
        err = _check_permission('read', filepath)
        if err:
            return err
        if start_line == 0:
            # 【修改】剪贴板模式：始终使用临时文件 + HTTP下载
            if clipboard_mode and os.path.isfile(filepath):
                try:
                    # 生成唯一ID
                    file_id = str(uuid.uuid4())
                    temp_path = os.path.join(get_temp_dir(), file_id)
                    # [修复] 确保目录存在 (防止被cleanup删掉后刷新报错)
                    os.makedirs(get_temp_dir(), exist_ok=True)
                    # 复制文件到临时目录 (保留原始二进制，不Base64)
                    shutil.copy2(filepath, temp_path)
                    filename = os.path.basename(filepath)
                    file_size = os.path.getsize(filepath)
                    # TODO: [PokerAgent] 后续可在此处增加分块下载逻辑支持进度条
                    # 返回新标记格式
                    return f'__CLIPBOARD_FILE__ID|||{file_id}|||{filename}|||{file_size}'
                except Exception as e:
                    return f'文件传输准备失败: {e}'
            # 非剪贴板模式或非文件：走原有逻辑
            try:
                content, _ = smart_read(filepath)
                lines = content.splitlines(True)
                if start_line > 0:
                    s_idx = max(0, start_line - 1)
                    e_idx = min(end_line, len(lines)) if end_line > 0 else len(lines)
                    selected = lines[s_idx:e_idx]
                    if not selected:
                        return f'指定范围内无内容（文件共 {len(lines)} 行）'
                    output = []
                    for i, line in enumerate(selected, start=s_idx + 1):
                        output.append(f"{i:>5}\t{line.rstrip()}")
                    result = '\n'.join(output)
                    log_action('READ', f'{filepath} 行 {start_line}-{end_line if end_line > 0 else "末尾"}')
                    return result
                else:
                    content_str = ''.join(lines)
                    log_action('READ', filepath)
                    return content_str if content_str else '（文件为空）'
            except FileNotFoundError:
                return f'错误：文件不存在：{filepath}'
            except Exception as e:
                return f'读取失败：{e}'
        else:
            try:
                content, _ = smart_read(filepath)
                lines = content.splitlines(True)
                s_idx = max(0, start_line - 1)
                e_idx = min(end_line, len(lines)) if end_line > 0 else len(lines)
                selected = lines[s_idx:e_idx]
                if not selected:
                    return f'指定范围内无内容（文件共 {len(lines)} 行）'
                output = []
                for i, line in enumerate(selected, start=s_idx + 1):
                    output.append(f"{i:>5}\t{line.rstrip()}")
                result = '\n'.join(output)
                log_action('READ', f'{filepath} 行 {start_line}-{end_line if end_line > 0 else "末尾"}')
                return result
            except FileNotFoundError:
                return f'错误：文件不存在：{filepath}'
            except Exception as e:
                return f'读取失败：{e}'
    elif cmd == 'append':
        if not arg:
            return '错误：缺少文件路径。发送 @@help append 获取指令详细用法'
        if '\x00' in arg:
            sep = arg.split('\x00', 1)
            p_args = parse_args_with_quotes(sep[0].strip())
            filepath_str = p_args[0] if p_args else sep[0].strip().strip('"')
            filepath = safe_path(W, filepath_str)
            content = sep[1]
        else:
            p_args = parse_args_with_quotes(arg.strip())
            if not p_args:
                return '错误：缺少文件路径。发送 @@help append 获取指令详细用法'
            filepath_str = p_args[0]
            rest = ' '.join(p_args[1:]) if len(p_args) > 1 else ''
            filepath = safe_path(W, filepath_str)
            content = rest
        content = content.replace('TICK3', '```')
        err = _check_permission('append', filepath)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
            file_enc = 'utf-8' if not os.path.exists(filepath) else smart_read(filepath)[1]
            with open(filepath, 'a', encoding=file_enc) as f:
                f.write('\n' + content)
            log_action('APPEND', filepath)
            return f'已追加到文件：{filepath}'
        except Exception as e:
            return f'追加失败：{e}'
    elif cmd == 'delete':
        # [修改] 严格格式校验：只允许 delete "路径" 或 delete 路径
        arg = arg.strip()
        m = re.match(r'^delete\s+["\']?(.+?)["\']?\s*$', line, re.IGNORECASE)
        if not m:
            return '错误：delete 指令格式不正确。发送 @@help delete 获取指令详细用法'
        target_path_str = m.group(1).strip()
        filepath = safe_path(W, target_path_str)
        # 拒绝删除工作目录本身
        if os.path.normpath(filepath).lower() == os.path.normpath(W).lower():
            return '错误：拒绝删除工作目录本身。'
        # 拦截对回收站的删除
        if _is_trash_path(filepath):
            return '错误：拒绝操作专属回收站。'
        err = _check_permission('delete', filepath)
        if err:
            return err
        if not os.path.exists(filepath):
            return f'错误：目标不存在：{filepath}'
        try:
            os.makedirs(TRASH_DIR, exist_ok=True)
            # 计算回收站内的对应路径
            trash_path = _get_trash_path(filepath)
            # 防覆盖：如果回收站已有同名残留（删了没恢复又删），拒绝操作
            if os.path.exists(trash_path):
                return f'错误：回收站已存在该路径的历史残留 [{trash_path}]，请先手动清理回收站或恢复历史文件。'
            # 创建回收站内的目录层级
            os.makedirs(os.path.dirname(trash_path), exist_ok=True)
            # 移动文件/目录
            shutil.move(filepath, trash_path)
            # 在日志中记录，用于 "restore 最近" 查询
            with open(os.path.join(TRASH_DIR, 'trash.log'), 'a', encoding='utf-8') as f:
                f.write(f'{time.time()}|{filepath}\n')
            log_action('DELETE', f'{filepath} -> 回收站')
            return f'已将 {filepath} 移入专属回收站。如需恢复，请使用：restore "{target_path_str}" 或 restore 最近'
        except Exception as e:
            return f'删除失败：{e}'
    elif cmd == 'restore':
        # [新增] 从专属回收站恢复文件/目录
        arg = arg.strip()
        if not os.path.exists(TRASH_DIR):
            return '错误：回收站为空或不存在。'
        try:
            trash_path_to_restore = None
            # 模式1：恢复最近删除
            if arg == '最近' or arg == '"最近"':
                log_file = os.path.join(TRASH_DIR, 'trash.log')
                if not os.path.exists(log_file):
                    return '错误：回收站没有任何删除记录。'
                with open(log_file, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                if not lines:
                    return '错误：回收站没有任何删除记录。'
                # 取最后一行的时间戳和路径
                last_line = lines[-1].strip()
                ts_str, orig_path = last_line.split('|', 1)
                trash_path_to_restore = _get_trash_path(orig_path)
            # 模式2：按原路径恢复
            else:
                m_name = re.match(r'^["\']?(.+?)["\']?\s*$', arg)
                if not m_name:
                    return '错误：restore 指令格式不正确。发送 @@help restore 获取指令详细用法'
                # 兼容 gitignore 风格的目录斜杠，去掉末尾斜杠
                target_name = m_name.group(1).strip().rstrip('\\/').strip('"\'')
                # 还原为绝对路径用于计算层级
                orig_path = safe_path(W, target_name)
                trash_path_to_restore = _get_trash_path(orig_path)
            if not trash_path_to_restore or not os.path.exists(trash_path_to_restore):
                return '错误：在回收站中未找到对应的记录。'
            # 反推原始绝对路径
            original_path = _get_original_path(trash_path_to_restore)
            if not original_path:
                return '错误：无法解析原始路径。'
            # 防覆盖：如果原路径已有同名文件，拒绝恢复
            if os.path.exists(original_path):
                return f'错误：原路径已存在文件/目录，为防止覆盖，恢复中止：{original_path}'
            # 确保原路径的父目录存在
            os.makedirs(os.path.dirname(original_path), exist_ok=True)
            # 执行恢复
            shutil.move(trash_path_to_restore, original_path)
            # 清理回收站中可能残留的空目录
            for root, dirs, files in os.walk(TRASH_DIR, topdown=False):
                for dir_name in dirs:
                    dir_path = os.path.join(root, dir_name)
                    if not os.listdir(dir_path):
                        os.rmdir(dir_path)
            log_action('RESTORE', f'-> {original_path}')
            return f'已恢复：{original_path}'
        except Exception as e:
            return f'恢复失败：{e}'
    elif cmd == 'copy':
        if not arg:
            return '错误：缺少参数。发送 @@help copy 获取指令详细用法'
        sep = parse_args_with_quotes(arg)
        if len(sep) < 2:
            return '错误：需要源路径和目标路径两个参数。'
        src = safe_path(W, sep[0])
        dst = safe_path(W, sep[1])
        err = _check_permission('copy', src, dst)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
            shutil.copy2(src, dst)
            log_action('COPY', f'{src} -> {dst}')
            return f'已复制：{src} -> {dst}'
        except Exception as e:
            return f'复制失败：{e}'
    elif cmd == 'move':
        if not arg:
            return '错误：缺少参数。发送 @@help move 获取指令详细用法'
        sep = parse_args_with_quotes(arg)
        if len(sep) < 2:
            return '错误：需要源路径和目标路径两个参数。'
        src = safe_path(W, sep[0])
        dst = safe_path(W, sep[1])
        err = _check_permission('move', src, dst)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(dst) or '.', exist_ok=True)
            shutil.move(src, dst)
            log_action('MOVE', f'{src} -> {dst}')
            return f'已移动：{src} -> {dst}'
        except Exception as e:
            return f'移动失败：{e}'
    elif cmd == 'list':
        parts = parse_args_with_quotes(arg.strip())
        dirpath = safe_path(W, parts[0] if parts else W)
        err = _check_permission('list', dirpath)
        if err:
            return err
        try:
            entries = os.listdir(dirpath)
            if not entries:
                return f'{dirpath} 下为空目录。'
            lines = [f'目录：{dirpath}\n']
            for name in sorted(entries):
                full = os.path.join(dirpath, name)
                if os.path.isdir(full):
                    lines.append(f'  [DIR] {name}')
                else:
                    size = os.path.getsize(full)
                    if size < 1024:
                        lines.append(f'  [FILE] {name} ({size} B)')
                    elif size < 1024 * 1024:
                        lines.append(f'  [FILE] {name} ({size / 1024:.1f} KB)')
                    else:
                        lines.append(f'  [FILE] {name} ({size / 1024 / 1024:.1f} MB)')
            log_action('LIST', dirpath)
            return '\n'.join(lines)
        except FileNotFoundError:
            return f'错误：目录不存在：{dirpath}'
        except Exception as e:
            return f'列出目录失败：{e}'
    elif cmd == 'mkdir':
        if not arg.strip():
            return '错误：缺少目录路径。发送 @@help mkdir 获取指令详细用法'
        parts = parse_args_with_quotes(arg.strip())
        if not parts:
            return '错误：缺少目录路径。发送 @@help mkdir 获取指令详细用法'
        dirpath = safe_path(W, parts[0])
        err = _check_permission('mkdir', dirpath)
        if err:
            return err
        try:
            os.makedirs(dirpath, exist_ok=True)
            log_action('MKDIR', dirpath)
            return f'已创建目录：{dirpath}'
        except Exception as e:
            return f'创建目录失败：{e}'
    # ========== 系统命令 (流式版) ==========
    elif cmd == 'exec':
        if not exec_enabled:
            return '错误：exec 指令已被管理员禁用。'
        # [新增] 代码块格式支持：exec 后跟【CodeSTART】/```代码块时，代码块内容即要执行的命令
        # 内联文本与代码块同时存在时以代码块为准（内联丢弃）；不做 TICK3 替换，保证命令逐字保真
        if '\x00' in arg:
            arg = arg.split('\x00', 1)[1]
        if not arg.strip():
            return '错误：缺少命令。发送 @@help exec 获取指令详细用法'
        # [新增] 危险命令拦截与弹窗确认
        # [修改] 补漏：新增 ri（Remove-Item 别名）、shred、remove-item 及 PowerShell 磁盘破坏性命令
        # （clear-disk / initialize-disk / remove-partition；format 的词边界已天然覆盖 Format-Volume）
        # 注：ri 可能对含 "ri" 独立词的路径误报，但误报仅多一次确认弹窗，成本可接受
        dangerous_patterns = re.compile(r'\b(del|rd|rm|rmdir|ri|shred|format|erase|diskpart|mkfs|remove-item|clear-disk|initialize-disk|remove-partition)\b', re.IGNORECASE)
        if dangerous_patterns.search(arg):
            if permission_mgr._callback:
                # 触发 GUI 弹窗或 CLI 询问
                approved = permission_mgr._callback('高危命令拦截', arg.strip())
                if not approved:
                    return f'操作被拒绝：执行高危系统命令需用户确认。命令：{arg.strip()}'
            else:
                approved = _default_permission_callback('高危命令拦截', arg.strip())
                if not approved:
                    return f'操作被拒绝：执行高危系统命令需用户确认。命令：{arg.strip()}'
        log_action('EXEC', arg.strip())
        try:
            # [修改] 根据 shell_type 配置选择 PowerShell 或 cmd
            if shell_type == 'powershell' and _POWERSHELL_EXE:
                # PowerShell：列表传参，不走 shell=True，避免二次解析
                process = subprocess.Popen(
                    [_POWERSHELL_EXE, '-NoProfile', '-NonInteractive', '-Command', arg.strip()],
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=W
                )
            else:
                # cmd 回退
                process = subprocess.Popen(
                    f'cmd /c {arg.strip()}',
                    shell=True, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=W
                )
            # [新增] 注册当前子进程，供 GUI 侧终止
            with _current_process_lock:
                _current_process = process
            output_lines = []
            start_time = time.time()
            # [重构] 读取线程 + Queue：readline 阻塞不再卡死中断响应
            _read_q = queue.Queue()
            def _reader():
                """daemon 线程：专门 readline，读到就往 queue 塞"""
                try:
                    while True:
                        line_bytes = process.stdout.readline()
                        if not line_bytes:
                            break
                        _read_q.put(line_bytes)
                except (OSError, ValueError):
                    pass  # 管道被外部关闭时静默退出
                finally:
                    _read_q.put(None)  # EOF 哨兵：通知主循环"没有更多数据了"
            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            # [新增] drain 阶段连续空轮计数：daemon 线程被强杀时 finally 不保证执行，
            # 哨兵可能丢失，连续空轮超过阈值则强制认为管道已死，避免无限空转
            _drain_empty_hits = 0
            timed_out = False
            draining = False  # [新增] 进程退出后的管道排空阶段
            while True:
                try:
                    # drain 阶段用较长超时等最后一批数据；正常阶段 50ms 检查中断
                    item = _read_q.get(timeout=0.3 if draining else 0.05)
                except queue.Empty:
                    if draining:
                        _drain_empty_hits += 1
                        if _drain_empty_hits > 10:  # 10 × 0.3s ≈ 3s 无新增数据
                            break
                        continue
                    _check_abort()
                    if time.time() - start_time > 3600:
                        timed_out = True
                        break
                    # [修复] 队列空但主进程已退出 → 切 drain 模式，等读取线程把缓冲区剩余数据吐完
                    if process.poll() is not None:
                        draining = True
                    # [修复] 关键：item 在本分支必然未绑定，必须回循环头重新 get，
                    # 禁止向下引用（原 except 内残留的 if item is None 已删除）
                    continue
                # ↓ 以下为成功 get 到 item 的公共路径
                if item is None:  # EOF 哨兵：管道彻底关闭
                    break
                _drain_empty_hits = 0  # 有数据则重置空轮计数
                line_out = smart_decode(item).rstrip()
                output_lines.append(line_out)
                emit_task_event({'id': task_id, 'type': 'log', 'data': line_out})
                _check_abort()
            if timed_out:
                _kill_process_tree(process)  # [修改] 用统一函数杀进程树
                with _current_process_lock:
                    _current_process = None
                return '错误：命令执行超时（3600秒限制），进程树已强杀。'
            process.wait()
            # 清理子进程引用
            with _current_process_lock:
                _current_process = None
            output = '\n'.join(output_lines).strip()
            if not output:
                output = '（命令已执行，无输出）'
            return output
        except TaskAborted:
            raise  # 中断信号透传给 worker_loop
        except Exception as e:
            return f'执行失败：{e}'
    elif cmd == 'run':
        if not arg.strip():
            return '错误：缺少脚本路径。发送 @@help run 获取指令详细用法'
        parts = parse_args_with_quotes(arg.strip())
        if not parts:
            return '错误：缺少脚本路径。发送 @@help run 获取指令详细用法'
        script = safe_path(W, parts[0])
        err = _check_permission('run', script)
        if err:
            return err
        if not os.path.exists(script):
            return f'错误：脚本不存在：{script}'
        log_action('RUN', script)
        try:
            process = subprocess.Popen(
                ['python', script],
                stdout=subprocess.PIPE, stderr=subprocess.STDOUT, cwd=W
            )
            # [新增] 注册当前子进程，供 GUI 侧终止
            with _current_process_lock:
                _current_process = process
            output_lines = []
            start_time = time.time()
            # [重构] 读取线程 + Queue（与 exec 相同模式）
            _read_q = queue.Queue()
            def _reader():
                try:
                    while True:
                        line_bytes = process.stdout.readline()
                        if not line_bytes:
                            break
                        _read_q.put(line_bytes)
                except (OSError, ValueError):
                    pass
                finally:
                    _read_q.put(None)
            reader_thread = threading.Thread(target=_reader, daemon=True)
            reader_thread.start()
            # [新增] drain 阶段连续空轮计数：daemon 线程被强杀时 finally 不保证执行，
            # 哨兵可能丢失，连续空轮超过阈值则强制认为管道已死，避免无限空转
            _drain_empty_hits = 0
            timed_out = False
            draining = False  # [新增] 进程退出后的管道排空阶段
            while True:
                try:
                    item = _read_q.get(timeout=0.3 if draining else 0.05)
                except queue.Empty:
                    if draining:
                        _drain_empty_hits += 1
                        if _drain_empty_hits > 10:
                            break
                        continue
                    _check_abort()
                    if time.time() - start_time > 60:
                        timed_out = True
                        break
                    # [修复] 队列空但主进程已退出 → 切 drain 模式
                    if process.poll() is not None:
                        draining = True
                    # [修复] item 在本分支必然未绑定，必须回循环头重新 get
                    continue
                # ↓ 以下为成功 get 到 item 的公共路径
                if item is None:
                    break
                _drain_empty_hits = 0
                line_out = smart_decode(item).rstrip()
                output_lines.append(line_out)
                emit_task_event({'id': task_id, 'type': 'log', 'data': line_out})
                _check_abort()
            if timed_out:
                _kill_process_tree(process)
                with _current_process_lock:
                    _current_process = None
                # [修改] 文案修正：超时路径已调用 _kill_process_tree 强杀进程树，
                # 原文案"命令可能仍在运行中"与实际行为不符
                return '命令执行超时（限制:60秒），进程树已被强制终止。'
            process.wait()
            with _current_process_lock:
                _current_process = None
            output = '\n'.join(output_lines).strip()
            if not output:
                output = '（脚本已执行，无输出）'
            return output
        except TaskAborted:
            raise  # 中断信号透传给 worker_loop
        except Exception as e:
            return f'运行失败：{e}'
    elif cmd == 'get':
        if not arg.strip():
            return '错误：缺少 URL。发送 @@help get 获取指令详细用法'
        url = arg.strip()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Agent/1.0 (PokerAgent)'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bytes = resp.read()
                content_type = resp.headers.get('Content-Type', '')
            charset = 'utf-8'
            m = re.search(r'charset=([a-zA-Z0-9\-]+)', content_type, re.I)
            if m:
                charset = m.group(1)
            try:
                body = raw_bytes.decode(charset)
            except (UnicodeDecodeError, LookupError):
                try:
                    body = raw_bytes.decode('utf-8')
                except UnicodeDecodeError:
                    body = raw_bytes.decode('gbk', errors='replace')
            log_action('GET', url)
            return body
        except urllib.error.HTTPError as e:
            return f'HTTP 错误：{e.code} {e.reason}'
        except Exception as e:
            return f'请求失败：{e}'
    elif cmd == 'download':
        if not arg:
            return '错误：缺少参数。发送 @@help download 获取指令详细用法'
        sep = parse_args_with_quotes(arg)
        if len(sep) < 2:
            return '错误：需要 URL 和保存路径两个参数。'
        url, save = sep[0], safe_path(W, sep[1])
        err = _check_permission('download', save)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(save) or '.', exist_ok=True)
            urllib.request.urlretrieve(url, save)
            size = os.path.getsize(save)
            log_action('DOWNLOAD', f'{url} -> {save}')
            return f'已下载：{save}（{size} 字节）'
        except Exception as e:
            return f'下载失败：{e}'
    else:
        return f'未知指令：{cmd}\n输入 @@help fast 查看可用指令列表。'
_EXEC_SRC = inspect.getsource(execute_line_streaming)
KNOWN_CMDS = set(re.findall(r"cmd\s*==\s*'([^']+)'", _EXEC_SRC))
# [新增] memory 指令末尾修饰参数统一解析器（新增写入 / 覆盖写入共用）
def _parse_memory_params(raw):
    """
    解析 memory 指令末尾修饰参数，支持任意顺序组合：
      -pin       → 固定记忆
      temp:N     → 自定义初始温度（N 纯数字，如 temp:200）
      tag:a,b,c  → 标签
    从字符串末尾循环剥离，直到末尾无任何匹配参数。
    行为增强：tag: 可出现多次，多段标签按书写顺序合并（原实现仅取最后一个 tag:）。
    返回 (content, tags, pin, custom_temp)；custom_temp 未指定为 None。
    """
    tags = []
    pin = False
    custom_temp = None
    s = raw.strip()
    while True:
        # 1. -pin：必须是独立 token（带前导空格或独占全文），避免误剥 "xxx-pin" 类内容
        if s == '-pin':
            pin = True
            s = ''
            continue
        if s.endswith(' -pin'):
            pin = True
            s = s[:-5].rstrip()
            continue
        # 2. temp:N：末尾匹配，N 纯数字；\b 词边界防止 "atemp:100" 被误剥
        m = re.search(r'\btemp:\s*(\d+)\s*$', s)
        if m:
            custom_temp = int(m.group(1))
            s = s[:m.start()].rstrip()
            continue
        # 3. tag:xxx：rfind 语义（最后一个 tag: 到结尾均为标签串）
        tag_idx = s.rfind('tag:')
        if tag_idx != -1:
            tag_str = s[tag_idx + 4:].strip()
            new_tags = [t.strip() for t in tag_str.split(',') if t.strip()]
            # 前插合并：循环从末尾向前剥，前插保持标签书写顺序
            tags = new_tags + tags
            s = s[:tag_idx].rstrip()
            continue
        break
    return s, tags, pin, custom_temp
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 记忆引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
class MemoryEngine:
    """
    记忆系统核心：管理短期/长期记忆的读写、温度衰减、暴露窗口裁剪
    核心设计原则：
    - 不遗忘，只裁剪暴露。记忆永不自动删除，只控制哪些标签进入 LLM 上下文。
    - 温度采用百分比衰减（指数衰减曲线），永远 > 0，不需要 GC。
    - 升温采用"向初始温度回归"，极冷数据被读取时温度飙升。
    - 手动删除通过 memory del 指令实现。
    """
    def __init__(self):
        self._tick_count = 0  # 全局 Tick 计数器（对话轮次）
    @property
    def memory_dir(self):
        """记忆文件存储目录：工作目录下的 .agent"""
        return os.path.join(WORK_DIR, '.agent')
    @property
    def remember_file(self):
        """短期记忆文件路径"""
        return os.path.join(self.memory_dir, 'remember.md')
    @property
    def memory_file(self):
        """长期记忆文件路径"""
        return os.path.join(self.memory_dir, 'memory.md')
    @property
    def meta_file(self):
        """长期记忆元数据文件路径（温度、标签、Pin状态）"""
        return os.path.join(self.memory_dir, 'memory_meta.json')
    def _ensure_dir(self):
        """确保记忆目录存在"""
        os.makedirs(self.memory_dir, exist_ok=True)
    def _load_meta(self):
        """加载长期记忆元数据"""
        if os.path.exists(self.meta_file):
            try:
                with open(self.meta_file, 'r', encoding='utf-8') as f:
                    return json.load(f)
            except Exception:
                pass
        return {'next_id': 1, 'memory': {}}
    def _save_meta(self, meta):
        """保存长期记忆元数据"""
        self._ensure_dir()
        with open(self.meta_file, 'w', encoding='utf-8') as f:
            json.dump(meta, f, ensure_ascii=False, indent=2)
    # ── 短期记忆 ──
    def write_short(self, content):
        """覆盖写入短期记忆（空内容 = 清空）"""
        self._ensure_dir()
        with open(self.remember_file, 'w', encoding='utf-8') as f:
            f.write(content)
        log_action('REMEMBER', f'{len(content)} 字符')
    def read_short(self):
        """读取短期记忆全文"""
        if os.path.exists(self.remember_file):
            try:
                with open(self.remember_file, 'r', encoding='utf-8') as f:
                    return f.read().strip()
            except Exception:
                pass
        return ''
    # ── 长期记忆写入 ──
    def write_long(self, content, tags, pin=False, custom_temp=None):
        """追加写入长期记忆，分配纯数字ID，返回ID
        [修改] custom_temp: temp:N 指定的初始温度（None=用全局默认）"""
        self._ensure_dir()
        meta = self._load_meta()
        mem_id = meta['next_id']
        meta['next_id'] += 1
        # 温度：Pin 记忆为 ∞，普通记忆为自定义或全局初始温度
        initial_temp = custom_temp if custom_temp is not None else MEMORY_TEMP_INITIAL
        temp = '∞' if pin else initial_temp
        # 写入 memory.md（追加，带 ID 注释块）
        entry = f"<!-- ID:{mem_id:03d} -->\n{content}\ntag: {', '.join(tags)}\n<!-- END:{mem_id:03d} -->\n"
        with open(self.memory_file, 'a', encoding='utf-8') as f:
            f.write(entry)
        # 更新 meta
        meta['memory'][str(mem_id)] = {
            'temp': temp,
            'initial_temp': initial_temp,  # [新增] 记录初始温度，unpin 时恢复用
            'tags': tags,
            'pin': pin,
            'created_at': time.time(),
            'last_accessed': time.time()
        }
        self._save_meta(meta)
        log_action('MEMORY-WRITE', f'ID:{mem_id:03d} | tags:{tags} | pin:{pin} | temp:{temp}')
        return mem_id
    def memory_exists(self, mem_id):
        """检查指定ID的记忆是否存在（用于区分覆盖写入和新增写入）"""
        meta = self._load_meta()
        return str(mem_id) in meta['memory']
    # ── 长期记忆搜索 ──
    def search(self, keyword, window=None):
        """搜索长期记忆，返回命中全文 + 上下 N 条元数据，触发加热
        [修改] 支持多命中：标签命中优先全量返回；标签零命中再搜内容，内容命中全量返回
        [修改] 温度显示改为区间：floor(t)~floor(t)+1（Pin 为 ∞）"""
        if window is None:
            window = MEMORY_READ_WINDOW
        if not os.path.exists(self.memory_file):
            return '长期记忆为空。'
        try:
            content, _ = smart_read(self.memory_file)
        except Exception:
            return '长期记忆读取失败。'
        # 解析所有记忆块
        entries = self._parse_memory_file(content)
        if not entries:
            return '长期记忆为空。'
        keyword_lower = keyword.lower()
        # [修改] 多命中收集：先按标签匹配，收集全部命中
        matched = [i for i, entry in enumerate(entries)
                   if any(keyword_lower in tag.lower() for tag in entry['tags'])]
        match_by = '标签'
        if not matched:
            # 标签零命中 → 回退按内容匹配，同样收集全部命中
            matched = [i for i, entry in enumerate(entries)
                       if keyword_lower in entry['content'].lower()]
            match_by = '内容'
        if not matched:
            return f'未找到匹配 "{keyword}" 的记忆。'
        # 加热所有命中的记忆
        for i in matched:
            self._heat_memory(entries[i]['id'])
        # [修改] 温度区间格式化：0.001/0.1/0.3 → 0~1；1.11111 → 1~2；Pin/∞ → ∞
        def _temp_range(e):
            if e['pin'] or e['temp'] == '∞':
                return '∞'
            t = int(e['temp'])  # 温度恒为正，截断即 floor
            return f'{t}~{t + 1}'
        # 展示行集合 = 所有命中项 ±window 的并集（重叠区自动去重）
        show_rows = set()
        for i in matched:
            show_rows.update(range(max(0, i - window), min(len(entries), i + window + 1)))
        matched_set = set(matched)
        # 回执头部：命中方式 + 数量 + ID 列表
        ids_str = ', '.join(f'{entries[i]["id"]:03d}' for i in matched)
        lines = [f'按{match_by}搜索命中 {len(matched)} 条: {ids_str}', '']
        for i in sorted(show_rows):
            e = entries[i]
            tags_str = ','.join(e['tags']) if e['tags'] else '无'
            if i in matched_set:
                # 命中项：元数据行 + 全文
                lines.append(f"ID: {e['id']:03d} | 温度: {_temp_range(e)} | 标签: {tags_str}")
                lines.append(e['content'])
                lines.append('')
            else:
                # 上下文项：仅元数据
                lines.append(f"ID: {e['id']:03d} | 温度: {_temp_range(e)} | 标签: {tags_str}")
        return '\n'.join(lines).rstrip()
    def _parse_memory_file(self, content):
        """解析 memory.md，提取所有记忆块（ID、标签、内容）"""
        entries = []
        pattern = re.compile(r'<!-- ID:(\d+) -->\n(.*?)\n<!-- END:\1 -->', re.DOTALL)
        meta = self._load_meta()
        for m in pattern.finditer(content):
            mem_id = int(m.group(1))
            body = m.group(2)
            # 解析 body：内容 + tag 行
            body_lines = body.split('\n')
            tags = []
            content_lines = []
            for line in body_lines:
                if line.startswith('tag:'):
                    tag_str = line[4:].strip()
                    tags = [t.strip() for t in tag_str.split(',') if t.strip()]
                else:
                    content_lines.append(line)
            content_text = '\n'.join(content_lines).strip()
            # 从 meta 获取温度和 pin 状态
            mem_meta = meta['memory'].get(str(mem_id), {})
            pin = mem_meta.get('pin', False)
            temp = mem_meta.get('temp', MEMORY_TEMP_INITIAL)
            entries.append({
                'id': mem_id,
                'temp': temp,
                'pin': pin,
                'tags': tags,
                'content': content_text
            })
        return entries
    def _heat_memory(self, mem_id):
        """加热指定记忆：temp = temp + (initial - temp) × heat_ratio"""
        meta = self._load_meta()
        key = str(mem_id)
        if key not in meta['memory']:
            return
        mem = meta['memory'][key]
        if mem.get('pin'):
            return  # Pin 记忆不加热
        current_temp = mem.get('temp', MEMORY_TEMP_INITIAL)
        if current_temp == '∞':
            return
        # 向初始温度回归：极冷数据飙升，极热数据微调
        new_temp = current_temp + (MEMORY_TEMP_INITIAL - current_temp) * MEMORY_TEMP_HEAT_RATIO
        mem['temp'] = new_temp
        mem['last_accessed'] = time.time()
        self._save_meta(meta)
    # ── 温度衰减（每次 Tick 调用）──
    def tick(self):
        """每次对话轮次触发：所有非 Pin 记忆温度指数衰减"""
        self._tick_count += 1
        meta = self._load_meta()
        changed = False
        for key, mem in meta['memory'].items():
            if mem.get('pin'):
                continue  # Pin 记忆不衰减
            temp = mem.get('temp', MEMORY_TEMP_INITIAL)
            if temp == '∞':
                continue
            # 指数衰减：temp = temp × decay_ratio
            new_temp = temp * MEMORY_TEMP_DECAY_RATIO
            mem['temp'] = new_temp
            changed = True
        if changed:
            self._save_meta(meta)
    # ── 按ID删除记忆 ──
    def delete_by_ids(self, ids):
        """按ID删除一条或多条记忆，返回删除数量"""
        meta = self._load_meta()
        deleted = 0
        for mem_id in ids:
            key = str(mem_id)
            if key in meta['memory']:
                del meta['memory'][key]
                self._remove_entry_from_file(mem_id)
                deleted += 1
        if deleted > 0:
            self._save_meta(meta)
            log_action('MEMORY-DEL', f'已删除 {deleted} 条记忆: {ids}')
        return deleted
    # ── 按ID固定记忆 ──
    def pin_by_ids(self, ids):
        """按ID固定一条或多条记忆（温度锁定为∞），返回固定数量"""
        meta = self._load_meta()
        pinned = 0
        for mem_id in ids:
            key = str(mem_id)
            if key in meta['memory']:
                meta['memory'][key]['pin'] = True
                meta['memory'][key]['temp'] = '∞'
                pinned += 1
        if pinned > 0:
            self._save_meta(meta)
            log_action('MEMORY-PIN', f'已固定 {pinned} 条记忆: {ids}')
        return pinned
    # ── 按ID取消固定 ──
    def unpin_by_ids(self, ids):
        """按ID取消固定（回到初始温度继续衰减），返回取消数量"""
        meta = self._load_meta()
        unpin_count = 0
        for mem_id in ids:
            key = str(mem_id)
            if key in meta['memory'] and meta['memory'][key].get('pin'):
                meta['memory'][key]['pin'] = False
                # [修改] 恢复记忆自身的 initial_temp（自定义 temp: 的记忆 unpin 后不丢失），旧数据无记录回退全局默认
                meta['memory'][key]['temp'] = meta['memory'][key].get('initial_temp', MEMORY_TEMP_INITIAL)
                unpin_count += 1
        if unpin_count > 0:
            self._save_meta(meta)
            log_action('MEMORY-UNPIN', f'已取消固定 {unpin_count} 条记忆: {ids}')
        return unpin_count
    # ── 按ID覆盖写入 ──
    def overwrite_by_id(self, mem_id, content, tags, pin=False, custom_temp=None):
        """按ID覆盖写入已有记忆的内容和标签，返回是否成功
        [修改] custom_temp: 指定时重置温度并更新 initial_temp
        [修复] 原有 bug：覆盖写入取消 pin 时温度残留 '∞'（永不衰减的僵尸态）"""
        meta = self._load_meta()
        key = str(mem_id)
        if key not in meta['memory']:
            return False
        # 更新 meta
        meta['memory'][key]['tags'] = tags
        meta['memory'][key]['pin'] = pin
        if custom_temp is not None:
            # 指定了初始温度：记录之；固定态保持 ∞，非固定态温度直接重置
            meta['memory'][key]['initial_temp'] = custom_temp
            meta['memory'][key]['temp'] = '∞' if pin else custom_temp
        elif pin:
            meta['memory'][key]['temp'] = '∞'
        else:
            # [修复] 取消 pin 且未指定 temp: → 恢复到该记忆的初始温度（原实现残留 '∞'）
            if meta['memory'][key].get('temp') == '∞':
                meta['memory'][key]['temp'] = meta['memory'][key].get('initial_temp', MEMORY_TEMP_INITIAL)
        self._save_meta(meta)
        # 更新 memory.md 中对应块的内容
        self._update_entry_content(mem_id, content, tags)
        log_action('MEMORY-OVERWRITE', f'ID:{mem_id:03d} | tags:{tags} | pin:{pin} | temp_arg:{custom_temp}')
        return True
    # ── 辅助方法 ──
    def _parse_ids(self, id_str):
        """解析ID字符串，支持空格/逗号分隔的多个ID，返回int列表"""
        if not id_str:
            return []
        tokens = re.split(r'[\s,]+', id_str.strip())
        ids = []
        for t in tokens:
            t = t.strip()
            if t.isdigit():
                ids.append(int(t))
        return ids
    def _remove_entry_from_file(self, mem_id):
        """从 memory.md 中删除指定 ID 的记忆块"""
        if not os.path.exists(self.memory_file):
            return
        try:
            content, _ = smart_read(self.memory_file)
            pattern = re.compile(
                rf'<!-- ID:{mem_id:03d} -->\n.*?\n<!-- END:{mem_id:03d} -->\n?',
                re.DOTALL
            )
            new_content = pattern.sub('', content)
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception:
            pass
    def _update_entry_content(self, mem_id, content, tags):
        """替换 memory.md 中指定ID的记忆块内容"""
        if not os.path.exists(self.memory_file):
            return
        try:
            file_content, _ = smart_read(self.memory_file)
            pattern = re.compile(
                rf'<!-- ID:{mem_id:03d} -->\n.*?\n<!-- END:{mem_id:03d} -->',
                re.DOTALL
            )
            new_block = f"<!-- ID:{mem_id:03d} -->\n{content}\ntag: {', '.join(tags)}\n<!-- END:{mem_id:03d} -->"
            new_content = pattern.sub(new_block, file_content)
            with open(self.memory_file, 'w', encoding='utf-8') as f:
                f.write(new_content)
        except Exception:
            pass
    # ── 获取暴露窗口标签（供前端注入）──
    def get_expose_tags(self):
        """[修改] 返回 (已固定标签列表, 暴露窗口标签列表)。
        Pin 记忆的标签全量收集、单独返回，不挤占正常记忆的温度 Top-N 暴露窗口"""
        meta = self._load_meta()
        pinned_tags = set()
        scored = []
        for key, mem in meta['memory'].items():
            temp = mem.get('temp', MEMORY_TEMP_INITIAL)
            tags = mem.get('tags', [])
            if mem.get('pin') or temp == '∞':
                # 已固定：标签进固定区，不参与温度排序
                pinned_tags.update(tags)
            else:
                scored.append((temp, tags))
        # 正常记忆按温度降序排序，取前 N 条
        scored.sort(key=lambda x: x[0], reverse=True)
        top_n = scored[:MEMORY_EXPOSE_WINDOW]
        normal_tags = set()
        for _, tag_list in top_n:
            normal_tags.update(tag_list)
        return sorted(pinned_tags), sorted(normal_tags)
    # ── 获取注入内容（供 /agent-memory-inject 接口）──
    def get_inject_content(self):
        """返回短期记忆全文 + 长期记忆标签云，供前端注入到输入框
        [修改] 已固定标签单列一行（[已固定:x,x]），与正常暴露窗口标签分区显示"""
        parts = []
        # 短期记忆
        short = self.read_short()
        if short:
            parts.append(f"[短期记忆]\n{short}")
        # 长期记忆标签云（固定区 + 暴露窗口区）
        pinned_tags, normal_tags = self.get_expose_tags()
        if pinned_tags or normal_tags:
            tag_lines = ['[长期记忆标签]']
            if pinned_tags:
                tag_lines.append(f"[已固定:{','.join(pinned_tags)}]")
            if normal_tags:
                tag_lines.append(', '.join(normal_tags))
            parts.append('\n'.join(tag_lines))
        return '\n\n'.join(parts) if parts else ''
# 全局记忆引擎实例
memory_engine = MemoryEngine()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动后台 Worker 线程（移到顶层，确保任何启动方式都能跑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动时加载持久化配置（必须在 permission_mgr 创建之后、worker 启动之前）
load_config()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [新增] 全量日志捕获 + 事件推送到 GUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 全局队列：GUI 注入后启用推送
_gui_log_queue = None
# 文件写入锁：防止多线程并发写坏文件
_log_file_lock = threading.Lock()
def set_gui_log_queue(q):
    """供 GUI 注入日志队列，调用后立即启用事件推送"""
    global _gui_log_queue
    _gui_log_queue = q
class _LogWriter:
    """
    重定向 stdout/stderr 的核心类：
    1. 写入原始流（控制台/IDE终端可见）
    2. 追加写入 agent_log.txt（持久化，二进制模式 + 字节偏移追踪）
    3. 推送到 GUI 队列（事件驱动，消息附带字节偏移区间）
    """
    def __init__(self, original_stream, stream_name):
        self._orig = original_stream  # 原始 sys.stdout 或 sys.stderr
        self._name = stream_name  # 'out' 或 'err'，用于区分来源
    def write(self, s):
        if not s:
            return
        # 1. 原始输出（确保控制台/终端仍能看到日志）
        self._orig.write(s)
        self._orig.flush()  # 立即刷新，防止卡顿
        # 2. 文件持久化（线程安全追加写入）
        # [修改] 改用二进制模式：1) 字节偏移精确可追踪（供 GUI 窗口化回读定位）
        #                          2) 消除 Windows 文本模式 \n→\r\n 隐式翻译
        # [行为变更] 日志文件新内容行尾为 LF（历史 CRLF 内容读取方均兼容）
        start_pos = end_pos = None
        with _log_file_lock:
            try:
                data = s.encode('utf-8')
                with open(LOG_FILE, 'ab') as f:
                    f.seek(0, os.SEEK_END)  # 显式定位末尾（C 标准对 'a' 流初始位置未定义，勿依赖）
                    start_pos = f.tell()
                    f.write(data)
                    end_pos = f.tell()
            except Exception:
                start_pos = end_pos = None  # 写入失败静默处理，不能让日志系统搞挂主流程
        # 3. 推送到 GUI（事件驱动核心）
        # [修改] 消息附带本次写入的字节偏移区间 (stream_name, text, start, end)
        # GUI 据此建立 内存行 ↔ 文件字节区间 的精确映射，支撑滑动窗口回读
        if _gui_log_queue:
            try:
                # 使用 put_nowait 避免阻塞 worker 线程
                # GUI 侧是异步消费，不会卡住这里
                _gui_log_queue.put_nowait((self._name, s, start_pos, end_pos))
            except Exception:
                pass  # 队列满或异常时静默丢弃，保证服务稳定
    def flush(self):
        self._orig.flush()
# ── 挂载钩子 ──
# 注意：必须在 load_config() 之后执行，确保 LOG_FILE 路径已确定
sys.stdout = _LogWriter(sys.stdout, 'out')
sys.stderr = _LogWriter(sys.stderr, 'err')
worker_thread = threading.Thread(target=worker_loop, daemon=True)
worker_thread.start()
@app.route('/agent-stream')
def agent_stream():
    """SSE 接口：前端建立长连接监听任务进度"""
    q = queue.Queue()
    # 持锁注册客户端 + 回放历史状态，确保回放期间不会有新事件插入造成丢失或重复
    with _task_registry_lock:
        with _sse_lock:
            sse_clients.append(q)
        # 回放所有任务的当前状态（晚订阅补偿）
        for tid, entry in _task_registry.items():
            evt = {'id': tid, 'type': 'status', 'status': entry['status']}
            if entry['status'] == 'done' and entry['result']:
                evt['result'] = entry['result']
            q.put(f"data: {json.dumps(evt, ensure_ascii=False)}\n\n")
            # 只对未完成任务回放日志（done 的任务结果已含全部信息）
            if entry['status'] != 'done':
                for log_line in entry['logs']:
                    log_evt = {'id': tid, 'type': 'log', 'data': log_line}
                    q.put(f"data: {json.dumps(log_evt, ensure_ascii=False)}\n\n")
    def generate():
        try:
            while True:
                try:
                    msg = q.get(timeout=15)
                except queue.Empty:
                    yield ": heartbeat\n\n"
                    continue
                if msg is None:
                    break
                yield msg
        except GeneratorExit:
            pass
        finally:
            with _sse_lock:
                if q in sse_clients:
                    sse_clients.remove(q)
    return Response(generate(), mimetype='text/event-stream')
@app.route('/agent-exec', methods=['POST', 'GET'])
def agent_exec():
    if request.method == 'GET':
        return jsonify({'status': 'running', 'work_dir': WORK_DIR, 'clipboard_mode': clipboard_mode})
    try:
        data = request.get_json(force=True)
        command_text = data.get('command', '').strip()
    except Exception:
        return '无法解析请求体', 400
    if not command_text:
        return '空的指令', 400
    # 清理上一轮已完成的任务（回执已通过 SSE 送达，避免注册表无限膨胀）
    with _task_registry_lock:
        stale = [tid for tid, e in _task_registry.items() if e['status'] == 'done']
        for tid in stale:
            del _task_registry[tid]
    command_text = command_text.replace('\r\n', '\n').replace('\r', '\n')
    # [修改] 温度衰减信号已移至前端 /agent-memory-tick（一次对话衰减一次，原为每条指令一次）
    log_action('RECEIVED', command_text)
    lines = command_text.split('\n')
    i = 0
    task_ids = []
    # [新增] 提取代码块的独立函数，兼容 【CodeSTART】 和 ```
    def extract_blocks(start_idx):
        blocks = []
        peek = start_idx
        while peek < len(lines):
            stripped = lines[peek].strip()
            # 匹配 【CodeSTART】...【/CodeEND】
            if '【codestart】' in stripped.lower():
                peek += 1
                block = []
                while peek < len(lines):
                    bln = lines[peek]
                    if '【/codeend】' in bln.lower():
                        idx = bln.lower().find('【/codeend】')
                        if idx != -1:
                            block.append(bln[:idx])
                        peek += 1
                        break
                    block.append(bln)
                    peek += 1
                blocks.append('\n'.join(block).strip('\n'))
            # 兼容 ``` 代码块
            elif stripped.startswith('```'):
                peek += 1
                block = []
                while peek < len(lines):
                    bln = lines[peek]
                    if bln.strip().startswith('```'):
                        peek += 1
                        break
                    block.append(bln)
                    peek += 1
                blocks.append('\n'.join(block).strip('\n'))
            # 遇到空行，跳过继续找代码块
            elif stripped == '':
                peek += 1
            # 遇到其他内容，认为多行指令内容结束
            else:
                break
        return blocks, peek
    while i < len(lines):
        line = lines[i].strip()
        if not line or line.startswith('#'):
            i += 1
            continue
        parts = line.split(None, 1)
        cmd = parts[0].lower()
        arg = parts[1] if len(parts) > 1 else ''
        # 处理多行指令
        # [修改] 新增 exec：支持 "exec + 代码块" 格式，代码块内容即要执行的命令
        if cmd in ('create', 'append', 'replace', 'insert', 'find', 'deleteline', 'remember', 'memory', 'exec'):
            # deleteline 如果带 -l 是单行
            if cmd == 'deleteline' and '-l' in arg:
                task_id = str(uuid.uuid4())
                task_queue.put({'id': task_id, 'cmd': line})
                task_ids.append(task_id)
                log_action('ENQUEUE', f'ID: {task_id} | CMD: {line}')
                i += 1
                continue
            # 提取后续的代码块
            blocks, next_i = extract_blocks(i + 1)
            if len(blocks) > 0:
                if cmd == 'replace':
                    if len(blocks) >= 2:
                        final_cmd = f"replace {arg}\x00{blocks[0]}\x00{blocks[1]}"
                        task_id = str(uuid.uuid4())
                        task_queue.put({'id': task_id, 'cmd': final_cmd})
                        task_ids.append(task_id)
                        log_action('ENQUEUE', f'ID: {task_id} | CMD: {final_cmd}')
                        i = next_i
                        continue
                    elif len(blocks) == 1 and '-l' in arg:
                        final_cmd = f"replace {arg}\x00{blocks[0]}"
                        task_id = str(uuid.uuid4())
                        task_queue.put({'id': task_id, 'cmd': final_cmd})
                        task_ids.append(task_id)
                        log_action('ENQUEUE', f'ID: {task_id} | CMD: {final_cmd}')
                        i = next_i
                        continue
                elif cmd == 'exec':
                    # [修改] exec 升级为多代码块：每个块独立入队 = 独立任务 = 独立 PowerShell 进程，
                    # 无状态共享、不拼接。单块行为与旧版完全一致；无块时本分支不进入，
                    # 由末尾单行逻辑兜底（此时 arg 即命令本身）
                    for block in blocks:
                        final_cmd = f"exec {arg}\x00{block}"
                        task_id = str(uuid.uuid4())
                        task_queue.put({'id': task_id, 'cmd': final_cmd})
                        task_ids.append(task_id)
                        log_action('ENQUEUE', f'ID: {task_id} | CMD: {final_cmd}')
                    i = next_i
                    continue
                elif cmd in ('create', 'append', 'insert', 'find', 'remember', 'memory'):
                    # 这些指令只需要一个内容块
                    final_cmd = f"{cmd} {arg}\x00{blocks[0]}"
                    task_id = str(uuid.uuid4())
                    task_queue.put({'id': task_id, 'cmd': final_cmd})
                    task_ids.append(task_id)
                    log_action('ENQUEUE', f'ID: {task_id} | CMD: {final_cmd}')
                    i = next_i
                    continue
                elif cmd == 'deleteline':
                    final_cmd = f"deleteline {arg}\x00{blocks[0]}"
                    task_id = str(uuid.uuid4())
                    task_queue.put({'id': task_id, 'cmd': final_cmd})
                    task_ids.append(task_id)
                    log_action('ENQUEUE', f'ID: {task_id} | CMD: {final_cmd}')
                    i = next_i
                    continue
            # 如果没收集到块，当作单行处理
            task_id = str(uuid.uuid4())
            task_queue.put({'id': task_id, 'cmd': line})
            task_ids.append(task_id)
            log_action('ENQUEUE', f'ID: {task_id} | CMD: {line}')
            i += 1
        else:
            # 其他单行指令
            task_id = str(uuid.uuid4())
            task_queue.put({'id': task_id, 'cmd': line})
            task_ids.append(task_id)
            log_action('ENQUEUE', f'ID: {task_id} | CMD: {line}')
            i += 1
    return jsonify({'type': 'task_batch', 'task_ids': task_ids})
@app.route('/agent-file-download')
def agent_file_download():
    """下载临时文件，并在响应完成后自动清理"""
    file_id = request.args.get('id')
    if not file_id:
        return "错误：缺少文件ID", 400
    if not re.match(r'^[a-f0-9-]+$', file_id):
        return "错误：无效的文件ID格式", 400
    # [修复] 使用动态路径，跟随 WORK_DIR 变化
    file_path = os.path.join(get_temp_dir(), file_id)
    # [调试日志] 打印一下请求路径，看看到底收到了什么ID
    print(f'[Download] 请求文件ID: {file_id}, 工作目录: {WORK_DIR}, 路径: {file_path}')
    if not os.path.exists(file_path):
        temp_dir = get_temp_dir()
        print(f'[Download] 文件不存在，当前目录内容: '
              f'{os.listdir(temp_dir) if os.path.exists(temp_dir) else "目录不存在"}')
        return "错误：文件不存在或已过期", 404
    try:
        with open(file_path, 'rb') as f:
            file_data = f.read()
        response = Response(file_data, mimetype='application/octet-stream')
        def cleanup():
            try:
                os.remove(file_path)
                # [修复] 尝试删除空目录
                temp_dir = get_temp_dir()
                if os.path.exists(temp_dir) and not os.listdir(temp_dir):
                    os.rmdir(temp_dir)
                print(f'[Download] 文件已清理: {file_id}')
            except OSError:
                pass
        response.call_on_close(cleanup)
        return response
    except Exception as e:
        print(f'[Download] 读取文件异常: {e}')
        return f'下载失败: {e}', 500
@app.route('/agent-config-poll', methods=['GET'])
def agent_config_poll():
    _config_changed.wait(timeout=25)
    _config_changed.clear()
    return jsonify({
        'clipboard_mode': clipboard_mode,
        'permission_enabled': permission_mgr.enabled,
        'exec_enabled': exec_enabled
    })
@app.route('/agent-memory-inject', methods=['GET'])
def agent_memory_inject():
    """返回当前的短期记忆内容和长期记忆标签云，供前端注入到输入框"""
    content = memory_engine.get_inject_content()
    return jsonify({'memory': content}) if content else jsonify({'memory': ''})
@app.route('/agent-memory-tick', methods=['GET'])
def agent_memory_tick():
    """[新增] 前端对话回合信号：一次对话衰减一次温度。
    去重由前端完成（跨标签页 GM 存储共享 round key），此处纯执行无状态"""
    memory_engine.tick()
    return jsonify({'ticked': True})
if __name__ == '__main__':
    permission_mgr.set_callback(_default_permission_callback)
    _push_config()
    print(f'========================================')
    print(f'  PokerAgent 本地服务已启动 (SSE流式版)')
    print(f'  监听地址：http://127.0.0.1:9966')
    print(f'  工作目录：{WORK_DIR}')
    print(f'  帮助文档：{HELP_FILE}')
    print(f'  操作日志：{LOG_FILE}')
    print(f'========================================')
    app.run(host='127.0.0.1', port=9966, debug=False, threaded=True)
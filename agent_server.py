"""
PokerAgent - 本地接应服务 (SSE流式版) v50
启动方式：python agent_server.py
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
import codecs  # [exec v2.1] 增量解码器（多字节劈叉免疫）
from collections import deque  # [新增] 跳过计划表用 FIFO 队列
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
# [新增·B1] exec/run 超时（秒）：原硬编码 3600/60，支持 agent_config.json 与 GUI 配置
EXEC_TIMEOUT_SEC = 3600
RUN_TIMEOUT_SEC = 60
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 任务队列与 SSE 流式架构
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
task_queue = queue.Queue()
# [新增] 任务控制共享状态（GUI 按钮 → Worker 线程）
_current_process = None  # 当前正在执行的子进程引用
_current_process_lock = threading.Lock()
_current_job = None  # [exec v2.1] 当前任务 Job Object 句柄（与 _current_process 同锁）
# [重构] 暂停控制：Event → Condition + 布尔态（单步放行功能的根源性前提）。
# Event.set() 是粘性的，无法表达"暂停态下仅放行一个任务"；worker 事后复位事件
# 又无法区分置位来源（单步 or 用户恢复），故换用带状态的条件变量。
# _paused: True=队列暂停；_step_pending: 单步令牌（1=放行一个任务，消费即清零）
_pause_cond = threading.Condition()
_paused = False
_step_pending = 0
_kill_mode = None  # None / 'discard' / 'done'
_kill_mode_lock = threading.Lock()
# [新增] 跳过计划表（FIFO 动作队列）：暂停队列时可预置，worker 每取出一个任务消费队首一个动作。
# 'discard' = 该任务不执行直接丢弃；'done' = 该任务不执行直接标记完成。
# 点击顺序即作用顺序（先点的先作用），两个按钮共用一张表
_skip_plan = deque()
_skip_plan_lock = threading.Lock()
_skip_plan_callback = None  # [新增] 计划表变更回调 cb(discard_n, done_n)，GUI 按钮计数显示用
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 任务状态注册表（解决 SSE 晚订阅竞态：新客户端连接时回放历史状态）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_task_registry = {}  # task_id -> {'status':..., 'logs':[...], 'result':...}
_task_registry_lock = threading.Lock()
def emit_task_event(evt):
    """更新任务注册表并推送给所有已连接的 SSE 客户端（SSE 侧自动剥离 ANSI 颜色码）"""
    task_id = evt.get('id')
    # [修复] 无任务上下文（CLI 手动执行，task_id=None）：直接返回。
    # 不入注册表（原 'cli-manual' 条目永不被清理 → 泄漏），也不推 SSE
    # （原 push_event 在 if 块外，id=None 事件照样推给浏览器，污染前端回放）
    if not task_id:
        return
    with _task_registry_lock:
        if task_id not in _task_registry:
            _task_registry[task_id] = {'status': 'waiting', 'logs': [], 'result': ''}
        entry = _task_registry[task_id]
        if evt.get('type') == 'status':
            entry['status'] = evt.get('status', entry['status'])
            if 'result' in evt:
                entry['result'] = strip_ansi(evt['result'])
        elif evt.get('type') == 'log':
            logs = entry['logs']
            logs.append(strip_ansi(evt.get('data', '')))
            # [exec v2.1] 注册表只做回放预览；完整真相在 .agent_task_logs 任务文件
            if len(logs) > _REGISTRY_LOG_CAP:
                del logs[:-_REGISTRY_LOG_CAP]
    # [修改] 推送前剥离 ANSI，前端/LLM 拿到干净文本（原在 if task_id 块内，现随 early-return 结构外提一级）
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
def emit_task_note(task_id, status, text, result=None):
    """[note 机制] 追加型收尾：注册表 status 翻转 +（可选）result 写入（供晚订阅回放）+ notes 追加；
    实时只推一条 note——前端在现有回执末尾追加一行，不覆写、不重发历史输出。
    （旧模式把终止说明+全部已有输出整体重发，前端只能覆写——已废弃）"""
    with _task_registry_lock:
        entry = _task_registry.get(task_id)
        if entry is None:
            return
        entry['status'] = status
        if result is not None:
            entry['result'] = result
        entry.setdefault('notes', []).append(text)
    push_event({'id': task_id, 'type': 'note', 'status': status, 'text': text})
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
            # [修复·B2] 补 timeout=5：原无超时，被 GUI 线程同步调用时最坏卡死数秒
            subprocess.run(f'taskkill /F /T /PID {proc.pid}', shell=True,
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=5)
        except Exception:
            pass
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [exec v2.1] 文件落盘执行核心 + Job Object 树级管控
# 根因消灭对照（9/20、9/21 两起挂死事故）：
#   孤儿攥管道滴水   → 无管道：stdout 落每任务独立文件，滴流与完成判定彻底解耦
#   drain 永不安静   → 概念删除：完成判定 = 顶层 shell 退出（OS 事件 poll）
#   超时分支不可达   → 完成/中断/超时三检查同节拍轮询，互不耦合
#   超时丢弃全部回执 → 回执 = 文件内容，超时文案附带末尾输出
#   终止打不到树梢   → TerminateJobObject 树级原子歼灭（含被领养孤儿）
#   滴流取证困难     → 滴流原样落 .agent_task_logs，自动留证
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
_EXEC_TICK_SEC = 0.05          # 主循环节拍：中断/超时响应上限
_EXEC_DRAIN_SEC = 1.5          # 顶层退出后的排水静默窗（v1 的 drain 是 3.3s，这里更快）
_EXEC_DRAIN_MAX_SEC = 5.0      # 排水硬上限（防"迟到输出"永不停止）
_EMIT_LINES_PER_TICK = 200     # 每 tick SSE 推送行数上限（洪峰削峰，残余下轮续传）
_PUMP_READ_CHUNK = 1024 * 1024 # 单次读文件字节上限
_REGISTRY_LOG_CAP = 500        # 注册表日志行数上限（回放预览用，完整真相在文件）
_RECEIPT_MAX_BYTES = 16 * 1024 * 1024  # 回执读取上限，超出读尾部并标注
_TIMEOUT_RECEIPT_LINES = 30    # 超时文案附带的末尾行数
_TASK_LOG_KEEP_DAYS = 7        # 任务日志保留天数
EXEC_JOB_KILL_ON_CLOSE = True  # True=任务结束即歼灭整树（构建冷启动，状态有界）
                               # False=树存活到 harness 退出（daemon 保温构建快，滴流入文件无害）
                               # [残留检测] GUI 可 setattr 切换，随 agent_config.json 持久化
DOWNLOAD_TIMEOUT_SEC = 300     # download 总时长上限
def _task_log_dir():
    """[exec v2.1] 每任务输出日志目录（跟随 WORK_DIR）"""
    return os.path.join(WORK_DIR, '.agent_task_logs')
def _remove_quiet(path):
    try:
        if os.path.exists(path):
            os.remove(path)
    except OSError:
        pass
def _decode_blob(data):
    """整块解码：优先 UTF-8，失败回退 GBK（文件级回执用，与 smart_decode 同策略）"""
    if not data:
        return ''
    try:
        return data.decode('utf-8')
    except UnicodeDecodeError:
        return data.decode('gbk', errors='replace')
# ---- Job Object（Windows 原生进程树管控）----
if platform.system() == 'Windows':
    import ctypes
    from ctypes import wintypes as _wt
    _k32 = ctypes.WinDLL('kernel32', use_last_error=True)
    _k32.CreateJobObjectW.restype = _wt.HANDLE
    _k32.CreateJobObjectW.argtypes = [_wt.LPVOID, _wt.LPWSTR]
    _k32.SetInformationJobObject.restype = _wt.BOOL
    _k32.SetInformationJobObject.argtypes = [_wt.HANDLE, ctypes.c_int, ctypes.c_void_p, _wt.DWORD]
    _k32.AssignProcessToJobObject.restype = _wt.BOOL
    _k32.AssignProcessToJobObject.argtypes = [_wt.HANDLE, _wt.HANDLE]
    _k32.TerminateJobObject.restype = _wt.BOOL
    _k32.TerminateJobObject.argtypes = [_wt.HANDLE, _wt.UINT]
    _k32.CloseHandle.restype = _wt.BOOL
    _k32.CloseHandle.argtypes = [_wt.HANDLE]
    # [残留检测] QueryInformationJobObject 显式声明 argtypes：HANDLE 是 c_void_p，
    # 不设 argtypes 时 ctypes 会按 c_int 传参，64 位下截断指针（未显式声明即 100% 失效）
    _k32.QueryInformationJobObject.restype = _wt.BOOL
    _k32.QueryInformationJobObject.argtypes = [
        _wt.HANDLE, ctypes.c_int, ctypes.c_void_p, _wt.DWORD, ctypes.POINTER(_wt.DWORD)]
    _JobObjectExtendedLimitInformation = 9
    _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE = 0x00002000
    class _IO_COUNTERS(ctypes.Structure):
        _fields_ = [('ReadOperationCount', ctypes.c_ulonglong),
                    ('WriteOperationCount', ctypes.c_ulonglong),
                    ('OtherOperationCount', ctypes.c_ulonglong),
                    ('ReadTransferCount', ctypes.c_ulonglong),
                    ('WriteTransferCount', ctypes.c_ulonglong),
                    ('OtherTransferCount', ctypes.c_ulonglong)]
    class _JOBOBJECT_BASIC_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('PerProcessUserTimeLimit', ctypes.c_longlong),
                    ('PerJobUserTimeLimit', ctypes.c_longlong),
                    ('LimitFlags', _wt.DWORD),
                    ('MinimumWorkingSetSize', ctypes.c_size_t),
                    ('MaximumWorkingSetSize', ctypes.c_size_t),
                    ('ActiveProcessLimit', _wt.DWORD),
                    ('Affinity', ctypes.c_size_t),
                    ('PriorityClass', _wt.DWORD),
                    ('SchedulingClass', _wt.DWORD)]
    class _JOBOBJECT_EXTENDED_LIMIT_INFORMATION(ctypes.Structure):
        _fields_ = [('BasicLimitInformation', _JOBOBJECT_BASIC_LIMIT_INFORMATION),
                    ('IoInfo', _IO_COUNTERS),
                    ('ProcessMemoryLimit', ctypes.c_size_t),
                    ('JobMemoryLimit', ctypes.c_size_t),
                    ('PeakProcessMemoryUsed', ctypes.c_size_t),
                    ('PeakJobMemoryUsed', ctypes.c_size_t)]
def _job_create():
    """创建 Job Object；失败/非 Windows 返回 None（退化为 taskkill 兜底）"""
    if platform.system() != 'Windows':
        return None
    try:
        job = _k32.CreateJobObjectW(None, None)
        if not job:
            return None
        if EXEC_JOB_KILL_ON_CLOSE:
            info = _JOBOBJECT_EXTENDED_LIMIT_INFORMATION()
            info.BasicLimitInformation.LimitFlags = _JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not _k32.SetInformationJobObject(job, _JobObjectExtendedLimitInformation,
                                                ctypes.byref(info), ctypes.sizeof(info)):
                _k32.CloseHandle(job)
                return None
        return job
    except Exception:
        return None
def _job_assign(job, proc):
    """挂入 Job：子进程自动继承（daemon/孙子/被领养孤儿一并受控）"""
    if job and proc:
        try:
            _k32.AssignProcessToJobObject(job, int(proc._handle))
        except Exception:
            pass
def _job_kill(job):
    if job:
        try:
            _k32.TerminateJobObject(job, 1)
        except Exception:
            pass
def _job_close(job):
    if job:
        try:
            _k32.CloseHandle(job)
        except Exception:
            pass
def _job_alive_count(job):
    """[残留检测] Job 内当前存活进程数。顶层退出后调用：>0 = 有存活后代（构建场景=daemon）。
    查询失败返回 None —— 检测不了就沉默，宁漏报不噪音。"""
    if not job:
        return None
    try:
        buf = (ctypes.c_ulong * 1026)()   # [assigned][in_list][pid×~512]（64位下余量已足）
        ret_len = _wt.DWORD(ctypes.sizeof(buf))
        if not _k32.QueryInformationJobObject(job, 3, ctypes.byref(buf),   # 3=JobObjectBasicProcessIdList
                                              ctypes.sizeof(buf), ctypes.byref(ret_len)):
            return None
        return int(buf[1])                # NumberOfProcessIdsInList = 当前存活
    except Exception:
        return None
def _residue_note(alive):
    """残留警告：只在 alive>0 时被调用。随模式给出后果 + 操作指引"""
    if EXEC_JOB_KILL_ON_CLOSE:
        return (f'\n\n[agent] ⚠ 任务结束后仍有 {alive} 个后台进程存活（如 gradle daemon），'
                '已随任务一并终止，下次构建将冷启动。'
                '（切换行为：GUI 控制面板「任务结束销毁残留进程」）')
    return (f'\n\n[agent] ℹ 任务结束后仍有 {alive} 个后台进程存活（如 gradle daemon），'
            '其后续输出继续写入 .agent_task_logs 对应任务日志（不影响上方回执）；'
            '它们已脱离本系统管辖，gradle daemon 将在闲置约 3 小时后自行退出。'
            '（切换行为：GUI 控制面板「任务结束销毁残留进程」）')
class _TaskLogTailer:
    """任务日志增量尾随器：
    - 多字节安全：UTF-8 优先、失败切 GBK；残字节跨读段保存（劈叉免疫）
    - 行缓冲：半行不外发；finish() 冲出残字节与半行"""
    def __init__(self):
        self._buf = b''
        self._dec = None
        self._enc = 'utf-8'
        self._line_buf = ''
    def feed(self, data):
        if not data:
            return []
        if self._dec is None:
            self._buf += data
            # 探测时机：攒到行边界或 8KB，避免把截断的 UTF-8 误判成 GBK
            if not (self._buf.endswith(b'\n') or len(self._buf) >= 8192):
                return []
            try:
                text = self._buf.decode('utf-8'); self._enc = 'utf-8'
            except UnicodeDecodeError:
                try:
                    text = self._buf.decode('gbk'); self._enc = 'gbk'
                except UnicodeDecodeError:
                    if len(self._buf) < 8192:
                        return []
                    text = self._buf.decode('utf-8', errors='replace'); self._enc = 'utf-8'
            self._dec = codecs.getincrementaldecoder(self._enc)(errors='replace')
            self._buf = b''
        else:
            text = self._dec.decode(data)
        return self._split(text)
    def finish(self):
        if self._dec is None:
            text = self._buf.decode(self._enc, errors='replace')
        else:
            text = self._dec.decode(b'', final=True)
        self._buf = b''
        out, self._line_buf = self._line_buf + text, ''
        return out
    def _split(self, text):
        if not text:
            return []
        self._line_buf += text
        parts = self._line_buf.split('\n')
        self._line_buf = parts.pop()      # 最后一段是半行或 ''，留在缓冲
        return parts
def _cleanup_old_task_logs():
    """机会式清理过期任务日志；可能被存活 daemon 占用——失败静默跳过"""
    try:
        cutoff = time.time() - _TASK_LOG_KEEP_DAYS * 86400
        log_dir = _task_log_dir()
        if os.path.isdir(log_dir):
            for name in os.listdir(log_dir):
                fp = os.path.join(log_dir, name)
                try:
                    if os.path.isfile(fp) and os.path.getmtime(fp) < cutoff:
                        os.remove(fp)
                except OSError:
                    pass
    except Exception:
        pass
def _read_receipt(log_path, freeze_pos):
    """回执 = 文件内容（冻结点前）。超限读尾部并标注。返回剥净文本（可为空串）"""
    try:
        size = os.path.getsize(log_path)
    except OSError:
        return '（回执读取失败：日志文件被占用，全文见 .agent_task_logs）'
    read_from = 0
    note = ''
    end = min(freeze_pos, size)
    if end > _RECEIPT_MAX_BYTES:
        read_from = end - _RECEIPT_MAX_BYTES
        note = f'（回执过长，仅保留末尾 {_RECEIPT_MAX_BYTES // (1024 * 1024)}MB，全文见 .agent_task_logs）\n'
    try:
        with open(log_path, 'rb') as f:
            f.seek(read_from)
            data = f.read(end - read_from)
    except OSError:
        return '（回执读取失败：日志文件被占用，全文见 .agent_task_logs）'
    text = _decode_blob(data).strip()
    return note + text if text else ''
def _tail_lines_text(log_path, freeze_pos, n=_TIMEOUT_RECEIPT_LINES):
    """超时附证：冻结点前末尾 n 个非空行（空行滴流无取证价值，剔除）"""
    try:
        with open(log_path, 'rb') as f:
            start = max(0, freeze_pos - 16384)
            f.seek(start)
            data = f.read(freeze_pos - start)
    except OSError:
        return '（无输出可附）'
    lines = [l.rstrip() for l in _decode_blob(data).splitlines() if l.strip()]
    return '\n'.join(lines[-n:]) if lines else '（无输出可附）'
def _stream_process_to_file(argv, task_id, timeout_sec, shell=False):
    """[exec v2.1] 统一执行核心（exec/run 共用）：
    stdout/stderr 直接落每任务独立文件（无管道）+ Job Object 树级管控 +
    固定节拍轮询（完成/中断/超时三检查互相独立）。
    正常返回回执；超时返回附末尾输出的文案；中断抛 TaskAborted（树已歼灭）。"""
    global _current_process, _current_job
    _cleanup_old_task_logs()
    log_dir = _task_log_dir()
    os.makedirs(log_dir, exist_ok=True)
    log_path = os.path.join(log_dir, f"{(task_id or 'cli')[:8]}-{int(time.time())}.log")
    job = None
    proc = None
    pending = deque()                # 已解码待推送整行（削峰队列，字节已消费不重读）
    tailer = _TaskLogTailer()
    def _pump(file_pos):
        """读文件新增字节 → 解码 → 限流推送；返回推进后的 file_pos"""
        try:
            size = os.path.getsize(log_path)
        except OSError:
            return file_pos
        if size > file_pos and len(pending) < _EMIT_LINES_PER_TICK:
            try:
                with open(log_path, 'rb') as f:
                    f.seek(file_pos)
                    data = f.read(min(size - file_pos, _PUMP_READ_CHUNK))
            except OSError:
                return file_pos
            if data:
                file_pos += len(data)
                pending.extend(tailer.feed(data))
        if pending:
            batch = [pending.popleft() for _ in range(min(_EMIT_LINES_PER_TICK, len(pending)))]
            for ln in batch:
                emit_task_event({'id': task_id, 'type': 'log', 'data': ln.rstrip()})
        return file_pos
    def _drain(file_pos):
        """顶层退出后的排水窗：孙子进程迟到输出照常推送，静默即冻结"""
        quiet_since = None
        deadline = time.time() + _EXEC_DRAIN_MAX_SEC
        while True:
            _check_abort()
            new_pos = _pump(file_pos)
            now = time.time()
            if new_pos > file_pos:
                file_pos = new_pos
                quiet_since = now
            elif quiet_since is None:
                quiet_since = now
            if now - quiet_since >= _EXEC_DRAIN_SEC or now >= deadline:
                return file_pos
            time.sleep(_EXEC_TICK_SEC)
    try:
        # 1) spawn：stdout 落文件（无管道→无 EOF/drain 概念）；stdin 断开防怪异子进程读控制台
        log_f = open(log_path, 'wb')
        try:
            proc = subprocess.Popen(argv, stdout=log_f, stderr=subprocess.STDOUT,
                                    stdin=subprocess.DEVNULL, cwd=WORK_DIR, shell=shell)
        finally:
            log_f.close()            # 子进程已持有继承句柄，写侧交割完毕
        # 2) Job：整树受控（含后续产生的 daemon/被领养孤儿）
        job = _job_create()
        _job_assign(job, proc)
        with _current_process_lock:
            _current_process = proc
            _current_job = job
        file_pos = 0
        start_time = time.time()
        timed_out = False
        # 3) 主循环：三检查同节拍、互相独立——v1 挂死的对 Kore
        while True:
            _check_abort()                                   # 中断 ≤50ms
            if time.time() - start_time > timeout_sec:       # 超时恒可达（与数据流无关）
                timed_out = True
                break
            if proc.poll() is not None:                      # 完成 = OS 事件
                break
            file_pos = _pump(file_pos)
            time.sleep(_EXEC_TICK_SEC)
        # 4) 收尾：残留检测必须在 kill/close 之前（歼灭后 Job 清空，查无所获）
        alive = _job_alive_count(job)
        if timed_out:
            _job_kill(job)
        freeze_pos = _drain(file_pos)
        try:
            proc.wait(timeout=5)
        except Exception:
            pass
        # 5) 回执构建（来自文件，超时也不丢）
        if timed_out:
            receipt = (f'错误：命令执行超时（{timeout_sec}秒限制），进程树已强杀。\n'
                       f'—— 末尾输出 ——\n{_tail_lines_text(log_path, freeze_pos)}')
        else:
            while pending:                                       # SSE 尾批冲刷
                batch = [pending.popleft() for _ in range(min(_EMIT_LINES_PER_TICK, len(pending)))]
                for ln in batch:
                    emit_task_event({'id': task_id, 'type': 'log', 'data': ln.rstrip()})
            residual = tailer.finish()                           # 残字节 + 半行
            if residual.strip():
                emit_task_event({'id': task_id, 'type': 'log', 'data': residual.rstrip()})
            receipt = _read_receipt(log_path, freeze_pos)
            receipt = receipt if receipt else '（命令已执行，无输出）'
        # [残留警告] 仅当真有活口时追加；0/None 沉默。中断路径不打（用户主动终止，前端已有提示）
        if alive:
            receipt += '\n' + _residue_note(alive)
        return receipt
    except TaskAborted:
        _job_kill(job)                                       # 树级歼灭（含 v1 修不掉的孤儿）
        _kill_process_tree(proc)                             # taskkill 双保险（Job 建立失败时兜底）
        raise
    finally:
        _job_close(job)          # KILL_ON_JOB_CLOSE=True → 任务结束 = 整树不复存在
        with _current_process_lock:
            if _current_process is proc:
                _current_process = None
            if _current_job is job:
                _current_job = None
        # 日志文件不删：完整真相留档，_cleanup_old_task_logs 按天回收
# [新增] 任务控制接口（供 GUI 调用）
def request_kill(mode):
    """请求终止当前任务。mode: 'discard'=丢弃结果 / 'done'=返回已有输出
    [重构·根源修复·B3] 旧版被 GUI 在 Tk 主线程同步调用，函数体内有两处阻塞源：
    1) proc.stdout.close()：reader 线程阻塞在 readline() 时持有缓冲区锁，close() 抢
       同一把锁 → Tk 主线程永久冻结（"点终止会卡死"的根源）。新版彻底删除 close()：
       exec/run 循环的 _check_abort() 检查点(≤50ms)自行退出，管道 EOF 由杀透进程树
       自然达成（TaskAborted 分支兜底杀树，见 execute_line_streaming）。
    2) subprocess.run(taskkill) 无 timeout：已在 _kill_process_tree 补 timeout=5。
    新版锁内只做无阻塞状态置位，进程树击杀挪至后台 daemon 线程，调用线程恒不阻塞"""
    global _kill_mode
    if _current_task_id is None:
        return False  # 没有正在执行的任务，忽略
    with _kill_mode_lock:
        _kill_mode = mode
    # 先 set 中断信号，让所有纯 Python 循环立即抛出 TaskAborted
    _abort_event.set()
    with _current_process_lock:
        proc = _current_process
        job = _current_job
    if job:
        # [exec v2.1] 树级原子歼灭：Job 内所有进程（含被领养孤儿）立即死亡，不阻塞调用线程
        _job_kill(job)
    if proc:
        # 双保险兜底：Job 建立失败（返回 None）或 assign 前微秒窗口逃逸者
        threading.Thread(target=_kill_process_tree, args=(proc,), daemon=True, name='kill-worker').start()
    return True
def request_pause():
    """暂停任务队列（当前任务继续执行完，不再取新任务）。不 notify：worker 若在执行任务自会走到门检"""
    global _paused
    with _pause_cond:
        _paused = True
def request_resume():
    """恢复任务队列"""
    global _paused
    with _pause_cond:
        _paused = False
        _pause_cond.notify_all()  # 唤醒睡眠在门检上的 worker
def request_step_once():
    """[新增] 单步放行令牌：暂停态下放行任务，执行完自动回到暂停冻结。
    [修改] 覆盖式赋值(=1) → 累加(+=1)：单步执行期间连点 N 次 → 连续放行 N 个，点击不丢。
    运行态下残留令牌在下一轮门检统一清零作废（语义不变）"""
    global _step_pending
    with _pause_cond:
        _step_pending += 1  # [修改] 原 _step_pending = 1：覆盖式赋值，连点只记一次
        _pause_cond.notify_all()  # 唤醒睡眠中的 worker 消费令牌
def is_paused():
    """[新增·上轮函数重写] 队列是否处于暂停态（跳过计划/单步均仅允许暂停时操作）"""
    with _pause_cond:
        return _paused
def set_skip_plan_callback(cb):
    """[新增] 注册跳过计划表变更回调：签名 cb(discard_count, done_count)。
    可能从 worker 线程触发，回调体内不得直接操作 Tk 控件（GUI 侧自行 after 调度）"""
    global _skip_plan_callback
    _skip_plan_callback = cb
def request_skip_next(mode):
    """[新增] 向跳过计划表队尾追加一个动作（累计模式：点几次攒几个，按点击顺序 FIFO 消费）。
    校验：未消费计划总数 + 1 ≤ 队列当前积压数（计划不得指向不存在的任务）。
    返回 True=已入队 / False=被拒绝"""
    if mode not in ('discard', 'done'):
        return False
    with _skip_plan_lock:
        if len(_skip_plan) + 1 > task_queue.qsize():
            return False  # 计划总量超出队列积压：拒绝（暂停态下 qsize 稳定，校验精确）
        _skip_plan.append(mode)
    _notify_skip_plan()
    return True
def clear_skip_plan():
    """[新增] 清空整张跳过计划表：未消费的预置动作全部作废（计数归零经 _notify_skip_plan 广播）"""
    with _skip_plan_lock:
        _skip_plan.clear()
    _notify_skip_plan()
def _notify_skip_plan():
    """[新增] 计划表变更后通知订阅方（点击入队 / worker 消费两个入口都触发）。
    锁外调用回调：回调内会重新取锁读计数，threading.Lock 不可重入，锁内调用即死锁"""
    cb = _skip_plan_callback
    if cb is None:
        return
    with _skip_plan_lock:
        d = _skip_plan.count('discard')
        n = _skip_plan.count('done')
    try:
        cb(d, n)
    except Exception:
        pass
def worker_loop():
    """后台 Worker 线程：严格串行执行任务"""
    global _current_task_id, _kill_mode
    import datetime
    print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] 🔧 Worker 线程已启动，等待任务...')
    while True:
        try:
            # [重构] 暂停门：运行态直接放行；暂停态睡眠在条件变量上，
            # 由 request_resume（恢复）或 request_step_once（单步令牌）唤醒。
            # 单步令牌仅放行一轮：本轮任务执行完回循环顶时令牌已消费且仍处暂停态，自动重新冻结
            with _pause_cond:
                while _paused and _step_pending <= 0:
                    _pause_cond.wait()  # 睡眠：等待恢复队列或单步放行信号
                _step_round = _paused  # 本轮是否单步放行（运行态过门时令牌作废）
                _step_pending = 0  # 令牌一律消费：运行态下的残留令牌直接作废（防御兜底）
            # [修改] 带超时的 get，确保暂停信号能及时生效（不会卡在无限阻塞的 get 上）
            try:
                task = task_queue.get(timeout=0.5)
            except queue.Empty:
                if _step_round:  # [新增] 单步放行但队列已空：令牌已消费，本轮作废，告知用户
                    print('[Worker] ▶ 单步放行但队列为空，本轮无任务可执行')
                continue  # 超时回循环顶部，重新检查暂停状态
            if task is None:
                print('[Worker] 收到退出信号，线程结束')
                break
            task_id = task['id']
            cmd_str = task['cmd']
            # [新增] 消费跳过计划表（FIFO 队首）：命中则该任务不执行，直接出对应状态。
            # 此处位于 _current_task_id 赋值之前，终止按钮不会误伤（无当前任务可终止）
            with _skip_plan_lock:
                skip_action = _skip_plan.popleft() if _skip_plan else None
            if skip_action == 'discard':
                print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] ⏭ 任务 {task_id[:8]} 按预置计划直接丢弃（未执行）')
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'killed', 'result': '任务已被预置计划丢弃（未执行）'})
                _notify_skip_plan()
                continue
            if skip_action == 'done':
                print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] ⏭ 任务 {task_id[:8]} 按预置计划直接返回 done（未执行）')
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'done', 'result': '任务已被预置计划直接完成（未执行）'})
                _notify_skip_plan()
                continue
            _abort_event.clear()  # [新增] 新任务开始前清除中断信号
            _current_task_id = task_id  # [新增] 记录当前任务
            print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] ⚙️ Worker 取出任务 {task_id[:8]}: {cmd_str}')
            emit_task_event({'id': task_id, 'type': 'status', 'status': 'running'})
            try:
                result = execute_line_streaming(cmd_str, task_id)
            except TaskAborted:
                # [新增] 任务被用户手动中断
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
                print(f'[{_ts}] ⛔ 任务 {task_id[:8]} 已终止并丢弃')
                emit_task_note(task_id, 'killed', '⛔ 当前任务已被用户手动终止（结果已丢弃）')
            elif mode == 'done':
                print(f'[{_ts}] ⛔ 任务 {task_id[:8]} 已终止，返回已有输出:')
                if _result_str:
                    print(_result_str)
                # 注册表存纯回执（回放时晚订阅者看到干净全文），实时只推一行追加提示
                emit_task_note(task_id, 'done', '⛔ 任务已被手动终止，以上回执为终止前的已有输出',
                               result=_result_str)
            else:
                # 正常完成（多行回执分行显示）
                if '\n' in _result_str:
                    print(f'[{_ts}] ✅ 任务 {task_id[:8]} 完成:')
                    print(_result_str)
                else:
                    print(f'[{_ts}] ✅ 任务 {task_id[:8]} 完成: {_result_str}')
                # [修改] 传 _result_str（恒为 str）：原始 result 若为 None（如空指令路径），
                # strip_ansi(None) 会在注册表写入时崩掉 worker；空串行为与原 None 一致（falsy 不回放）
                emit_task_event({'id': task_id, 'type': 'status', 'status': 'done', 'result': _result_str})
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
        # [新增·B1] exec/run 超时持久化
        'exec_timeout_sec': EXEC_TIMEOUT_SEC,
        'run_timeout_sec': RUN_TIMEOUT_SEC,
        # [新增·exec v2.1] Job Object 残留进程策略持久化
        'job_kill_on_close': EXEC_JOB_KILL_ON_CLOSE,
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
    global EXEC_TIMEOUT_SEC, RUN_TIMEOUT_SEC  # [新增·B1]
    global EXEC_JOB_KILL_ON_CLOSE  # [新增·exec v2.1]
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
        # [新增·B1] exec/run 超时（下限钳 1 秒，防 0/负值把超时判定变成立即超时）
        if 'exec_timeout_sec' in config:
            EXEC_TIMEOUT_SEC = max(1, int(config['exec_timeout_sec']))
        if 'run_timeout_sec' in config:
            RUN_TIMEOUT_SEC = max(1, int(config['run_timeout_sec']))
        # [新增·exec v2.1] Job Object 残留进程策略（旧配置无此项保持默认 True）
        if 'job_kill_on_close' in config:
            EXEC_JOB_KILL_ON_CLOSE = bool(config['job_kill_on_close'])
        print(f'[Agent] 配置已加载: {CONFIG_FILE}')
    except Exception as e:
        print(f'[Agent] 配置加载失败，使用默认值: {e}')
def _push_config():
    save_config()  # 每次配置变更时持久化
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 路径权限管理器
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    # [修复] task_id 传 None（原 'cli-manual'）：CLI 执行 exec/run 时流式日志经 emit_task_event
    # 写注册表，'cli-manual' 条目 status 恒为 waiting，永不被 agent_exec 的清理逻辑扫到
    # → 内存泄漏 + 每个新 SSE 客户端连接都被回放一个幽灵任务。传 None = 无任务上下文，
    # CLI 输出走 stdout → _LogWriter → GUI 日志区，本就不依赖注册表
    return execute_line_streaming(line, None)
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
    arg = arg.replace('\u201c', '"').replace('\u201d', '"')
    cs_idx = arg.find('【code】')
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
            f'  当前工作目录 "work_dir": "{WORK_DIR}",',
            f'  剪贴板读取模式 "clipboard_mode": {str(clipboard_mode).lower()},',
            f'  系统命令执行开关 "exec_enabled": {str(exec_enabled).lower()},',
            f'  终端类型 "shell_type": "{shell_type}",',
            f'  目录权限限制开关 "permission_enabled": {str(permission_mgr.enabled).lower()},',
            f'  始终允许列表条目数 "always_allow_count": {len(permission_mgr._always_allow)},',
            f'  操作系统 "platform": "{platform.system()}"',
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
        # [协议 v2] 内容只认【code】代码块（\x00 通道），内联文本废除：
        #   remember + 代码块            → 覆盖写入
        #   remember（完全空参数）        → 清空短期记忆（保留原语义）
        #   remember 内联文本（无代码块） → 报错（绝不静默清空，LLM 可据此自纠）
        block = ''
        if '\x00' in arg:
            arg, block = arg.split('\x00', 1)
            arg = arg.strip()
            block = block.strip('\n').replace('TICK3', '```')
        if not block:
            if arg:
                # 有内联文本但无代码块：协议违规，拒绝执行（防止误清空短期记忆）
                return '错误：remember 写入内容必须通过【code】代码块提供，内联文本已不支持。'
            memory_engine.write_short('')
            return '已清空短期记忆。'
        memory_engine.write_short(block)
        return '已更新短期记忆。'
    elif cmd == 'memory':
        # 长期记忆指令：支持多种子命令
        raw_arg = arg.strip()
        # [修复] 多行代码块：拆分 "\x00" 分隔的代码块（原样混入导致记忆内容损坏）。
        # inline 部分承载 tag:/-pin 修饰符，代码块作为正文
        mem_block = ''
        if '\x00' in raw_arg:
            raw_arg, mem_block = raw_arg.split('\x00', 1)
            raw_arg = raw_arg.strip()
            mem_block = mem_block.strip('\n').replace('TICK3', '```')
        # [修改] 仅 block 无 inline 参数时也放行（原来是直接报缺参数）
        if not raw_arg and not mem_block:
            return '错误：memory 指令缺少参数。发送 @@help memory 获取指令详细用法'
        # ── 子命令：search ──
        # [协议 v2] 标签/内容模式强制显式分流（原为标签优先、内容兜底自动回退）：
        #   memory search -tag 标签1,标签2    → 按标签匹配
        #   memory search -c 关键词1 关键词2  → 按内容匹配
        # 多关键词 OR 语义：任一关键词命中即算命中该条（与 grep -e 多模式一致）
        if raw_arg.lower().startswith('search'):
            spec = raw_arg[6:].strip()
            if not spec:
                return '错误：memory search 必须显式指定模式：-tag（按标签）或 -c（按内容）。'
            spec_parts = spec.split(None, 1)
            mode_flag = spec_parts[0].lower()
            if mode_flag not in ('-tag', '-c') or len(spec_parts) < 2:
                return '错误：memory search 必须显式指定模式：-tag（按标签）或 -c（按内容）。用法：memory search -tag a,b / memory search -c 词1 词2'
            # 标签模式：逗号/空白均可分隔（标签本身不含空白）；内容模式：仅按空白分词（关键词可含逗号）
            if mode_flag == '-tag':
                keywords = [k for k in re.split(r'[,\s，、]+', spec_parts[1]) if k]
            else:
                keywords = spec_parts[1].split()
            if not keywords:
                return '错误：memory search 关键词为空。'
            return memory_engine.search(keywords, 'tag' if mode_flag == '-tag' else 'content')
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
        # [协议 v2] ID 不存在不再 fall-through 到新增写入（内联死刑后此兜底路径也一并失效），
        # 直接按 ID 未找到报错，避免把数字串误当正文
        first_token = raw_arg.split(None, 1)[0] if raw_arg else ''
        if first_token.isdigit():
            mem_id = int(first_token)
            if memory_engine.memory_exists(mem_id):
                # 覆盖写入模式
                # [协议 v2] 正文只认代码块：无块直接报错；块外残留正文（内联）同样报错
                if not mem_block:
                    return '错误：memory <id> 覆盖写入内容必须通过【code】代码块提供，内联文本已不支持。'
                rest = raw_arg[len(first_token):].strip()
                # [修改] 末尾参数统一走解析器（与新增写入一致，temp:N / -pin / tag: 任意组合）
                content, tags, pin, custom_temp = _parse_memory_params(rest)
                if content.strip():
                    return '错误：memory 覆盖写入不支持内联文本，正文必须放在【code】代码块中。'
                content = mem_block
                success = memory_engine.overwrite_by_id(mem_id, content, tags, pin, custom_temp)
                if success:
                    return f'已覆盖写入长期记忆，编号 {mem_id:03d}'
                else:
                    return f'错误：未找到编号为 {mem_id:03d} 的记忆。'
            else:
                return f'错误：未找到编号为 {mem_id:03d} 的记忆（ID 不存在，新增请勿携带数字前缀）。'
        # ── 新增写入模式 ──
        # [协议 v2] 正文只认代码块：块外残留正文（内联）报错；无块且无正文也报错
        # [修改] 末尾参数（-pin / temp:N / tag:）统一走解析器，任意顺序组合
        content, tags, pin, custom_temp = _parse_memory_params(raw_arg)
        if content.strip():
            return '错误：memory 写入不支持内联文本，正文必须放在【code】代码块中。'
        if not mem_block:
            return '错误：memory 写入内容必须通过【code】代码块提供。'
        content = mem_block
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
            return (f'文件统计：{filepath}\n'
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
                return '错误：缺少文件路径或查找内容。发送 @@help find 获取指令详细用法'
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
                _check_abort()  # [新增] 匹配前检查中断
                # 调用通用匹配方法
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
                                diag.append(f'    文: {repr(f_proc[:120])}{"（仅前120字符）" if len(f_proc) > 120 else ""}')
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
        flag_set = set()          # 单字符标志集合（支持 -ivr 合并写法）
        patterns = []             # -e 显式指定的模式列表
        include_pattern = None    # --include 文件名过滤正则（字符串）
        exclude_pattern = None    # --exclude 文件名排除正则（字符串）
        non_opts = []             # 非选项参数（模式 / 路径）
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
        ignore_case = 'i' in flag_set      # 忽略大小写
        invert_match = 'v' in flag_set     # 反向匹配（输出不匹配的行）
        count_only = 'c' in flag_set       # 仅输出匹配行数
        files_only = 'l' in flag_set       # 仅输出含匹配的文件名
        whole_word = 'w' in flag_set       # 全词匹配（自动包 \b）
        recursive = 'r' in flag_set        # 递归搜索目录
        strip_indent = 's' in flag_set     # 匹配前去除行首空白（保留原有功能）
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
        # [新增] 代码块格式支持：exec 后跟【code】/```代码块时，代码块内容即要执行的命令
        # 内联文本与代码块同时存在时以代码块为准（内联丢弃）；不做 TICK3 替换，保证命令逐字保真
        if '\x00' in arg:
            arg = arg.split('\x00', 1)[1]
            if not arg.strip():
                return '错误：缺少命令。发送 @@help exec 获取指令详细用法'
        # [新增] 危险命令拦截与弹窗确认
        # [修改] 补漏：新增 ri（Remove-Item 别名）、shred、remove-item 及 PowerShell 磁盘破坏性命令
        # （clear-disk / initialize-disk / remove-partition；format 的词边界已天然覆盖 Format-Volume）
        # 注：ri 可能对含 "ri" 独立词的路径误报，但误报仅多一次确认弹窗，成本可接受
        dangerous_patterns = re.compile(
            r'\b(del|rd|rm|rmdir|ri|shred|format|erase|diskpart|mkfs|remove-item|clear-disk|initialize-disk|remove-partition)\b',
            re.IGNORECASE)
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
            # [exec v2.1] shell 选择不变，执行核心换文件落盘版
            if shell_type == 'powershell' and _POWERSHELL_EXE:
                argv = [_POWERSHELL_EXE, '-NoProfile', '-NonInteractive', '-Command', arg.strip()]
                return _stream_process_to_file(argv, task_id, EXEC_TIMEOUT_SEC)
            else:
                return _stream_process_to_file(f'cmd /c {arg.strip()}', task_id,
                                               EXEC_TIMEOUT_SEC, shell=True)
        except TaskAborted:
            raise      # 杀树已在核心内完成（Job 歼灭 + taskkill 双保险）
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
            return _stream_process_to_file(['python', script], task_id, RUN_TIMEOUT_SEC)
        except TaskAborted:
            raise
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
            req = urllib.request.Request(url, headers={'User-Agent': 'Agent/1.0 (PokerAgent)'})
            # [download v2] 三重修复：socket 超时(30s) + 总时长上限 + 分块循环中断检查点
            # （原 urlretrieve 无超时、无检查点——"终止按下却无效"的根源）
            with urllib.request.urlopen(req, timeout=30) as resp, open(save + '.part', 'wb') as f:
                total = 0
                t0 = time.time()
                while True:
                    _check_abort()                            # 终止按钮 ≤1s 生效
                    if time.time() - t0 > DOWNLOAD_TIMEOUT_SEC:
                        raise TimeoutError(f'下载总时长超过 {DOWNLOAD_TIMEOUT_SEC} 秒')
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
                    total += len(chunk)
            os.replace(save + '.part', save)                  # 原子落盘：中断/超时不留半截成品
            log_action('DOWNLOAD', f'{url} -> {save}')
            return f'已下载：{save}（{total} 字节）'
        except TaskAborted:
            _remove_quiet(save + '.part')
            raise
        except Exception as e:
            _remove_quiet(save + '.part')
            return f'下载失败：{e}'
    else:
        return f'未知指令：{cmd}\n输入 @@help fast 查看可用指令列表。'
_EXEC_SRC = inspect.getsource(execute_line_streaming)
KNOWN_CMDS = set(re.findall(r"cmd\s*==\s*'([^']+)'", _EXEC_SRC))
# [新增] memory 指令末尾修饰参数统一解析器（新增写入 / 覆盖写入共用）
def _parse_memory_params(raw):
    """
    解析 memory 指令末尾修饰参数，支持任意顺序组合：
      -pin    → 固定记忆
      temp:N  → 自定义初始温度（N 纯数字，如 temp:200）
      tag:a,b,c → 标签
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 记忆引擎
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
    def search(self, keywords, mode='tag', window=None):
        """搜索长期记忆，返回命中全文 + 上下 N 条元数据，触发加热
        [协议 v2] 标签/内容模式分离（原为标签优先、内容兜底自动回退）：
          mode='tag'     → 仅按标签匹配
          mode='content' → 仅按内容匹配
        [协议 v2] 多关键词 OR 语义：keywords 为列表，任一关键词命中即算该条命中（与 grep -e 一致）
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
        # 关键词统一小写（子串匹配不区分大小写）
        kws_lower = [k.lower() for k in keywords if k]
        if not kws_lower:
            return '错误：memory search 关键词为空。'
        # [协议 v2] 模式分流：不再自动回退，模式由指令显式指定
        if mode == 'tag':
            # 标签模式：条目的任一标签包含任一关键词即命中
            matched = [i for i, entry in enumerate(entries)
                       if any(kw in tag.lower() for tag in entry['tags'] for kw in kws_lower)]
            match_by = '标签'
        else:
            # 内容模式：正文包含任一关键词即命中
            matched = [i for i, entry in enumerate(entries)
                       if any(kw in entry['content'].lower() for kw in kws_lower)]
            match_by = '内容'
        if not matched:
            return f'未找到匹配 "{", ".join(keywords)}" 的记忆（模式：{match_by}）。'
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
                # [修改] 恢复记忆自身的 initial_temp（自定义 temp: 的记忆 unpin 后不丢失），
                # 旧数据无记录回退全局默认
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
                rf'<!-- ID:{mem_id:03d} -->\n.*?\n<!-- END:{mem_id:03d} -->\n?', re.DOTALL)
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
                rf'<!-- ID:{mem_id:03d} -->\n.*?\n<!-- END:{mem_id:03d} -->', re.DOTALL)
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动后台 Worker 线程（移到顶层，确保任何启动方式都能跑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动时加载持久化配置（必须在 permission_mgr 创建之后、worker 启动之前）
load_config()
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# [新增] 全量日志捕获 + 事件推送到 GUI
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
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
        self._name = stream_name      # 'out' 或 'err'，用于区分来源
    def write(self, s):
        if not s:
            return
        # 1. 原始输出（确保控制台/终端仍能看到日志）
        self._orig.write(s)
        self._orig.flush()  # 立即刷新，防止卡顿
        # 2. 文件持久化（线程安全追加写入）
        # [修改] 改用二进制模式：1) 字节偏移精确可追踪（供 GUI 窗口化回读定位）
        # 2) 消除 Windows 文本模式 \n→\r\n 隐式翻译
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
        # [修改] 消息附带本次写入的字节偏移区间
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
            # [修改·B7] killed 也回放 result（原仅 done）：终止说明同样属于回执信息
            if entry['status'] in ('done', 'killed') and entry['result']:
                evt['result'] = entry['result']
            q.put(f"data: {json.dumps(evt, ensure_ascii=False)}\n\n")
            # [exec v2.1] result 非空的正常 done 不回放日志（行为不变）；
            # result 空的终止型 done 回放日志补全回执（note 机制下终止型任务 result 为空）
            if entry['status'] != 'done' or not entry['result']:
                for log_line in entry['logs']:
                    log_evt = {'id': tid, 'type': 'log', 'data': log_line}
                    q.put(f"data: {json.dumps(log_evt, ensure_ascii=False)}\n\n")
            # [新增·note 机制] 回放 notes（终止提示行）
            for note_text in entry.get('notes', []):
                q.put(f"data: {json.dumps({'id': tid, 'type': 'note', 'status': entry['status'], 'text': note_text}, ensure_ascii=False)}\n\n")
        # [新增·B7] 回放结束哨兵：前端据此对账本地任务表，识别"后端重启导致注册表清空"的僵尸任务
        q.put('data: {"id": "all", "type": "replay_done"}\n\n')
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
# [新增·B6] 统一入队入口：注册表登记(waiting) + 入队 + 日志三合一
def _enqueue_task(cmd_str):
    """[新增] 统一入队入口：注册表登记(waiting) + 入队 + 日志三合一。
    原各分支散装 uuid+put：任务被 worker 取走前不在注册表里，SSE 晚订阅/断线重连的
    状态回放会整段漏掉排队中的任务。登记前置到入队时刻，回放完整覆盖全生命周期"""
    task_id = str(uuid.uuid4())
    with _task_registry_lock:
        _task_registry[task_id] = {'status': 'waiting', 'logs': [], 'result': ''}
    task_queue.put({'id': task_id, 'cmd': cmd_str})
    log_action('ENQUEUE', f'ID: {task_id} | CMD: {cmd_str}')
    return task_id
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
        # [修复] 清理条件补上 'killed'：原仅清 done，手动终止/预置丢弃的任务在注册表永久残留
        stale = [tid for tid, e in _task_registry.items() if e['status'] in ('done', 'killed')]
        for tid in stale:
            del _task_registry[tid]
    command_text = command_text.replace('\r\n', '\n').replace('\r', '\n')
    # [修改] 温度衰减信号已移至前端 /agent-memory-tick（一次对话衰减一次，原为每条指令一次）
    log_action('RECEIVED', command_text)
    lines = command_text.split('\n')
    i = 0
    task_ids = []
    # 提取代码块的独立函数，仅认 【code】...【/code】 边界。
    # [协议说明] ``` 不作为边界：它是前端的 markdown 渲染记号，正常链路下
    # 前端渲染消费后不会到达后端；若仍出现在块内，一律视为字面内容（不剥离、不匹配）。
    # LLM 侧约定：正文中需要字面 ``` 时用 TICK3 转义（前端不渲染转义序列）。
    def extract_blocks(start_idx):
        blocks = []
        peek = start_idx
        while peek < len(lines):
            stripped = lines[peek].strip()
            # 匹配 【code】...【/code】
            if '【code】' in stripped.lower():
                peek += 1
                block = []
                while peek < len(lines):
                    bln = lines[peek]
                    if '【/code】' in bln.lower():
                        idx = bln.lower().find('【/code】')
                        if idx != -1:
                            block.append(bln[:idx])
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
                task_ids.append(_enqueue_task(line))  # [重构·B6] 统一入队入口
                i += 1
                continue
            # 提取后续的代码块
            blocks, next_i = extract_blocks(i + 1)
            if len(blocks) > 0:
                if cmd == 'replace':
                    if len(blocks) >= 2:
                        final_cmd = f"replace {arg}\x00{blocks[0]}\x00{blocks[1]}"
                        task_ids.append(_enqueue_task(final_cmd))  # [重构·B6]
                        i = next_i
                        continue
                    elif len(blocks) == 1 and '-l' in arg:
                        final_cmd = f"replace {arg}\x00{blocks[0]}"
                        task_ids.append(_enqueue_task(final_cmd))  # [重构·B6]
                        i = next_i
                        continue
                elif cmd == 'exec':
                    # [修改] exec 升级为多代码块：每个块独立入队 = 独立任务 = 独立 PowerShell 进程，
                    # 无状态共享、不拼接。单块行为与旧版完全一致；无块时本分支不进入，
                    # 由末尾单行逻辑兜底（此时 arg 即命令本身）
                    # [修复·B6] 原 i=next_i/continue 误写进 for 体内（for-continue 语义）：
                    # 块循环结束后跌落到下方兜底单行入队，导致 exec+代码块每批额外多出
                    # 一条执行原始标记文本的垃圾任务。已修正：块全部入队后统一推进并 continue
                    for block in blocks:
                        final_cmd = f"exec {arg}\x00{block}"
                        task_ids.append(_enqueue_task(final_cmd))  # [重构·B6]
                    i = next_i
                    continue
                elif cmd in ('create', 'append', 'insert', 'find', 'remember', 'memory'):
                    # 这些指令只需要一个内容块
                    final_cmd = f"{cmd} {arg}\x00{blocks[0]}"
                    task_ids.append(_enqueue_task(final_cmd))  # [重构·B6]
                    i = next_i
                    continue
                elif cmd == 'deleteline':
                    final_cmd = f"deleteline {arg}\x00{blocks[0]}"
                    task_ids.append(_enqueue_task(final_cmd))  # [重构·B6]
                    i = next_i
                    continue
                # 如果没收集到块，当作单行处理
                task_ids.append(_enqueue_task(line))  # [重构·B6]
                i += 1
            else:
                # 其他单行指令
                task_ids.append(_enqueue_task(line))  # [重构·B6]
                i += 1
        else:
            # 其他单行指令
            task_ids.append(_enqueue_task(line))  # [重构·B6]
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
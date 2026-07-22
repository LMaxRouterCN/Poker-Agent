"""PokerAgent - 本地接应服务 (SSE流式版) v31
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
import fnmatch  # 用于 find 指令按文件名通配符递归搜索
import shutil  # 用于移动文件/目录到回收站
import time  # 用于回收站时间戳记录
import locale  # 获取系统默认编码
import platform  # 用于判断操作系统
import uuid
import queue
import json
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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 任务队列与 SSE 流式架构
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
task_queue = queue.Queue()
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
def worker_loop():
    """后台 Worker 线程：严格串行执行任务"""
    import datetime
    print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] 🔧 Worker 线程已启动，等待任务...')
    while True:
        try:
            task = task_queue.get()
            if task is None:
                print('[Worker] 收到退出信号，线程结束')
                break
            task_id = task['id']
            cmd_str = task['cmd']
            print(f'[{datetime.datetime.now().strftime("%H:%M:%S")}] ⚙️ Worker 取出任务 {task_id[:8]}: {cmd_str}')
            emit_task_event({'id': task_id, 'type': 'status', 'status': 'running'})
            try:
                result = execute_line_streaming(cmd_str, task_id)
            except Exception as e:
                import traceback
                print(f'[Worker] ❌ 执行异常: {e}')
                traceback.print_exc()
                result = f'执行异常：{e}'
            # [修改] 多行回执时，标题行与内容分行显示，避免挤成一坨
            _result_str = str(result)
            _ts = datetime.datetime.now().strftime("%H:%M:%S")
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
            except:
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
        'always_allow': list(permission_mgr._always_allow),
    }
    try:
        with open(CONFIG_FILE, 'w', encoding='utf-8') as f:
            json.dump(config, f, ensure_ascii=False, indent=2)
    except Exception as e:
        print(f'[Agent] 配置保存失败: {e}')


def load_config():
    """启动时从 JSON 文件加载配置，文件不存在或损坏则静默使用默认值"""
    global WORK_DIR, TRASH_DIR, clipboard_mode, exec_enabled, shell_type
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
                if proc_old[j] == proc_file[i+j]:
                    total_sim += 1.0
                else:
                    total_sim += difflib.SequenceMatcher(None, proc_old[j], proc_file[i+j]).ratio()
            avg_sim = total_sim / num_old
            if avg_sim < fuzzy_threshold:
                is_match = False
        # 精确/归一化匹配逻辑
        else:
            for j in range(num_old):
                if proc_old[j] != proc_file[i+j]:
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
    print(f' 指令: {cmd}')
    print(f' 目标: {filepath}')
    print(f' 工作目录: {WORK_DIR}')
    while True:
        ans = input(' 是否允许? [y=允许/n=拒绝/a=本次会话始终允许]: ').strip().lower()
        if ans in ('y', 'yes'):
            return True
        elif ans in ('n', 'no'):
            return False
        elif ans in ('a', 'always'):
            return 'always'
        else:
            print(' 请输入 y, n 或 a')
# 兼容 GUI CLI 模式的壳函数
def execute_line(line):
    return execute_line_streaming(line, 'cli-manual')
def execute_line_streaming(line, task_id):
    """统一执行核心：支持实时推送 exec/run 的日志"""
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    parts = line.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ''
    arg = arg.replace('\u201c', '"').replace('\u201d', '"')
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
                    ['pwsh', '--version'],
                    capture_output=True, text=True, timeout=5
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
            f' 当前工作目录 "work_dir": "{WORK_DIR}",',
            f' 剪贴板读取模式 "clipboard_mode": {str(clipboard_mode).lower()},',
            f' 系统命令执行开关 "exec_enabled": {str(exec_enabled).lower()},',
            f' 终端类型 "shell_type": "{shell_type}",',
            f' 目录权限限制开关 "permission_enabled": {str(permission_mgr.enabled).lower()},',
            f' 始终允许列表条目数 "always_allow_count": {len(permission_mgr._always_allow)},',
            f' 操作系统 "platform": "{platform.system()}"',
            '',
            f'>{_ps_cmd}',
            _ps_ver,
            '>python --version',
            _py_ver,
            '}',
        ]
        return '\n'.join(_lines)
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
                    f' 行数：{len(lines)}\n'
                    f' 字数（中英文混合）：{words}\n'
                    f' 字符数（含空白）：{chars}')
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
                            block_text = '\n'.join(file_lines[i:i+num_search])
                            results.append((start_line_no, block_text))
                if not results:
                    return f'在 {filepath} 中未找到匹配内容'
                output = [f'在 {filepath} 中找到 {len(results)} 处匹配：\n']
                for line_no, line_text in results:
                    if '\n' in line_text:
                        preview = line_text.split('\n')[0]
                        output.append(f' 行 {line_no}: {preview} ... (共 {num_search} 行)')
                    else:
                        output.append(f' 行 {line_no}: {line_text}')
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
                    output.append(f' {fpath}')
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
                            f_proc = re.sub(r'\s+', ' ', file_lines[i+j].strip()).lower()
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
                                diag.append(f' ✓ {repr(o_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
                            else:
                                diag.append(f' ✗ 旧: {repr(o_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
                                diag.append(f' ✗ 文: {repr(f_proc[:120])}{"（仅前120字符）" if len(o_proc) > 120 else ""}')
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
                    return f'错误：行号 {line_no} 超出文件范围 (1-{len(lines)+1})'
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
            log_action('INSERT', f'{filepath} 行 {insert_idx+1}')
            return f'已在 {filepath} 的第 {insert_idx+1} 行处插入内容。'
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
                del lines[start-1:end]
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
        tokens = parse_args_with_quotes(arg)
        if not tokens:
            return '错误：缺少参数。发送 @@help grep 获取指令详细用法'
        opts = [t for t in tokens if t.startswith('-')]
        non_opts = [t for t in tokens if not t.startswith('-')]
        strip_indent = '-s' in opts
        if len(non_opts) < 2:
            return '错误：缺少参数。发送 @@help grep 获取指令详细用法'
        keyword = non_opts[0]
        target_str = non_opts[-1]
        if not target_str:
            return '错误：缺少文件路径。发送 @@help grep 获取指令详细用法'
        kw_list = [k.strip() for k in keyword.split('|') if k.strip()]
        if not kw_list:
            return '错误：关键词为空。'
        cmp_kws = kw_list if len(kw_list) == 1 else kw_list
        target = safe_path(W, target_str)
        err = _check_permission('grep', target)
        if err:
            return err
        try:
            if os.path.isfile(target):
                content, _ = smart_read(target)
                lines = content.splitlines(True)
                results = []
                for idx, line in enumerate(lines, 1):
                    check = line.lstrip() if strip_indent else line
                    matched = any(kw in check for kw in cmp_kws)
                    if matched:
                        hit = [kw for kw in cmp_kws if kw in check]
                        results.append(f' 行 {idx}: {line.rstrip()} ← {hit}')
                if results:
                    output = [f'{target}:']
                    output.extend(results)
                    return '\n'.join(output)
                return f'{target}: 无匹配'
            elif os.path.isdir(target):
                output = []
                for root, dirs, files in os.walk(target):
                    for fname in files:
                        fpath = os.path.join(root, fname)
                        try:
                            content, _ = smart_read(fpath)
                            for idx, line in enumerate(content.splitlines(True), 1):
                                check = line.lstrip() if strip_indent else line
                                matched = any(kw in check for kw in cmp_kws)
                                if matched:
                                    hit = [kw for kw in cmp_kws if kw in check]
                                    output.append(f'{fpath}:{idx}: {line.rstrip()} ← {hit}')
                        except:
                            pass
                if output:
                    return '\n'.join(output)
                return f'在目录 {target} 中未找到匹配。'
            else:
                return f'错误：路径不存在 {target}'
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
                    log_action('READ', f'{filepath} 行 {start_line}-{end_line if end_line>0 else "末尾"}')
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
                log_action('READ', f'{filepath} 行 {start_line}-{end_line if end_line>0 else "末尾"}')
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
                return f'错误：在回收站中未找到对应的记录。'
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
        dirpath = safe_path(W, parts[0]) if parts else W
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
                    lines.append(f' [DIR] {name}')
                else:
                    size = os.path.getsize(full)
                    if size < 1024:
                        lines.append(f' [FILE] {name} ({size} B)')
                    elif size < 1024 * 1024:
                        lines.append(f' [FILE] {name} ({size/1024:.1f} KB)')
                    else:
                        lines.append(f' [FILE] {name} ({size/1024/1024:.1f} MB)')
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
        if not arg.strip():
            return '错误：缺少命令。发送 @@help exec 获取指令详细用法'
        # [新增] 危险命令拦截与弹窗确认
        dangerous_patterns = re.compile(r'\b(del|rd|rm|rmdir|format|erase|diskpart|mkfs)\b', re.IGNORECASE)
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
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=W
                )
            else:
                # cmd 回退
                process = subprocess.Popen(
                    f'cmd /c {arg.strip()}', shell=True,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                    cwd=W
                )
            output_lines = []
            start_time = time.time()
            while True:
                line_bytes = process.stdout.readline()
                if not line_bytes and process.poll() is not None:
                    break
                if line_bytes:
                    line_out = smart_decode(line_bytes).rstrip()
                    output_lines.append(line_out)
                    emit_task_event({'id': task_id, 'type': 'log', 'data': line_out})
                if time.time() - start_time > 3600:
                    if platform.system() == 'Windows':
                        # 杀掉整个进程树 ( /T )，强制 ( /F )
                        subprocess.run(f'taskkill /F /T /PID {process.pid}', shell=True, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
                    else:
                        process.kill()
                        process.wait()
                    return '错误：命令执行超时（3600秒限制），进程树已强杀。'
            process.wait()
            output = '\n'.join(output_lines).strip()
            if not output:
                output = '（命令已执行，无输出）'
            return output
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
                ['python', script], stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                cwd=W
            )
            output_lines = []
            start_time = time.time()
            while True:
                line_bytes = process.stdout.readline()
                if not line_bytes and process.poll() is not None:
                    break
                if line_bytes:
                    line_out = smart_decode(line_bytes).rstrip()
                    output_lines.append(line_out)
                    emit_task_event({'id': task_id, 'type': 'log', 'data': line_out})
                if time.time() - start_time > 60:
                    process.kill()
                    process.wait()
                    return '命令执行超时（限制:60秒）,命令可能仍在运行中,只是60秒内没有执行完成,具体情况请求助管理员。'
            process.wait()
            output = '\n'.join(output_lines).strip()
            if not output:
                output = '（脚本已执行，无输出）'
            return output
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
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动后台 Worker 线程（移到顶层，确保任何启动方式都能跑）
# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
# 启动时加载持久化配置（必须在 permission_mgr 创建之后、worker 启动之前）
load_config()
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
        if cmd in ('create', 'append', 'replace', 'insert', 'find', 'deleteline'):
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
                elif cmd in ('create', 'append', 'insert', 'find'):
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
        print(f'[Download] 文件不存在，当前目录内容: {os.listdir(temp_dir) if os.path.exists(temp_dir) else "目录不存在"}')
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
        return f"下载失败: {e}", 500
@app.route('/agent-config-poll', methods=['GET'])
def agent_config_poll():
    _config_changed.wait(timeout=25)
    _config_changed.clear()
    return jsonify({
        'clipboard_mode': clipboard_mode,
        'permission_enabled': permission_mgr.enabled,
        'exec_enabled': exec_enabled
    })
if __name__ == '__main__':
    permission_mgr.set_callback(_default_permission_callback)
    _push_config()
    print(f'========================================')
    print(f' PokerAgent 本地服务已启动 (SSE流式版)')
    print(f' 监听地址：http://127.0.0.1:9966')
    print(f' 工作目录：{WORK_DIR}')
    print(f' 帮助文档：{HELP_FILE}')
    print(f' 操作日志：{LOG_FILE}')
    print(f'========================================')
    app.run(host='127.0.0.1', port=9966, debug=False, threaded=True)
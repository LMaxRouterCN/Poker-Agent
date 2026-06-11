""" 
PokerAgent - 本地接应服务 
启动方式：python agent_server.py 
默认监听：http://127.0.0.1:9966 
""" 
from flask import Flask, request, jsonify 
from flask_cors import CORS 
import os 
import subprocess 
import urllib.request 
import urllib.error 
import re 
import inspect 
import threading 
import base64 
import difflib # 用于 -s 模式的模糊匹配策略 
import locale # 获取系统默认编码 
import platform # [新增] 用于判断操作系统
app = Flask(__name__) 
CORS(app) 
# 工作目录：脚本所在目录 
WORK_DIR = os.path.dirname(os.path.abspath(__file__)) 
# 帮助文档路径 
HELP_FILE = os.path.join(WORK_DIR, 'commands.md') 
# 操作日志 
LOG_FILE = os.path.join(WORK_DIR, 'agent_log.txt') 
clipboard_mode = False 
exec_enabled = True 
_config_changed = threading.Event()
# [修改] Windows 的 cmd 默认输出是 GBK，Linux/Mac 是 UTF-8
encoding = 'gbk' if platform.system() == 'Windows' else 'utf-8'

_SYS_ENCODING = locale.getpreferredencoding(False) or 'gbk'

def smart_read(filepath):
    """智能读取：优先 UTF-8 (含BOM)，失败回退系统默认编码(如 GBK)，保底 latin-1"""
    # 1. 尝试 UTF-8 (utf-8-sig 会自动处理并剥离 BOM 头)
    try:
        with open(filepath, 'r', encoding='utf-8-sig') as f:
            return f.read(), 'utf-8-sig'
    except UnicodeDecodeError:
        pass
    
    # 2. 尝试系统默认编码 (Windows 下通常是 GBK)
    try:
        with open(filepath, 'r', encoding=_SYS_ENCODING) as f:
            return f.read(), _SYS_ENCODING
    except (UnicodeDecodeError, LookupError):
        pass
        
    # 3. 终极保底：latin-1 (不会抛错，保证程序不崩)
    with open(filepath, 'r', encoding='latin-1') as f:
        return f.read(), 'latin-1'

def smart_write(filepath, content, encoding):
    """智能写入：保持原有编码格式"""
    # 如果是 utf-8-sig，写入时保留 BOM
    with open(filepath, 'w', encoding=encoding) as f:
        f.write(content)

# 👇 新增智能解码函数
def smart_decode(b_str):
    """智能解码：优先UTF-8（兼容dotnet/node等现代工具），失败则回退GBK（兼容传统cmd命令）"""
    if not b_str:
        return ''
    try:
        # 优先尝试 UTF-8
        return b_str.decode('utf-8')
    except UnicodeDecodeError:
        # 如果不是合法的 UTF-8，说明是传统的 GBK 输出，回退到系统默认编码
        return b_str.decode(encoding, errors='replace')

def _push_config(): 
    _config_changed.set() 
def _truncate(s, max_display=20000, keep_len=100): 
    if len(s) > max_display: 
        return s[:keep_len] + f"... (共 {len(s)} 字符)" 
    return s 
def log_action(action, detail=''): 
    import datetime 
    timestamp = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S') 
    detail = _truncate(detail) 
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
    """ 
    解析命令参数，支持双引号包裹含空格的参数。 
    例如：'read "11111.md" 10-20' -> ['11111.md', '10-20'] 
    """ 
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
                return True 
            return bool(result) 
        return False 
    def reset_session(self): 
        with self._lock: 
            self._always_allow.clear() 
permission_mgr = PermissionManager() 
def _check_permission(cmd, *paths): 
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
def execute_line(line): 
    line = line.strip() 
    if not line or line.startswith('#'): 
        return None 
    parts = line.split(None, 1) 
    cmd = parts[0].lower() 
    arg = parts[1] if len(parts) > 1 else '' 
    # 将中文引号替换为英文引号，确保带空格的路径能被正确解析 
    arg = arg.replace('\u201c', '"').replace('\u201d', '"') 
    # 【CodeSTART】截断：防止LLM未换行导致arg混入标签 
    cs_idx = arg.find('【CodeSTART】') 
    if cs_idx != -1: 
        arg = arg[:cs_idx] 
    W = WORK_DIR 
    # ========== 系统指令 ========== 
    if cmd == '@@help': 
        if os.path.exists(HELP_FILE): 
            with open(HELP_FILE, 'r', encoding='utf-8') as f: 
                return f.read() 
        return 'commands.md 文件未找到，请寻找管理员确认它与此脚本在同一目录下。' 
    # ========== 精确内容操作 ========== 
    elif cmd == 'count': 
        if not arg.strip(): 
            return '错误：缺少文件路径。用法：count <路径>' 
        p_args = parse_args_with_quotes(arg.strip()) 
        if not p_args: 
            return '错误：缺少文件路径。用法：count <路径>' 
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
        if '\x00' in arg: 
            sep = arg.split('\x00', 1) 
            opts_str = sep[0].strip() 
            search_text = sep[1] 
        else: 
            all_tokens = parse_args_with_quotes(arg) 
            if len(all_tokens) < 2: 
                return '错误：缺少查找内容。用法：find <路径> [选项] 换行查找内容' 
            j = 1 
            while j < len(all_tokens) and all_tokens[j] in ('-i', '-w'): 
                j += 1 
            opts_str = ' '.join(all_tokens[:j]) 
            search_text = ' '.join(all_tokens[j:]) 
        tokens = parse_args_with_quotes(opts_str) 
        if not tokens: 
            return '错误：缺少文件路径。' 
        filepath = safe_path(W, tokens[0]) 
        flags = tokens[1:] if len(tokens) > 1 else [] 
        ignore_case = '-i' in flags 
        whole_word = '-w' in flags 
        err = _check_permission('find', filepath) 
        if err: 
            return err 
        try: 
            content, _ = smart_read(filepath)
            lines = content.splitlines(True) # 保持换行符
            results = [] 
            is_multi = '\n' in search_text 
            if is_multi: 
                search_comp = search_text.lower() if ignore_case else search_text 
                full_text = ''.join(lines) 
                full_comp = full_text.lower() if ignore_case else full_text 
                start_idx = 0 
                while True: 
                    pos = full_comp.find(search_comp, start_idx) 
                    if pos == -1: 
                        break 
                    line_no = full_text[:pos].count('\n') + 1 
                    context_start = max(0, full_text.rfind('\n', 0, pos) + 1) 
                    context_end = full_text.find('\n', pos + len(search_text)) 
                    if context_end == -1: 
                        context_end = len(full_text) 
                    context = full_text[context_start:context_end].rstrip() 
                    results.append((line_no, context)) 
                    start_idx = pos + len(search_comp) 
            else: 
                search_comp = search_text.strip().lower() if ignore_case else search_text.strip() 
                for idx, line in enumerate(lines, 1): 
                    line_comp = line.lower() if ignore_case else line 
                    if whole_word: 
                        pattern = r'\b' + re.escape(search_comp) + r'\b' 
                        if re.search(pattern, line_comp): 
                            results.append((idx, line.rstrip())) 
                    else: 
                        if search_comp in line_comp: 
                            results.append((idx, line.rstrip())) 
            if not results: 
                opt_desc = [] 
                if ignore_case: 
                    opt_desc.append('忽略大小写') 
                if whole_word: 
                    opt_desc.append('全词匹配') 
                opt_str = f' ({", ".join(opt_desc)})' if opt_desc else '' 
                preview = search_text[:50] + '...' if len(search_text) > 50 else search_text 
                return f'在 {filepath} 中未找到 "{preview}"{opt_str}' 
            output = [f'在 {filepath} 中找到 {len(results)} 处匹配：\n'] 
            for line_no, line_text in results: 
                output.append(f' 行 {line_no}: {line_text}') 
            log_action('FIND', f'{filepath} -> {len(results)} 处') 
            return '\n'.join(output) 
        except Exception as e: 
            return f'查找失败：{e}' 
    elif cmd == 'replace': 
        parts = arg.split('\x00') 
        if not parts: 
            return '错误：缺少参数。用法：replace <路径> [选项]' 
        opts_str = parts[0].strip() 
        tokens = parse_args_with_quotes(opts_str) 
        if not tokens: 
            return '错误：缺少文件路径。' 
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
                return '错误：行号模式需要提供新文本。用法：replace <路径> -l <行号范围>' 
            new_text = parts[1].strip().replace('TICK3', '```') 
            old_text = '' 
        else: 
            if len(parts) < 3: 
                return '错误：缺少参数。用法：replace <路径> [选项]' 
            old_text = parts[1].strip().replace('TICK3', '```') 
            new_text = parts[2].strip().replace('TICK3', '```') 
        ignore_case = '-i' in flags 
        replace_all = '-a' in flags 
        strip_indent = '-s' in flags 
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
            elif strip_indent: 
                old_lines = old_text.split('\n') 
                file_lines = content.split('\n') 
                matches = [] 
                def _norm(s, aggressive=False): 
                    s = s.strip() 
                    if aggressive: 
                        s = re.sub(r'\s+', ' ', s) 
                    if ignore_case: 
                        s = s.lower() 
                    return s 
                for i in range(len(file_lines) - len(old_lines) + 1): 
                    ok = True 
                    for j in range(len(old_lines)): 
                        if _norm(file_lines[i + j]) != _norm(old_lines[j]): 
                            ok = False 
                            break 
                    if ok: 
                        matches.append(i) 
                        if not replace_all: 
                            break 
                if not matches: 
                    for i in range(len(file_lines) - len(old_lines) + 1): 
                        ok = True 
                        for j in range(len(old_lines)): 
                            if _norm(file_lines[i + j], True) != _norm(old_lines[j], True): 
                                ok = False 
                                break 
                        if ok: 
                            matches.append(i) 
                            if not replace_all: 
                                break 
                if not matches: 
                    _FUZZY_THRESHOLD = 0.92 
                    old_cmp = [_norm(l, True) for l in old_lines] 
                    candidates = [] 
                    for i in range(len(file_lines) - len(old_lines) + 1): 
                        total = 0.0 
                        for j in range(len(old_lines)): 
                            fl = _norm(file_lines[i + j], True) 
                            total += difflib.SequenceMatcher(None, old_cmp[j], fl).ratio() 
                        avg = total / len(old_lines) 
                        if avg >= _FUZZY_THRESHOLD: 
                            candidates.append((i, avg)) 
                    if candidates: 
                        if not replace_all: 
                            candidates.sort(key=lambda x: x[1], reverse=True) 
                            matches.append(candidates[0][0]) 
                        else: 
                            candidates.sort(key=lambda x: x[0]) 
                            matches.extend(c[0] for c in candidates) 
                if not matches: 
                    old_diag = [_norm(l, True) for l in old_lines] 
                    best_pos = -1 
                    best_cnt = 0 
                    for i in range(len(file_lines) - len(old_lines) + 1): 
                        cnt = sum(1 for j in range(len(old_lines)) if _norm(file_lines[i + j], True) == old_diag[j]) 
                        if cnt > best_cnt: 
                            best_cnt = cnt 
                            best_pos = i 
                    diag = [] 
                    if best_pos >= 0 and best_cnt > 0: 
                        diag.append(f'最接近的匹配：第 {best_pos + 1} 行起，{best_cnt}/{len(old_lines)} 行精确匹配（空白归一化后）') 
                        for j in range(len(old_lines)): 
                            ol = old_diag[j] 
                            fl = _norm(file_lines[best_pos + j], True) 
                            if ol == fl: 
                                diag.append(f' \u2713 {repr(fl[:120])}') 
                            else: 
                                diag.append(f' \u2717 旧文本: {repr(ol[:120])}') 
                                diag.append(f' \u2717 文件: {repr(fl[:120])}') 
                        total_fuzz = sum( 
                            difflib.SequenceMatcher(None, ol, fl).ratio() 
                            for ol, fl in zip(old_diag, [_norm(file_lines[best_pos + j], True) for j in range(len(old_lines))]) 
                        ) 
                        diag.append(f' 模糊相似度: {total_fuzz / len(old_lines):.2%}') 
                    else: 
                        diag.append('未找到任何部分匹配。') 
                    return ( 
                        '未找到要替换的文本（忽略缩进模式，已依次尝试精确匹配、空白归一化匹配、模糊匹配三种策略）。\n' 
                        + '\n'.join(diag) 
                    ) 
                for idx in reversed(matches): 
                    indent = re.match(r'^(\s*)', file_lines[idx]).group(1) 
                    new_lines = new_text.split('\n') 
                    if indent: 
                        new_lines = [indent + l if l.strip() else l for l in new_lines] 
                    file_lines[idx:idx + len(old_lines)] = new_lines 
                    count += 1 
                new_content = '\n'.join(file_lines) 
            elif replace_all: 
                if ignore_case: 
                    pattern = re.compile(re.escape(old_text), re.IGNORECASE) 
                    new_content, count = pattern.subn(new_text, content) 
                else: 
                    new_content = content.replace(old_text, new_text) 
                    count = content.count(old_text) 
            else: 
                if ignore_case: 
                    pattern = re.compile(re.escape(old_text), re.IGNORECASE) 
                    new_content = pattern.sub(new_text, content, count=1) 
                    count = 1 if new_content != content else 0 
                else: 
                    new_content = content.replace(old_text, new_text, 1) 
                    count = 1 if new_content != content else 0 
            if count == 0: 
                return '未找到要替换的文本。' 
            smart_write(filepath, new_content, file_enc)
            log_action('REPLACE', f'{filepath} ({count} 处)') 
            return f'已替换 {filepath} 中的 {count} 处文本。' 
        except Exception as e: 
            return f'替换失败：{e}' 
    elif cmd == 'insert': 
        if '\x00' not in arg: 
            return '错误：缺少参数。用法：insert <路径> -after <行号或文本> 换行插入内容' 
        sep = arg.split('\x00', 1) 
        opts_str = sep[0].strip() 
        insert_text = sep[1] 
        tokens = parse_args_with_quotes(opts_str) 
        if not tokens: 
            return '错误：缺少文件路径。' 
        filepath = safe_path(W, tokens[0]) 
        opts = ' '.join(tokens[1:]) if len(tokens) > 1 else '' 
        m = re.match(r'-(after|before)\s+["\']?(.+?)["\']?\s*$', opts) 
        if not m: 
            return '错误：选项格式不正确。示例：-after 10 或 -before "目标文本"' 
        pos_type = m.group(1) 
        pos_val = m.group(2) 
        err = _check_permission('insert', filepath) 
        if err: 
            return err 
        try:
            content, file_enc = smart_read(filepath)
            lines = content.splitlines(True)  # 👈 补上这行，将字符串转为行列表
            insert_idx = -1
            if pos_val.isdigit():
                line_no = int(pos_val)
                if line_no < 1 or line_no > len(lines) + 1:
                    return f'错误：行号 {line_no} 超出文件范围 (1-{len(lines)+1})'
                if pos_type == 'after': insert_idx = line_no
                else: insert_idx = line_no - 1
            else:
                found_idx = -1
                for idx, line in enumerate(lines):
                    if pos_val in line:
                        found_idx = idx
                        break
                if found_idx == -1:
                    return f'未找到定位文本：{pos_val}'
                if pos_type == 'after': insert_idx = found_idx + 1
                else: insert_idx = found_idx
                
            insert_text = insert_text.replace('TICK3', '`')
            if not insert_text.endswith('\n'):
                insert_text += '\n'
                
            lines.insert(insert_idx, insert_text)
            new_content = ''.join(lines)  # 👈 补上这行，重新拼成字符串
            smart_write(filepath, new_content, file_enc)
            log_action('INSERT', f'{filepath} 行 {insert_idx+1}')
            return f'已在 {filepath} 的第 {insert_idx+1} 行处插入内容。'
        except Exception as e:
            return f'插入失败：{e}'
    elif cmd == 'deleteline': 
        if not arg.strip(): 
            return '错误：缺少参数。用法：deleteline <路径> -l <行号或范围> 或 deleteline <路径> [选项] <要删除的文本>' 
        parts = parse_args_with_quotes(arg) 
        filepath = safe_path(W, parts[0]) 
        err = _check_permission('deleteline', filepath) 
        if err: 
            return err 
        if '-l' in parts: 
            l_index = parts.index('-l') 
            if l_index + 1 >= len(parts): 
                return '错误：-l 选项后需要指定行号或范围' 
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
                lines = content.splitlines(True)  # 👈 补上这行
                if start < 1 or end > len(lines):
                    return f'错误：行号范围 {start}-{end} 超出文件范围 (1-{len(lines)})'
                del lines[start-1:end]
                new_content = ''.join(lines)      # 👈 补上这行
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
                return '错误：缺少要删除的文本' 
            try: 
                content, file_enc = smart_read(filepath)
                if ignore_case: 
                    flags_re = re.IGNORECASE 
                else: 
                    flags_re = 0 
                if whole_word: 
                    pattern = r'\b' + re.escape(delete_text) + r'\b' 
                else: 
                    pattern = re.escape(delete_text) 
                regex = re.compile(pattern, flags_re) 
                matches = list(regex.finditer(content)) 
                if not matches: 
                    return f'未找到要删除的文本：{delete_text[:50]}' 
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
            return '错误：缺少参数。用法：grep [-s] <关键词1|关键词2> <路径或文件>' 
        opts = [t for t in tokens if t.startswith('-')] 
        non_opts = [t for t in tokens if not t.startswith('-')] 
        strip_indent = '-s' in opts 
        if len(non_opts) < 2: 
            return '错误：缺少参数。用法：grep [-s] <关键词1|关键词2> <路径或文件>' 
        keyword = non_opts[0] 
        target_str = non_opts[-1] 
        if not target_str: 
            return '错误：缺少文件路径。' 
        kw_list = [k.strip() for k in keyword.split('|') if k.strip()] 
        if not kw_list: 
            return '错误：关键词为空。' 
        if len(kw_list) == 1: 
            cmp_kws = kw_list 
        else: 
            cmp_kws = kw_list 
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
            return '错误：缺少文件路径。用法：head <路径> [行数]' 
        filepath = safe_path(W, parts[0]) 
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10 
        err = _check_permission('head', filepath) 
        if err: 
            return err 
        try:
            content, _ = smart_read(filepath)
            lines = content.splitlines(True)
            head_lines = [l.rstrip() for l in lines[:n]]  # 👈 限制只取前 n 行
            log_action('HEAD', filepath)
            return '\n'.join(head_lines) if head_lines else '（文件为空）'
        except Exception as e:
            return f'读取失败：{e}'
    elif cmd == 'tail': 
        parts = parse_args_with_quotes(arg) 
        if not parts: 
            return '错误：缺少文件路径。用法：tail <路径> [行数]' 
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
    # ========== 文件操作 ========== 
    elif cmd == 'create': 
        if not arg: 
            return '错误：缺少文件路径。用法：create <路径>' 
        if '\x00' in arg: 
            sep = arg.split('\x00', 1) 
            filepath = safe_path(W, sep[0].strip()) 
            content = sep[1] 
        else: 
            s = arg.strip() 
            if s.startswith('"'): 
                end_quote = s.find('"', 1) 
                if end_quote == -1: 
                    filepath_str = s[1:] 
                    rest = '' 
                else: 
                    filepath_str = s[1:end_quote] 
                    rest = s[end_quote+1:].strip() 
            else: 
                parts = s.split(None, 1) 
                filepath_str = parts[0] 
                rest = parts[1] if len(parts) > 1 else '' 
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
            return '错误：缺少文件路径。用法：read <路径> [起始行]-[结束行]' 
        parts = parse_args_with_quotes(arg.strip()) 
        if not parts: 
            return '错误：缺少文件路径。用法：read <路径> [起始行]-[结束行]' 
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
                return '错误：行号格式不正确。用法：read <路径> [起始行]-[结束行]' 
        err = _check_permission('read', filepath) 
        if err: 
            return err 
        if start_line == 0 and clipboard_mode and os.path.isfile(filepath): 
            try: 
                filename = os.path.basename(filepath) 
                with open(filepath, 'rb') as f: 
                    b64 = base64.b64encode(f.read()).decode('ascii') 
                file_size = os.path.getsize(filepath) 
                return f'__CLIPBOARD_FILE__{filename}\x00{file_size}\x00{b64}' 
            except Exception as e: 
                return f'读取失败：{e}' 
        try:
            content, _ = smart_read(filepath)  # 👈 替换原来的 open
            lines = content.splitlines(True)   # 👈 转为列表以兼容后续代码
            
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
                if len(content_str) > 5000:
                    return f'{content_str[:5000]}\n\n...（文件过长，仅显示前 5000 字符，共 {len(content_str)} 字符）'
                return content_str if content_str else '（文件为空）'
        except FileNotFoundError:
            return f'错误：文件不存在：{filepath}'
        except Exception as e:
            return f'读取失败：{e}'
    elif cmd == 'append': 
        if not arg: 
            return '错误：缺少文件路径。用法：append <路径>' 
        if '\x00' in arg: 
            sep = arg.split('\x00', 1) 
            filepath = safe_path(W, sep[0].strip()) 
            content = sep[1] 
        else: 
            s = arg.strip() 
            if s.startswith('"'): 
                end_quote = s.find('"', 1) 
                if end_quote == -1: 
                    filepath_str = s[1:] 
                    rest = '' 
                else: 
                    filepath_str = s[1:end_quote] 
                    rest = s[end_quote+1:].strip() 
            else: 
                parts = s.split(None, 1) 
                filepath_str = parts[0] 
                rest = parts[1] if len(parts) > 1 else '' 
            filepath = safe_path(W, filepath_str) 
            content = rest 
        content = content.replace('TICK3', '```') 
        err = _check_permission('append', filepath) 
        if err: 
            return err 
        try:
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
            # 👇 智能获取原文件编码，如果是新文件则默认 utf-8
            file_enc = 'utf-8'
            if os.path.exists(filepath):
                _, file_enc = smart_read(filepath)
                
            with open(filepath, 'a', encoding=file_enc) as f: 
                f.write('\n' + content)
            log_action('APPEND', filepath)
            return f'已追加到文件：{filepath}'
        except Exception as e:
            return f'追加失败：{e}'
    elif cmd == 'delete': 
        if not arg.strip(): 
            return '错误：缺少文件路径。用法：delete <路径>' 
        parts = parse_args_with_quotes(arg.strip()) 
        if not parts: 
            return '错误：缺少文件路径。用法：delete <路径>' 
        filepath = safe_path(W, parts[0]) 
        err = _check_permission('delete', filepath) 
        if err: 
            return err 
        try: 
            if os.path.isfile(filepath): 
                os.remove(filepath) 
                log_action('DELETE', filepath) 
                return f'已删除文件：{filepath}' 
            elif os.path.isdir(filepath): 
                return f'错误：{filepath} 是一个目录，请使用 exec rd /s /q "{filepath}" 手动删除。' 
            else: 
                return f'错误：文件不存在：{filepath}' 
        except Exception as e: 
            return f'删除失败：{e}' 
    elif cmd == 'copy': 
        if not arg: 
            return '错误：缺少参数。用法：copy <源路径> <目标路径>' 
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
            import shutil 
            shutil.copy2(src, dst) 
            log_action('COPY', f'{src} -> {dst}') 
            return f'已复制：{src} -> {dst}' 
        except Exception as e: 
            return f'复制失败：{e}' 
    elif cmd == 'move': 
        if not arg: 
            return '错误：缺少参数。用法：move <源路径> <目标路径>' 
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
            import shutil 
            shutil.move(src, dst) 
            log_action('MOVE', f'{src} -> {dst}') 
            return f'已移动：{src} -> {dst}' 
        except Exception as e: 
            return f'移动失败：{e}' 
    # ========== 目录操作 ========== 
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
            return '错误：缺少目录路径。用法：mkdir <路径>' 
        parts = parse_args_with_quotes(arg.strip()) 
        if not parts: 
            return '错误：缺少目录路径。用法：mkdir <路径>' 
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
    # ========== 系统命令 ========== 
    elif cmd == 'exec': 
        if not exec_enabled: 
            return '错误：exec 指令已被管理员禁用。' 
        if not arg.strip(): 
            return '错误：缺少命令。用法：exec <系统命令>' 
        log_action('EXEC', arg.strip()) 
        try: 
            result = subprocess.run( 
                f'cmd /c {arg.strip()}', shell=True, capture_output=True, timeout=60, cwd=W 
            ) 
            out = smart_decode(result.stdout)
            err = smart_decode(result.stderr)
            output = (out + err).strip() 
            if not output: 
                output = '（命令已执行，无输出）' 
            if len(output) > 8000: 
                output = output[:8000] + f'\n\n...（输出过长，仅显示前 8000 字符,若需要全部输出请求助管理员）' 
            return output 
        except subprocess.TimeoutExpired: 
            return '错误：命令执行超时（60秒限制）。' 
        except Exception as e: 
            return f'执行失败：{e}' 
    elif cmd == 'run': 
        if not arg.strip(): 
            return '错误：缺少脚本路径。用法：run <脚本路径>' 
        parts = parse_args_with_quotes(arg.strip()) 
        if not parts: 
            return '错误：缺少脚本路径。用法：run <脚本路径>' 
        script = safe_path(W, parts[0]) 
        err = _check_permission('run', script) 
        if err: 
            return err 
        if not os.path.exists(script): 
            return f'错误：脚本不存在：{script}' 
        log_action('RUN', script) 
        try: 
            result = subprocess.run( 
                ['python', script], capture_output=True, timeout=60, cwd=W 
            ) 
            out = smart_decode(result.stdout)
            err = smart_decode(result.stderr)
            output = (out + err).strip() 
            if not output: 
                output = '（脚本已执行，无输出）' 
            if len(output) > 8000: 
                output = output[:8000] + '\n\n...（输出过长，仅显示前 8000 字符）' 
            return output 
        except subprocess.TimeoutExpired: 
            return '命令执行超时（限制:60秒）,命令可能仍在运行中,只是60秒内没有执行完成,具体情况请求助管理员。' 
        except Exception as e: 
            return f'运行失败：{e}' 
    # ========== 网络操作 ========== 
    elif cmd == 'get':
        if not arg.strip():
            return '错误：缺少 URL。用法：get <URL>'
        url = arg.strip()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Agent/1.0 (PokerAgent)'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                raw_bytes = resp.read()
                
                # 1. 尝试从 HTTP Header 提取 charset
                content_type = resp.headers.get('Content-Type', '')
                charset = 'utf-8'
                m = re.search(r'charset=([a-zA-Z0-9\-]+)', content_type, re.I)
                if m:
                    charset = m.group(1)
                
                # 2. 智能解码
                try:
                    body = raw_bytes.decode(charset)
                except (UnicodeDecodeError, LookupError):
                    try:
                        body = raw_bytes.decode('utf-8')
                    except UnicodeDecodeError:
                        body = raw_bytes.decode('gbk', errors='replace')
                        
                if len(body) > 8000:
                    body = body[:8000] + '\n\n...（内容过长，仅显示前 8000 字符）'
                log_action('GET', url)
                return body
        except urllib.error.HTTPError as e:
            return f'HTTP 错误：{e.code} {e.reason}'
        except Exception as e:
            return f'请求失败：{e}'
    elif cmd == 'download': 
        if not arg: 
            return '错误：缺少参数。用法：download <URL> <保存路径>' 
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
        return f'未知指令：{cmd}\n输入 @@help 查看可用指令列表。' 
_EXEC_SRC = inspect.getsource(execute_line) 
KNOWN_CMDS = set(re.findall(r"cmd\s*==\s*'([^']+)'", _EXEC_SRC)) 
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
    command_text = command_text.replace('\r\n', '\n').replace('\r', '\n') 
    log_action('RECEIVED', command_text[:20000]) 
    lines = command_text.split('\n') 
    i = 0 
    results = [] 
    while i < len(lines): 
        line = lines[i].strip() 
        if not line or line.startswith('#'): 
            i += 1 
            continue 
        parts = line.split(None, 1) 
        cmd = parts[0].lower() 
        arg = parts[1] if len(parts) > 1 else '' 
        if cmd == 'replace': 
            peek = i + 1 
            blocks = [] 
            if '【codestart】' in lines[i].lower(): 
                if peek < len(lines) and lines[peek].strip().startswith('```'): 
                    peek += 1 
                block = [] 
                while peek < len(lines): 
                    bln = lines[peek] 
                    if bln.strip().startswith('```') or '【/codeend】' in bln.lower(): 
                        if '【/codeend】' in bln.lower(): 
                            idx2 = bln.lower().find('【/codeend】') 
                            if idx2 != -1: 
                                block.append(bln[:idx2]) 
                                peek += 1 
                                break 
                        peek += 1 
                        break 
                    block.append(bln) 
                    peek += 1 
                blocks.append('\n'.join(block)) 
            while peek < len(lines) and len(blocks) < 2: 
                ln = lines[peek] 
                stripped = ln.strip() 
                if stripped.lower() == '【codestart】': 
                    peek += 1 
                    if peek < len(lines) and lines[peek].strip().startswith('```'): 
                        peek += 1 
                    block = [] 
                    while peek < len(lines): 
                        bln = lines[peek] 
                        if bln.strip().startswith('```') or '【/codeend】' in bln.lower(): 
                            if '【/codeend】' in bln.lower(): 
                                idx = bln.lower().find('【/codeend】') 
                                if idx != -1: 
                                    block.append(bln[:idx]) 
                                    peek += 1 
                                    break 
                            peek += 1 
                            break 
                        block.append(bln) 
                        peek += 1 
                    blocks.append('\n'.join(block)) 
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
                    blocks.append('\n'.join(block)) 
                else: 
                    peek += 1 
            if len(blocks) == 2: 
                final_cmd = f"replace {arg}\x00{blocks[0].strip(chr(10))}\x00{blocks[1].strip(chr(10))}" 
                print(f"[DEBUG] final_cmd = {repr(final_cmd)}") 
                result = execute_line(final_cmd) 
                if result is not None: 
                    results.append(result) 
                i = peek 
                continue 
            elif len(blocks) == 1 and '-l' in arg: 
                final_cmd = f"replace {arg}\x00{blocks[0].strip(chr(10))}" 
                print(f"[DEBUG] final_cmd = {repr(final_cmd)}") 
                result = execute_line(final_cmd) 
                if result is not None: 
                    results.append(result) 
                i = peek 
                continue 
        if cmd in ('create', 'append', 'replace', 'insert', 'find', 'deleteline'): 
            if cmd == 'deleteline' and '-l' in arg: 
                result = execute_line(line) 
                if result is not None: 
                    results.append(result) 
                i += 1 
                continue 
            else: 
                peek = i + 1 
                content_lines = [] 
                has_code_start = False 
                if '【codestart】' in lines[i].lower(): 
                    has_code_start = True 
                    clean_line = lines[i].split('【CodeSTART】', 1)[0].strip() 
                    parts = clean_line.split(None, 1) 
                    cmd = parts[0].lower() 
                    arg = parts[1] if len(parts) > 1 else '' 
                elif peek < len(lines) and lines[peek].strip().lower() == '【codestart】': 
                    has_code_start = True 
                    peek += 1 
                if has_code_start: 
                    if peek < len(lines) and lines[peek].strip().startswith('```'): 
                        peek += 1 
                    while peek < len(lines): 
                        ln = lines[peek] 
                        if '【/codeend】' in ln.lower(): 
                            idx = ln.lower().find('【/codeend】') 
                            if idx != -1: 
                                content_lines.append(ln[:idx]) 
                                peek += 1 
                                break 
                        if ln.strip().startswith('```'): 
                            peek += 1 
                            break 
                        content_lines.append(ln) 
                        peek += 1 
                    i = peek 
                elif peek < len(lines) and lines[peek].strip().startswith('```'): 
                    peek += 1 
                    while peek < len(lines) and not lines[peek].strip().startswith('```'): 
                        content_lines.append(lines[peek]) 
                        peek += 1 
                    i = peek 
                else: 
                    while peek < len(lines): 
                        ln = lines[peek].strip() 
                        if ln and ln.split(None, 1)[0].lower() in KNOWN_CMDS: 
                            break 
                        content_lines.append(lines[peek]) 
                        peek += 1 
                    i = peek 
                while content_lines and not content_lines[0].strip(): 
                    content_lines.pop(0) 
                while content_lines and not content_lines[-1].strip(): 
                    content_lines.pop() 
                if content_lines: 
                    content = '\n'.join(content_lines) 
                    final_cmd = f"{cmd} {arg}\x00{content}" 
                    result = execute_line(final_cmd) 
                else: 
                    result = execute_line(line) 
                if result is not None: 
                    results.append(result) 
                continue 
        else: 
            result = execute_line(line) 
            if result is not None: 
                results.append(result) 
        i += 1 
    # 分离文本结果和剪贴板文件结果 
    text_results = [] 
    file_results = [] 
    for r in results: 
        if isinstance(r, str) and r.startswith('__CLIPBOARD_FILE__'): 
            m = re.match(r'__CLIPBOARD_FILE__(.+?)\x00(\d+)\x00([\s\S]+)', r) 
            if m: 
                log_action('READ-CLIPBOARD', m.group(1)) 
                file_results.append({ 
                    'filename': m.group(1), 
                    'size': int(m.group(2)), 
                    'data': m.group(3).strip() 
                }) 
        elif r is not None: 
            text_results.append(r) 
    if file_results: 
        # 【修改】构建有序的回执列表，保证文本回执和文件的原始顺序 
        ordered_results = [] 
        text_idx = 0 
        file_idx = 0 
        
        for r in results: 
            if isinstance(r, str) and r.startswith('__CLIPBOARD_FILE__'): 
                if file_idx < len(file_results): 
                    ordered_results.append({ 
                        'type': 'file', 
                        'data': file_results[file_idx] 
                    }) 
                    file_idx += 1 
            elif r is not None: 
                ordered_results.append({ 
                    'type': 'text', 
                    'data': r 
                }) 
                
        log_action('RESULT', f'ordered: {len(ordered_results)} items') 
        return jsonify({ 
            'type': 'clipboard_file_ordered', 
            'results': ordered_results 
        }) 
    if not results: 
        output = '（无可执行的指令）' 
    elif len(results) == 1: 
        output = results[0] 
    else: 
        output = '\n---\n'.join(f'[指令 {idx+1}] {r}' for idx, r in enumerate(results)) 
    log_action('RESULT', output[:20000]) 
    return output 
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
    print(f' 低配版Agent 本地服务已启动') 
    print(f' 监听地址：http://127.0.0.1:9966') 
    print(f' 工作目录：{WORK_DIR}') 
    print(f' 帮助文档：{HELP_FILE}') 
    print(f' 操作日志：{LOG_FILE}') 
    print(f'========================================') 
    app.run(host='127.0.0.1', port=9966, debug=False)
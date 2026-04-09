"""低配版Agent - 本地接应服务
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


app = Flask(__name__)
CORS(app)

# 工作目录：脚本所在目录
WORK_DIR = os.path.dirname(os.path.abspath(__file__))
# 帮助文档路径
HELP_FILE = os.path.join(WORK_DIR, 'commands.md')
# 操作日志
LOG_FILE = os.path.join(WORK_DIR, 'agent_log.txt')
clipboard_mode = False
_config_changed = threading.Event()

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

# ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
#  路径权限管理器
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

def execute_line(line):
    line = line.strip()
    if not line or line.startswith('#'):
        return None
    parts = line.split(None, 1)
    cmd = parts[0].lower()
    arg = parts[1] if len(parts) > 1 else ''
    W = WORK_DIR

    # ========== 系统指令 ==========
    if cmd == '@@help':
        if os.path.exists(HELP_FILE):
            with open(HELP_FILE, 'r', encoding='utf-8') as f:
                return f.read()
        return 'commands.md 文件未找到，请确认它与此脚本在同一目录下。'

    # ========== 精确内容操作 ==========
    elif cmd == 'count':
        if not arg.strip():
            return '错误：缺少文件路径。用法：count <路径>'
        filepath = safe_path(W, arg.strip())
        err = _check_permission('count', filepath)
        if err: return err
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
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
        if '\x00' in arg:
            sep = arg.split('\x00', 1)
            opts_str = sep[0].strip()
            search_text = sep[1]
        else:
            all_tokens = arg.split()
            if len(all_tokens) < 2:
                return '错误：缺少查找内容。用法：find <路径> [选项] 换行查找内容'
            j = 1
            while j < len(all_tokens) and all_tokens[j] in ('-i', '-w'):
                j += 1
            opts_str = ' '.join(all_tokens[:j])
            search_text = ' '.join(all_tokens[j:])
            
        tokens = opts_str.split()
        if not tokens:
            return '错误：缺少文件路径。'
        filepath = safe_path(W, tokens[0])
        flags = tokens[1:] if len(tokens) > 1 else []
        
        ignore_case = '-i' in flags
        whole_word = '-w' in flags
        
        err = _check_permission('find', filepath)
        if err: return err
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            results = []
            is_multi = '\n' in search_text
            if is_multi:
                search_comp = search_text.lower() if ignore_case else search_text
                full_text = ''.join(lines)
                full_comp = full_text.lower() if ignore_case else full_text
                
                start_idx = 0
                while True:
                    pos = full_comp.find(search_comp, start_idx)
                    if pos == -1: break
                    line_no = full_text[:pos].count('\n') + 1
                    context_start = max(0, full_text.rfind('\n', 0, pos) + 1)
                    context_end = full_text.find('\n', pos + len(search_text))
                    if context_end == -1: context_end = len(full_text)
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
                if ignore_case: opt_desc.append('忽略大小写')
                if whole_word: opt_desc.append('全词匹配')
                opt_str = f' ({", ".join(opt_desc)})' if opt_desc else ''
                preview = search_text[:50] + '...' if len(search_text)>50 else search_text
                return f'在 {filepath} 中未找到 "{preview}"{opt_str}'
            
            output = [f'在 {filepath} 中找到 {len(results)} 处匹配：\n']
            for line_no, line_text in results:
                output.append(f'  行 {line_no}: {line_text}')
            
            log_action('FIND', f'{filepath} -> {len(results)} 处')
            return '\n'.join(output)
        except Exception as e:
            return f'查找失败：{e}'

    elif cmd == 'replace':
        parts = arg.split('\x00')
        if len(parts) >= 3:
            opts_str = parts[0].strip()
            old_text = parts[1].strip()   # 原来是 .strip('\n')
            new_text = parts[2].strip()   # 原来是 .strip('\n')
        else:
            return '错误：缺少参数。用法：replace <路径> [选项]'
        old_text = old_text.replace('TICK3', '```')
        new_text = new_text.replace('TICK3', '```')
        tokens = opts_str.split()
        if not tokens:
            return '错误：缺少文件路径。'
        filepath = safe_path(W, tokens[0])
        flags = tokens[1:] if len(tokens) > 1 else []
        ignore_case = '-i' in flags
        replace_all = '-a' in flags
        strip_indent = '-s' in flags
        
        err = _check_permission('replace', filepath)
        if err: return err
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                content = f.read()
            count = 0
            if strip_indent:
                old_lines = old_text.split('\n')
                file_lines = content.split('\n')
                matches = []
                for i in range(len(file_lines) - len(old_lines) + 1):
                    matched = True
                    for j in range(len(old_lines)):
                        fl = file_lines[i + j].strip()
                        ol = old_lines[j].strip()
                        if ignore_case:
                            fl = fl.lower()
                            ol = ol.lower()
                        if fl != ol:
                            matched = False
                            break
                    if matched:
                        matches.append(i)
                        if not replace_all:
                            break
                if not matches:
                    return '未找到要替换的文本（忽略缩进模式）。'
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
            with open(filepath, 'w', encoding='utf-8') as f:
                f.write(new_content)
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
        
        tokens = opts_str.split(None, 1)
        if not tokens:
            return '错误：缺少文件路径。'
        filepath = safe_path(W, tokens[0])
        opts = tokens[1] if len(tokens) > 1 else ''
        
        m = re.match(r'-(after|before)\s+["\']?(.+?)["\']?\s*$', opts)
        if not m:
            return '错误：选项格式不正确。示例：-after 10 或 -before "目标文本"'
        pos_type = m.group(1)
        pos_val = m.group(2)
        
        err = _check_permission('insert', filepath)
        if err: return err
        
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
            
            insert_idx = -1
            if pos_val.isdigit():
                line_no = int(pos_val)
                if line_no < 1 or line_no > len(lines) + 1:
                    return f'错误：行号 {line_no} 超出文件范围 (1-{len(lines)+1})'
                if pos_type == 'after':
                    insert_idx = line_no
                else:
                    insert_idx = line_no - 1
            else:
                found_idx = -1
                for idx, line in enumerate(lines):
                    if pos_val in line:
                        found_idx = idx
                        break
                if found_idx == -1:
                    return f'未找到定位文本：{pos_val}'
                if pos_type == 'after':
                    insert_idx = found_idx + 1
                else:
                    insert_idx = found_idx
            
            insert_text = insert_text.replace('TICK3', '```')
            if not insert_text.endswith('\n'): insert_text += '\n'
            lines.insert(insert_idx, insert_text)
            
            with open(filepath, 'w', encoding='utf-8') as f:
                f.writelines(lines)
                
            log_action('INSERT', f'{filepath} 行 {insert_idx+1}')
            return f'已在 {filepath} 的第 {insert_idx+1} 行处插入内容。'
        except Exception as e:
            return f'插入失败：{e}'

    elif cmd == 'grep':
        stripped = arg.strip()
        if stripped and stripped[0] in ('"', "'"):
            quote = stripped[0]
            end = stripped.find(quote, 1)
            if end == -1:
                return '错误：缺少闭合引号。用法：grep [-s] "关键词1|关键词2" <路径或文件>'
            keyword = stripped[1:end]
            rest = stripped[end+1:].strip().split()
            strip_indent = '-s' in rest
            target_str = rest[-1] if rest and rest[-1] != '-s' else ''
        else:
            tokens = arg.split()
            opts = [t for t in tokens if t.startswith('-')]
            non_opts = [t for t in tokens if not t.startswith('-')]
            strip_indent = '-s' in opts
            if len(non_opts) < 2:
                return '错误：缺少参数。用法：grep [-s] <关键词1|关键词2> <路径或文件>'
            keyword = non_opts[0]
            if len(keyword) >= 2 and keyword[0] in ('"', "'") and keyword[-1] == keyword[0]:
                keyword = keyword[1:-1]
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
        if err: return err
        try:
            if os.path.isfile(target):
                with open(target, 'r', encoding='utf-8') as f:
                    lines = f.readlines()
                results = []
                for idx, line in enumerate(lines, 1):
                    check = line.lstrip() if strip_indent else line
                    matched = any(kw in check for kw in cmp_kws)
                    if matched:
                        hit = [kw for kw in cmp_kws if kw in check]
                        results.append(f' 行 {idx}: {line.rstrip()}  ← {hit}')
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
                            with open(fpath, 'r', encoding='utf-8', errors='ignore') as f:
                                for idx, line in enumerate(f, 1):
                                    check = line.lstrip() if strip_indent else line
                                    matched = any(kw in check for kw in cmp_kws)
                                    if matched:
                                        hit = [kw for kw in cmp_kws if kw in check]
                                        output.append(f'{fpath}:{idx}: {line.rstrip()}  ← {hit}')
                        except: pass
                if output:
                    return '\n'.join(output)
                return f'在目录 {target} 中未找到匹配。'
            else:
                return f'错误：路径不存在 {target}'
        except Exception as e:
            return f'搜索失败：{e}'

    elif cmd == 'head':
        parts = arg.split()
        if not parts:
            return '错误：缺少文件路径。用法：head <路径> [行数]'
        filepath = safe_path(W, parts[0])
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        err = _check_permission('head', filepath)
        if err: return err
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = [f.readline().rstrip() for _ in range(n)]
            log_action('HEAD', filepath)
            return '\n'.join(lines) if any(lines) else '（文件为空）'
        except Exception as e:
            return f'读取失败：{e}'

    elif cmd == 'tail':
        parts = arg.split()
        if not parts:
            return '错误：缺少文件路径。用法：tail <路径> [行数]'
        filepath = safe_path(W, parts[0])
        n = int(parts[1]) if len(parts) > 1 and parts[1].isdigit() else 10
        err = _check_permission('tail', filepath)
        if err: return err
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
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
            sep = arg.split(None, 1)
            filepath = safe_path(W, sep[0])
            content = sep[1] if len(sep) > 1 else ''
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
        parts = arg.strip().split()
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
        if err: return err
        if start_line == 0 and clipboard_mode and os.path.isfile(filepath):
            try:
                filename = os.path.basename(filepath)
                with open(filepath, 'rb') as f:
                    b64 = base64.b64encode(f.read()).decode('ascii')
                return f'__CLIPBOARD_FILE__{filename}\x00{b64}'
            except Exception as e:
                return f'读取失败：{e}'
        try:
            with open(filepath, 'r', encoding='utf-8') as f:
                lines = f.readlines()
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
                content = ''.join(lines)
                log_action('READ', filepath)
                if len(content) > 5000:
                    return f'{content[:5000]}\n\n...（文件过长，仅显示前 5000 字符，共 {len(content)} 字符）'
                return content if content else '（文件为空）'
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
            sep = arg.split(None, 1)
            filepath = safe_path(W, sep[0])
            content = sep[1] if len(sep) > 1 else ''
        content = content.replace('TICK3', '```')
        err = _check_permission('append', filepath)
        if err:
            return err
        try:
            os.makedirs(os.path.dirname(filepath) or '.', exist_ok=True)
            with open(filepath, 'a', encoding='utf-8') as f:
                f.write('\n' + content)
            log_action('APPEND', filepath)
            return f'已追加到文件：{filepath}'
        except Exception as e:
            return f'追加失败：{e}'

    elif cmd == 'delete':
        if not arg.strip():
            return '错误：缺少文件路径。用法：delete <路径>'
        filepath = safe_path(W, arg.strip())
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
        sep = arg.split()
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
        sep = arg.split()
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
        dirpath = safe_path(W, arg.strip()) if arg.strip() else W
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
        dirpath = safe_path(W, arg.strip())
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
        if not arg.strip():
            return '错误：缺少命令。用法：exec <系统命令>'
        log_action('EXEC', arg.strip())
        try:
            result = subprocess.run(
                f'cmd /c {arg.strip()}', shell=True,
                capture_output=True, text=True, timeout=60, cwd=W
            )
            output = (result.stdout + result.stderr).strip()
            if not output:
                output = '（命令已执行，无输出）'
            if len(output) > 8000:
                output = output[:8000] + f'\n\n...（输出过长，仅显示前 8000 字符）'
            return output
        except subprocess.TimeoutExpired:
            return '错误：命令执行超时（60秒限制）。'
        except Exception as e:
            return f'执行失败：{e}'

    elif cmd == 'run':
        if not arg.strip():
            return '错误：缺少脚本路径。用法：run <脚本路径>'
        script = safe_path(W, arg.strip())
        err = _check_permission('run', script)
        if err:
            return err
        if not os.path.exists(script):
            return f'错误：脚本不存在：{script}'
        log_action('RUN', script)
        try:
            result = subprocess.run(
                ['python', script], capture_output=True, text=True, timeout=60, cwd=W
            )
            output = (result.stdout + result.stderr).strip()
            if not output:
                output = '（脚本已执行，无输出）'
            if len(output) > 8000:
                output = output[:8000] + '\n\n...（输出过长，仅显示前 8000 字符）'
            return output
        except subprocess.TimeoutExpired:
            return '错误：脚本执行超时（60秒限制）。'
        except Exception as e:
            return f'运行失败：{e}'

    # ========== 网络操作 ==========
    elif cmd == 'get':
        if not arg.strip():
            return '错误：缺少 URL。用法：get <URL>'
        url = arg.strip()
        try:
            req = urllib.request.Request(url, headers={'User-Agent': 'Agent/1.0'})
            with urllib.request.urlopen(req, timeout=15) as resp:
                body = resp.read().decode('utf-8', errors='replace')
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
        sep = arg.split()
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

        if cmd in ('create', 'append', 'replace', 'insert', 'find'):
            peek = i + 1
            content_lines = []
            has_code_start = False

            # 情况1：指令行自身包含【CodeSTART】（LLM没换行）
            if '【codestart】' in lines[i].lower():
                has_code_start = True
                clean_line = lines[i].split('【CodeSTART】', 1)[0].strip()
                parts = clean_line.split(None, 1)
                cmd = parts[0].lower()
                arg = parts[1] if len(parts) > 1 else ''
            # 情况2：下一行是【CodeSTART】（标准格式）
            elif peek < len(lines) and lines[peek].strip().lower() == '【codestart】':
                has_code_start = True
                peek += 1

            if has_code_start:
                # 跳过开头的 ```（如果有）
                if peek < len(lines) and lines[peek].strip().startswith('```'):
                    peek += 1
                # 提取内容直到【/CodeEND】或结尾的 ```
                while peek < len(lines):
                    ln = lines[peek]
                    # 遇到【/CodeEND】，只取前面的部分（容忍没换行）
                    if '【/codeend】' in ln.lower():
                        idx = ln.lower().find('【/codeend】')
                        if idx != -1:
                            content_lines.append(ln[:idx])
                        peek += 1
                        break
                    # 遇到结尾的 ```，跳过并结束
                    if ln.strip().startswith('```'):
                        peek += 1
                        break
                    content_lines.append(ln)
                    peek += 1
                i = peek
            elif peek < len(lines) and lines[peek].strip().startswith('```'):
                # 兼容没有【CodeSTART】但保留了反引号的情况
                peek += 1
                while peek < len(lines) and not lines[peek].strip().startswith('```'):
                    content_lines.append(lines[peek])
                    peek += 1
                i = peek
            else:
                # 兜底贪心模式：啥标记都没有，遇到下一条指令才截断
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

            content = '\n'.join(content_lines)
            final_cmd = f"{cmd} {arg}\x00{content}"
            result = execute_line(final_cmd)
            if result is not None:
                results.append(result)
            continue
        else:
            result = execute_line(line)
            if result is not None:
                results.append(result)
        i += 1

    # 拦截剪贴板文件模式，转为JSON返回（让JS能拿到真实的File对象）
    for r in results:
        if isinstance(r, str) and r.startswith('__CLIPBOARD_FILE__'):
            m = re.match(r'__CLIPBOARD_FILE__(.+?)\x00([\s\S]+)', r)
            if m:
                log_action('READ-CLIPBOARD', m.group(1))
                return jsonify({
                    'type': 'clipboard_file',
                    'filename': m.group(1),
                    'data': m.group(2).strip()
                })
            break

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
        'permission_enabled': permission_mgr.enabled
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

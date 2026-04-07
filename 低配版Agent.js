// ==UserScript==
// @name         低配版Agent
// @namespace    http://tampermonkey.net/
// @version      3.1
// @description  拦截LLM输出中的【cmd】指令，发给本地执行。修复卡死与长指令误杀问题。
// @match        *://*/*
// @grant        GM_registerMenuCommand
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @grant        GM_setClipboard
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

(function () {
    'use strict';

    /* ================================================================
     * 1. 存储与配置
     * ================================================================ */
    const STORE_KEY = 'low_cost_agent_config_v3';
    const DEFAULTS = {
        whitelist: ['https://chatglm.cn/'],
        apiUrl: 'http://127.0.0.1:9966/agent-exec',
        selChatContainer: '',
        selInputBox: '',
        selSendButton: '',
        debugMode: false
    };

    const cfgLoad = () => {
        try { return { ...DEFAULTS, ...GM_getValue(STORE_KEY, {}) }; }
        catch (_) { return { ...DEFAULTS }; }
    };
    const cfgSave = (c) => GM_setValue(STORE_KEY, c);
    const isWhitelisted = () => cfgLoad().whitelist.some(p => location.href.startsWith(p));

    /* ================================================================
     * 2. 样式注入
     * ================================================================ */
    GM_addStyle(`
        /* --- 配置面板 --- */
        #agent-panel{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(540px,92vw);max-height:82vh;overflow-y:auto;background:#1a1b2e;color:#d4d4d8;border:1px solid #2e3047;border-radius:14px;box-shadow:0 24px 80px rgba(0,0,0,.55);z-index:2147483647;font:14px/1.5 system-ui,sans-serif}
        #agent-panel *{box-sizing:border-box;margin:0;padding:0}
        #agent-panel-head{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid #2e3047}
        #agent-panel-head b{font-size:15px;color:#818cf8}
        #agent-panel-close{background:none;border:none;color:#71717a;font-size:20px;cursor:pointer;padding:2px 8px;border-radius:6px;transition:.15s}
        #agent-panel-close:hover{background:#2e3047;color:#f472b6}
        #agent-panel-body{padding:20px}
        .ag-sec{margin-bottom:18px}
        .ag-sec-title{font-size:11px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
        .ag-sec-title::before{content:'';width:3px;height:13px;background:#818cf8;border-radius:2px}
        .ag-field{margin-bottom:10px}
        .ag-field label{display:block;font-size:12px;color:#a1a1aa;margin-bottom:4px}
        .ag-row{display:flex;gap:6px;align-items:center}
        .ag-inp{flex:1;min-width:0;background:#2e3047;border:1px solid #3f3f46;color:#d4d4d8;padding:7px 10px;border-radius:8px;font-size:12px;outline:none;transition:.15s;font-family:'SF Mono',Consolas,monospace}
        .ag-inp:focus{border-color:#818cf8}
        .ag-btn{padding:7px 13px;border:none;border-radius:8px;font-size:12px;font-weight:600;cursor:pointer;transition:.15s;white-space:nowrap}
        .ag-btn-p{background:#818cf8;color:#0f0f23}.ag-btn-p:hover{background:#a5b4fc}
        .ag-btn-g{background:#2e3047;color:#d4d4d8;border:1px solid #3f3f46}.ag-btn-g:hover{border-color:#818cf8;color:#818cf8}
        .ag-wl-list{max-height:110px;overflow-y:auto;background:#232436;border-radius:8px;padding:3px;margin-bottom:6px}
        .ag-wl-item{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:6px;font-size:12px}
        .ag-wl-item code{flex:1;min-width:0;color:#86efac;word-break:break-all;font-family:'SF Mono',Consolas,monospace;font-size:11px}
        .ag-wl-rm{background:none;border:none;color:#f472b6;cursor:pointer;font-size:14px;padding:0 4px;opacity:.5}.ag-wl-rm:hover{opacity:1}
        .ag-match{font-size:11px;padding:3px 8px;border-radius:4px;margin-top:3px}
        .ag-m-ok{background:rgba(134,239,172,.12);color:#86efac}
        .ag-m-fail{background:rgba(244,114,182,.12);color:#f472b6}
        .ag-m-none{background:rgba(161,161,170,.1);color:#71717a}
        .ag-foot{display:flex;justify-content:flex-end;gap:8px;padding-top:14px;border-top:1px solid #2e3047;margin-top:6px}
        .ag-toggle{display:flex;align-items:center;gap:10px}
        .ag-toggle input[type=checkbox]{width:16px;height:16px;accent-color:#818cf8}

        /* --- 选择器 --- */
        #agent-pick-dim{position:fixed;inset:0;background:rgba(0,0,0,.28);z-index:2147483645;pointer-events:none}
        #agent-pick-hl{position:fixed;border:2.5px solid #818cf8;background:rgba(129,140,248,.08);border-radius:5px;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;box-shadow:0 0 0 4000px rgba(0,0,0,.25);display:none}
        #agent-pick-tip{position:fixed;background:#1a1b2e;color:#c4b5fd;border:1px solid #3f3f46;padding:5px 10px;border-radius:6px;font:11px/1.4 'SF Mono',Consolas,monospace;z-index:2147483647;pointer-events:none;max-width:420px;word-break:break-all;box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .08s}
        #agent-pick-bar{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#1a1b2e;color:#d4d4d8;border:1px solid #818cf8;padding:10px 28px;border-radius:12px;font-size:14px;z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.5);pointer-events:none}

        /* --- 调试浮窗 --- */
        #agent-debug{ position:fixed;top:10px;right:10px;width:380px;max-height:60vh; background:rgba(15,15,30,.92);border:1px solid #3f3f46;border-radius:10px; box-shadow:0 10px 40px rgba(0,0,0,.5);z-index:2147483644; display:flex;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace; backdrop-filter:blur(8px);color:#a1a1aa; }
        #agent-debug-head{padding:8px 12px;border-bottom:1px solid #2e3047;display:flex;justify-content:space-between;align-items:center;color:#d4d4d8}
        #agent-debug-body{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:4px}
        #agent-debug-body::-webkit-scrollbar{width:4px}
        #agent-debug-body::-webkit-scrollbar-thumb{background:#3f3f46;border-radius:4px}
        .ag-log{padding:4px 6px;border-radius:4px;word-break:break-all;background:rgba(255,255,255,.03);border-left:3px solid transparent}
        .ag-log-time{color:#52525b;margin-right:6px}
        .ag-log-info{border-left-color:#818cf8;color:#c4b5fd}
        .ag-log-warn{border-left-color:#facc15;color:#fde68a;background:rgba(250,204,21,.05)}
        .ag-log-err{border-left-color:#f472b6;color:#fda4af;background:rgba(244,114,182,.05)}
        .ag-log-ok{border-left-color:#86efac;color:#bbf7d0;background:rgba(134,239,172,.05)}
        #agent-debug-foot{padding:6px 12px;border-top:1px solid #2e3047;text-align:right}
        .ag-dbg-btn{background:#2e3047;border:1px solid #3f3f46;color:#a1a1aa;padding:3px 10px;border-radius:4px;cursor:pointer;font-size:11px}
        .ag-dbg-btn:hover{border-color:#818cf8;color:#818cf8}
    `);

    /* ================================================================
     * 3. 调试日志系统
     * ================================================================ */
    let _debugPanel = null;
    let _debugBody = null;

    function initDebugUI() {
        if (_debugPanel) return;
        _debugPanel = document.createElement('div');
        _debugPanel.id = 'agent-debug';
        _debugPanel.innerHTML = `
            <div id="agent-debug-head"><span>🕵️ Agent 调试台</span><button class="ag-dbg-btn" id="ag-dbg-close">隐藏</button></div>
            <div id="agent-debug-body"></div>
            <div id="agent-debug-foot"><button class="ag-dbg-btn" id="ag-dbg-clear">清空日志</button></div>
        `;
        document.body.appendChild(_debugPanel);
        _debugBody = _debugPanel.querySelector('#agent-debug-body');
        _debugPanel.querySelector('#ag-dbg-close').onclick = () => _debugPanel.style.display = 'none';
        _debugPanel.querySelector('#ag-dbg-clear').onclick = () => _debugBody.innerHTML = '';
    }

    function showDebug() {
        if (!_debugPanel) initDebugUI();
        _debugPanel.style.display = 'flex';
    }

function _truncate(str, maxDisplay = 200, keepLen = 100) {
    str = String(str);
    if (str.length > maxDisplay) return str.substring(0, keepLen) + `... (共 ${str.length} 字符)`;
    return str;
}

function log(type, msg) {
    const c = cfgLoad();
    console.log(`[Agent-${type}] ${msg}`);
    if (!c.debugMode) return;
    if (!_debugBody) return;
    if (_debugPanel.style.display === 'none') return;
    const cls = type === 'INFO' ? 'ag-log-info' : type === 'WARN' ? 'ag-log-warn' : type === 'ERR' ? 'ag-log-err' : type === 'OK' ? 'ag-log-ok' : '';
    const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
    const div = document.createElement('div');
    div.className = `ag-log ${cls}`;
    div.innerHTML = `<span class="ag-log-time">${time}</span>${esc(_truncate(msg))}`;   // ← 包一层 _truncate
    _debugBody.appendChild(div);
    _debugBody.scrollTop = _debugBody.scrollHeight;
}


    /* ================================================================
     * 4. 元素选择器
     * ================================================================ */
    const PICKER_IDS = new Set(['agent-pick-dim', 'agent-pick-hl', 'agent-pick-tip', 'agent-pick-bar', 'agent-panel', 'agent-debug']);
    let _pickActive = false, _pickType = '', _pickHL, _pickTip, _pickBar, _pickDim;
    const TYPE_LABEL = { chat: '聊天记录容器', input: '输入框', send: '发送按钮' };

    function genSelector(el) {
        if (!el || el === document.body || el === document.documentElement) return '';
        const segs = [];
        let cur = el;
        while (cur && cur !== document.body && cur !== document.documentElement && segs.length < 5) {
            let seg = cur.tagName.toLowerCase();
            if (cur.id && !/\d/.test(cur.id)) {
                segs.unshift('#' + CSS.escape(cur.id));
                break;
            }
            if (cur.className && typeof cur.className === 'string') {
                const cls = cur.className.trim().split(/\s+/).filter(c => c && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c)).slice(0, 3);
                if (cls.length) seg += '.' + cls.map(c => CSS.escape(c)).join('.');
            }
            if (cur !== el && cur.parentElement) {
                const sib = [...cur.parentElement.children].filter(n => n.tagName === cur.tagName);
                if (sib.length > 1) seg += ':nth-child(' + ([...cur.parentElement.children].indexOf(cur) + 1) + ')';
            }
            segs.unshift(seg);
            cur = cur.parentElement;
        }
        const sel = segs.join(' > ');
        try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
        return sel;
    }

    function pickerEnter(type) {
        _pickActive = true;
        _pickType = type;
        hidePanel();
        _pickDim = document.createElement('div'); _pickDim.id = 'agent-pick-dim'; document.body.appendChild(_pickDim);
        _pickHL = document.createElement('div'); _pickHL.id = 'agent-pick-hl'; document.body.appendChild(_pickHL);
        _pickTip = document.createElement('div'); _pickTip.id = 'agent-pick-tip'; document.body.appendChild(_pickTip);
        _pickBar = document.createElement('div'); _pickBar.id = 'agent-pick-bar';
        _pickBar.innerHTML = `🎯 请点击 <span class="ag-target">${TYPE_LABEL[type]}</span><span class="ag-esc">ESC 取消</span>`;
        document.body.appendChild(_pickBar);
        document.addEventListener('mousemove', _onMove, true);
        document.addEventListener('click', _onClick, true);
        document.addEventListener('contextmenu', _onCtx, true);
        document.addEventListener('keydown', _onKey, true);
    }

    function pickerExit() {
        _pickActive = false; _pickType = '';
        document.removeEventListener('mousemove', _onMove, true);
        document.removeEventListener('click', _onClick, true);
        document.removeEventListener('contextmenu', _onCtx, true);
        document.removeEventListener('keydown', _onKey, true);
        [_pickDim, _pickHL, _pickTip, _pickBar].forEach(e => e && e.remove());
        showPanel();
    }

    function _targetAt(x, y) {
        let el = document.elementFromPoint(x, y);
        while (el && PICKER_IDS.has(el.id)) el = el.parentElement;
        return el;
    }

    function _onMove(e) {
        e.stopPropagation(); e.preventDefault();
        const el = _targetAt(e.clientX, e.clientY);
        if (!el) { _pickHL.style.display = 'none'; _pickTip.style.opacity = '0'; return; }
        const r = el.getBoundingClientRect();
        _pickHL.style.display = 'block';
        Object.assign(_pickHL.style, { left: (r.left-2)+'px', top: (r.top-2)+'px', width: (r.width+4)+'px', height: (r.height+4)+'px' });
        const sel = genSelector(el);
        _pickTip.textContent = sel + ' ← ' + el.tagName.toLowerCase();
        _pickTip.style.opacity = '1';
        _pickTip.style.left = Math.min(e.clientX + 14, innerWidth - 430) + 'px';
        _pickTip.style.top = (e.clientY + 22) + 'px';
    }

    function _onClick(e) {
        e.stopPropagation(); e.preventDefault();
        const el = _targetAt(e.clientX, e.clientY);
        if (!el) return;
        const sel = genSelector(el);
        if (!sel) return;
        const c = cfgLoad();
        if (_pickType === 'chat') c.selChatContainer = sel;
        if (_pickType === 'input') c.selInputBox = sel;
        if (_pickType === 'send') c.selSendButton = sel;
        cfgSave(c);
        log('OK', `已选择 [${TYPE_LABEL[_pickType]}]: ${sel}`);
        log('INFO', `目标元素详情: <${el.tagName.toLowerCase()}>, class="${el.className}", id="${el.id}"`);
        pickerExit();
    }

    function _onCtx(e) { e.stopPropagation(); e.preventDefault(); }
    function _onKey(e) { if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); pickerExit(); } }

    /* ================================================================
     * 5. 配置面板
     * ================================================================ */
    let _panel = null;
    function showPanel() {
        if (!_panel) { _panel = document.createElement('div'); _panel.id = 'agent-panel'; document.body.appendChild(_panel); }
        _renderPanel();
        _panel.style.display = 'block';
    }
    function hidePanel() { if (_panel) _panel.style.display = 'none'; }

    function _renderPanel() {
        const c = cfgLoad();
        _panel.innerHTML = `
            <div id="agent-panel-head"><b>🔧 低配版 Agent 配置</b><button id="agent-panel-close">✕</button></div>
            <div id="agent-panel-body">
                <div class="ag-sec">
                    <div class="ag-sec-title">控制台</div>
                    <div class="ag-toggle">
                        <input type="checkbox" id="ag-debug-toggle" ${c.debugMode ? 'checked' : ''} />
                        <label for="ag-debug-toggle" style="cursor:pointer">启用调试模式 (右侧显示日志浮窗)</label>
                    </div>
                </div>
                <div class="ag-sec">
                    <div class="ag-sec-title">网站白名单</div>
                    <div class="ag-wl-list" id="ag-wl-list">${c.whitelist.length ? c.whitelist.map((u, i) => `<div class="ag-wl-item"><code>${esc(u)}</code><button class="ag-wl-rm" data-i="${i}">✕</button></div>`).join('') : '<div style="padding:8px 10px;color:#52525b;font-size:12px">暂无</div>'}</div>
                    <div class="ag-row"><input class="ag-inp" id="ag-wl-new" placeholder="https://example.com/" /><button class="ag-btn ag-btn-g" id="ag-wl-add">添加</button></div>
                </div>
                <div class="ag-sec">
                    <div class="ag-sec-title">本地 Agent 服务</div>
                    <div class="ag-field"><label>接收指令的 HTTP 地址</label><input class="ag-inp" id="ag-api" value="${esc(c.apiUrl)}" /></div>
                </div>
                <div class="ag-sec">
                    <div class="ag-sec-title">页面元素绑定</div>
                    <div class="ag-field"><label>聊天记录容器</label><div class="ag-row"><input class="ag-inp" id="ag-s-chat" value="${esc(c.selChatContainer)}" /><button class="ag-btn ag-btn-p" id="ag-pick-chat">🖱 选择</button></div><div id="ag-m-chat"></div></div>
                    <div class="ag-field"><label>输入框</label><div class="ag-row"><input class="ag-inp" id="ag-s-input" value="${esc(c.selInputBox)}" /><button class="ag-btn ag-btn-p" id="ag-pick-input">🖱 选择</button></div><div id="ag-m-input"></div></div>
                    <div class="ag-field"><label>发送按钮</label><div class="ag-row"><input class="ag-inp" id="ag-s-send" value="${esc(c.selSendButton)}" /><button class="ag-btn ag-btn-p" id="ag-pick-send">🖱 选择</button></div><div id="ag-m-send"></div></div>
                </div>
                <div class="ag-foot"><button class="ag-btn ag-btn-g" id="ag-cancel">取消</button><button class="ag-btn ag-btn-p" id="ag-save">💾 保存配置</button></div>
            </div>`;

        _panel.querySelector('#agent-panel-close').onclick = hidePanel;
        _panel.querySelector('#ag-cancel').onclick = hidePanel;

        _panel.querySelector('#ag-debug-toggle').onchange = (e) => {
            if(e.target.checked) { showDebug(); log('INFO', '调试模式已开启'); }
            else { if(_debugPanel) _debugPanel.style.display = 'none'; }
        };

        const wlInput = _panel.querySelector('#ag-wl-new');
        const doAdd = () => {
            const v = wlInput.value.trim(); if (!v) return;
            const cfg = cfgLoad(); if (!cfg.whitelist.includes(v)) cfg.whitelist.push(v);
            cfgSave(cfg); wlInput.value=''; _renderPanel();
        };
        _panel.querySelector('#ag-wl-add').onclick = doAdd;
        wlInput.onkeydown = e => { if (e.key === 'Enter') doAdd(); };
        _panel.querySelectorAll('.ag-wl-rm').forEach(btn => {
            btn.onclick = () => { const cfg = cfgLoad(); cfg.whitelist.splice(+btn.dataset.i, 1); cfgSave(cfg); _renderPanel(); };
        });

        _panel.querySelector('#ag-pick-chat').onclick = () => pickerEnter('chat');
        _panel.querySelector('#ag-pick-input').onclick = () => pickerEnter('input');
        _panel.querySelector('#ag-pick-send').onclick = () => pickerEnter('send');

        ['chat', 'input', 'send'].forEach(t => {
            const key = t === 'chat' ? 'selChatContainer' : t === 'input' ? 'selInputBox' : 'selSendButton';
            _panel.querySelector(`#ag-s-${t}`).addEventListener('input', function() { _showMatch(this.value.trim(), `ag-m-${t}`); });
            _showMatch(c[key], `ag-m-${t}`);
        });

        _panel.querySelector('#ag-save').onclick = () => {
            const cfg = cfgLoad();
            cfg.debugMode = _panel.querySelector('#ag-debug-toggle').checked;
            cfg.apiUrl = _panel.querySelector('#ag-api').value.trim() || cfg.apiUrl;
            cfg.selChatContainer = _panel.querySelector('#ag-s-chat').value.trim();
            cfg.selInputBox = _panel.querySelector('#ag-s-input').value.trim();
            cfg.selSendButton = _panel.querySelector('#ag-s-send').value.trim();
            cfgSave(cfg); hidePanel();
            if (isWhitelisted()) initAgent();
            if (cfg.debugMode) showDebug();
        };
    }

    function _showMatch(sel, id) {
        const el = _panel.querySelector('#' + id);
        if (!sel) { el.innerHTML = '<div class="ag-match ag-m-none">未设置</div>'; return; }
        try {
            const n = document.querySelectorAll(sel).length;
            el.innerHTML = n === 0 ? '<div class="ag-match ag-m-fail">✘ 未匹配</div>' : n === 1 ? '<div class="ag-match ag-m-ok">✔ 精确匹配 1 个</div>' : `<div class="ag-match ag-m-ok">✔ 匹配 ${n} 个</div>`;
        } catch (_) { el.innerHTML = '<div class="ag-match ag-m-fail">✘ 语法错误</div>'; }
    }

/* ================================================================
 * 6. Agent 核心逻辑
 * ================================================================ */
let _clipboardMode = false;
let _permissionEnabled = true;

function _pollConfig() {
    const c = cfgLoad();
    const pollUrl = c.apiUrl.replace('/agent-exec', '/agent-config-poll');
    GM_xmlhttpRequest({
        method: 'GET',
        url: pollUrl,
        timeout: 30000,
        onload(r) {
            if (r.status === 200) {
                try {
                    const data = JSON.parse(r.responseText);
                    const newClip = !!data.clipboard_mode;
                    const newPerm = !!data.permission_enabled;
                    if (newClip !== _clipboardMode) {
                        _clipboardMode = newClip;
                        log('INFO', `剪贴板模式: ${_clipboardMode ? '已开启' : '已关闭'}`);
                    }
                    if (newPerm !== _permissionEnabled) {
                        _permissionEnabled = newPerm;
                        log('INFO', `目录限制: ${_permissionEnabled ? '已启用' : '已禁用'}`);
                    }
                } catch(e) {}
            }
            _pollConfig();
        },
        onerror() {
            setTimeout(_pollConfig, 5000);
        },
        ontimeout() {
            _pollConfig();
        }
    });
}


    let _pollTimer = null;
    let _fillTimeout = null;
    let _lastAnswerEl = null;
    const _currentRoundSent = new Set();



    let _heartbeatCounter = 0;
    let _knownAnswers = [];


    function getCleanText(el) {
        const clone = el.cloneNode(true);
        // 移除所有疑似思考过程的节点
        const thinkNodes = clone.querySelectorAll(
            '[class*="thinking"], [class*="reasoning"], [class*="probe"], [class*="deepseek-reason"], details'
        );
        thinkNodes.forEach(n => n.remove());
        // 移除代码块右上角的复制/操作按钮，防止文本污染
        const uiNodes = clone.querySelectorAll(
            'button, [class*="copy"], [class*="operate"], [class*="action"], [class*="toolbar"]'
        );
        uiNodes.forEach(n => n.remove());
        const codeNodes = clone.querySelectorAll('pre');
            codeNodes.forEach(n => { if(n.closest('.answer')?.textContent.includes('\u3010CodeSTART\u3011')) return; n.remove(); });
        // 【关键修复】textContent 不会在块级元素之间插入换行
        // 浏览器渲染后，相邻的 <p>、<div>、<pre> 等元素文本会被拼接成一行
        // 必须手动在每个块级元素前插入换行符
        function injectNewlines(node) {
            for (let i = node.childNodes.length - 1; i >= 0; i--) {
                const child = node.childNodes[i];
                if (child.nodeType === 1) {
                    if (/^(P|DIV|BR|LI|H[1-6]|PRE|BLOCKQUOTE|TR|HR|TABLE|UL|OL|SECTION|ARTICLE|HEADER|FOOTER|FIGURE|DD|DT|DL)$/.test(child.tagName)) {
                        node.insertBefore(document.createTextNode('\n'), child);
                    }
                    injectNewlines(child);
                }
            }
        }
        injectNewlines(clone);

        return clone.textContent;
    }




    function initAgent() {
        if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
        if (_fillTimeout) { clearTimeout(_fillTimeout); _fillTimeout = null; }
        _lastAnswerEl = null;
        _currentRoundSent.clear();
        _heartbeatCounter = 0;

        const c = cfgLoad();
        const selector = c.selChatContainer || 'div.chatScrollContainer';
        let currentContainer = document.querySelector(selector);

        if (!currentContainer) {
            log('WARN', `找不到容器 ${selector}，5秒后重试...`);
            setTimeout(initAgent, 5000);
            return;
        }

        // 首次启动：扫描已有回答作为历史，不执行
        const existingAnswers = [...currentContainer.querySelectorAll('.answer')];
        _knownAnswers = existingAnswers;
        _lastAnswerEl = null;
        _currentRoundSent.clear();

        _pollConfig();
        log('OK', `✅ 监听已启动！`);

        _pollTimer = setInterval(() => {
            try {
                _heartbeatCounter++;
                const freshContainer = document.querySelector(selector);
                if (!freshContainer) return;

                // 检测方式1：容器元素本身被替换了
                if (freshContainer !== currentContainer) {
                    if (_fillTimeout) { clearTimeout(_fillTimeout); _fillTimeout = null; }
                    log('WARN', '🚨 检测到聊天容器被替换（新对话），重置监听状态...');
                    currentContainer = freshContainer;
                    _lastAnswerEl = null;
                    _currentRoundSent.clear();
                    _pendingSkipMsgs = [];
                    _knownAnswers = [];
                    _cmdQueue = [];
                    _prevAnswersLen = -1;
                    if (_fallbackTimer) { clearTimeout(_fallbackTimer); _fallbackTimer = null; }

                    log('OK', `✅ 已切换至新容器，继续监听...`);
                    return;
                }

                const answers = [...currentContainer.querySelectorAll('.answer')];
                const currentSet = new Set(answers);

                // 检测方式2：容器没变，但所有旧回答都消失了 → 也是新对话
                if (_knownAnswers.length > 0 && !_knownAnswers.some(el => currentSet.has(el))) {
                    _lastAnswerEl = null;
                    _currentRoundSent.clear();
                    _pendingSkipMsgs = [];
                    _cmdQueue = [];
                    _prevAnswersLen = -1;
                    if (_fallbackTimer) { clearTimeout(_fallbackTimer); _fallbackTimer = null; }

                    log('WARN', '🚨 检测到对话被清空（新对话），重置监听状态...');
                }
                _knownAnswers = answers;

                // 心跳
                if (_heartbeatCounter % 20 === 0) {
                   log('INFO', `💓 心跳 | ${answers.length} 个回答 | 本轮${_currentRoundSent.size}`);
                }

                if (answers.length === 0) return;
                const lastAnswer = answers[answers.length - 1];
                if (lastAnswer !== _lastAnswerEl) {
                    _lastAnswerEl = lastAnswer;
                    _currentRoundSent.clear();
                }
                const re = /【cmd】([\s\S]*?)【\/cmd】/g;
                const text = getCleanText(lastAnswer);
                re.lastIndex = 0;
                let m;
                while ((m = re.exec(text)) !== null) {
                    const cmdStr = m[1].trim();
                    const normKey = m[0].replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n');
                    const preview = cmdStr.substring(0, 100) + (cmdStr.length > 100 ? `... (共 ${cmdStr.length} 字符)` : '');
                    if (_currentRoundSent.has(normKey)) continue;
                    log('OK', `🎉 捕获指令: ${preview}`);
                    _currentRoundSent.add(normKey);
                    _enqueueCmd(cmdStr);
                }


                // 文本稳定检测：等 LLM 输出停稳再批量发送
                const currentLen = answers.reduce((s, a) => s + getCleanText(a).length, 0);
                if (_cmdQueue.length > 0 && currentLen === _prevAnswersLen) {
                    if (_fallbackTimer) { clearTimeout(_fallbackTimer); _fallbackTimer = null; }
                    const batch = _cmdQueue.join('\n');
                    _cmdQueue = [];
                    _dispatch(batch);
                }
                _prevAnswersLen = currentLen;

            } catch (err) {
                console.error('[Agent-ERR] 轮询异常（已恢复）:', err);
            }
        }, 1500);
    }

let _cmdQueue = [];
let _pendingSkipMsgs = [];
let _prevAnswersLen = -1;
let _fallbackTimer = null;

function _enqueueCmd(cmdStr) {
    _cmdQueue.push(cmdStr);
    if (!_fallbackTimer) {
        _fallbackTimer = setTimeout(() => {
            _fallbackTimer = null;
            if (_cmdQueue.length > 0) {
                const batch = _cmdQueue.join('\n');
                _cmdQueue = [];
               _dispatch(batch, _pendingSkipMsgs);
                _pendingSkipMsgs = [];
            }
        }, 6000);
    }
}


    function _dispatch(cmd, skipMsgs = []) {
        const c = cfgLoad();
        log('INFO', `发送至本地: ${c.apiUrl}`);
        cmd = cmd.replace(/\u2014/g, '--');
        cmd = cmd.replace(/\u2013/g, '-');
        cmd = cmd.replace(/\u201C/g, '"');
        cmd = cmd.replace(/\u201D/g, '"');
        cmd = cmd.replace(/\u2018/g, "'");
        cmd = cmd.replace(/\u2019/g, "'");

        GM_xmlhttpRequest({
            method: 'POST',
            url: c.apiUrl,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({ command: cmd }),
            onload(r) {
                if (r.status === 200) {
                    // 判断是否是剪贴板文件模式（Python返回的是JSON）
                    try {
                        const data = JSON.parse(r.responseText);
                        if (data.type === 'clipboard_file') {
                            log('OK', `准备粘贴文件: ${data.filename}`);
                            _pasteFile(data.filename, data.data);
                            return;
                        }
                    } catch(e) {}
                    // 普通文本模式
                    let resultText = r.responseText;
                    if (skipMsgs.length > 0) resultText = skipMsgs.join('\n') + '\n\n' + resultText;
                    log('OK', `本地返回: ${resultText}`);
                    _fillAndSend(resultText);
                } else {
                    log('ERR', `HTTP ${r.status}`);
                    _fillAndSend(`[Agent 错误] HTTP ${r.status}`);
                }
            },
            onerror() {
                log('ERR', '无法连接本地服务');
                _fillAndSend('[Agent 错误] 无法连接本地服务');
            }
        });
    }


    function _directInput(input, text) {
        input.focus();
        if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
            const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) setter.call(input, text);
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            document.execCommand('selectAll', false, null);
            document.execCommand('insertText', false, text);
        }
    }

    function _pasteFile(filename, b64Data) {
        const c = cfgLoad();
        const input = document.querySelector(c.selInputBox);
        const btn = document.querySelector(c.selSendButton);
        if (!input || !btn) {
            log('ERR', '找不到输入框或发送按钮');
            return;
        }
        try {
            // base64 → Uint8Array → Blob → File（网页只认真实的File对象）
            const byteChars = atob(b64Data);
            const byteArr = new Uint8Array(byteChars.length);
            for (let i = 0; i < byteChars.length; i++) {
                byteArr[i] = byteChars.charCodeAt(i);
            }
            const ext = filename.split('.').pop().toLowerCase();
            const mimeMap = {
                'js':'text/javascript','ts':'text/typescript',
                'html':'text/html','css':'text/css',
                'json':'application/json','md':'text/markdown',
                'py':'text/x-python','txt':'text/plain',
                'xml':'text/xml','csv':'text/csv'
            };
            const mime = mimeMap[ext] || 'text/plain';
            const file = new File([byteArr], filename, { type: mime });

            input.focus();
            const dt = new DataTransfer();
            dt.items.add(file);

            const pasteEvt = new ClipboardEvent('paste', {
                bubbles: true, cancelable: true
            });
            // 强制注入包含File对象的DataTransfer，覆盖浏览器空白的默认值
            Object.defineProperty(pasteEvt, 'clipboardData', {
                get() { return dt; }
            });
            input.dispatchEvent(pasteEvt);
            log('OK', '已触发文件粘贴事件');

            // 在光标处追加提示文本（不能用全选，否则会清空刚粘贴的文件）
            try {
                document.execCommand('insertText', false, '[Poker Agent] 文件已上传');
            } catch (e) {
                try {
                    input.dispatchEvent(new InputEvent('input', {
                        inputType: 'insertText', data: '[Poker Agent] 文件已上传', bubbles: true
                    }));
                } catch (e2) {}
            }

            if (_fillTimeout) { clearTimeout(_fillTimeout); _fillTimeout = null; }
            _accumText = '';

            // 文件粘贴后网页可能需要一点时间渲染上传提示，等1.5秒再点发送
            _fillTimeout = setTimeout(() => {
                _fillTimeout = null;
                try { btn.click(); } catch (err) { log('ERR', `点击发送失败: ${err.message}`); }
            }, 1500);
        } catch (err) {
            log('ERR', `文件粘贴失败: ${err.message}`);
        }
    }


    let _accumText = '';
    function _fillAndSend(text) {
        text = '[Poker Agent] ' + text;
        _accumText = _accumText ? _accumText + '\n\n' + text : text;

        if (_fillTimeout) clearTimeout(_fillTimeout);

        const c = cfgLoad();
        const input = document.querySelector(c.selInputBox);
        const btn = document.querySelector(c.selSendButton);
        if (!input || !btn) {
            log('ERR', '找不到输入框或发送按钮');
            return;
        }
        log('INFO', '正在模拟输入并发送...');
        _directInput(input, _accumText);

        _fillTimeout = setTimeout(() => {
            _fillTimeout = null;
            _accumText = '';
            try { btn.click(); } catch (err) { log('ERR', `点击发送失败: ${err.message}`); }
        }, 800);
    }



    /* ================================================================
     * 7. 启动入口
     * ================================================================ */
    GM_registerMenuCommand('⚙️ Agent 配置面板', showPanel);

    if (cfgLoad().debugMode) setTimeout(initDebugUI, 500);

    if (isWhitelisted()) {
        const start = () => setTimeout(initAgent, 1500);
        if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
        else start();
    }

    function esc(s) {
        const d = document.createElement('div');
        d.textContent = s;
        return d.innerHTML;
    }

})();

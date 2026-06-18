// ==UserScript==
// @name PokerAgent
// @namespace http://tampermonkey.net/
// @version 12.4
// @author LMaxRouterCN
// @description PokerAgent的浏览器端核心脚本，提供元素选择、配置管理、调试日志等功能，支持多站点独立配置和自动发送功能。
// @match *://*/*
// @grant GM_registerMenuCommand
// @grant GM_xmlhttpRequest
// @grant GM_getValue
// @grant GM_setValue
// @grant GM_addStyle
// @grant GM_setClipboard
// @connect localhost
// @connect 127.0.0.1
// ==/UserScript==

//* - 增加 selAnswerItem 可配置选择器（替代硬编码 .answer）
//* - 增加 cleanIgnoreClassKeywords / cleanRemoveButtonLike / cleanRemovePre 清理规则可配置
//* - 修复 <pre> 处理中 .answer 硬编码 bug
//* - 补全 injectNewlines 块级元素列表
//* - 改进 _getSendBtnFingerprint 指纹计算
//* - 增加心跳自诊断（连续未找到回答元素时打警告）
//* - 重构核心逻辑适配流式架构
//* - [v12.3] 用 GM_xmlhttpRequest 流式模式替代 EventSource，解决 SSE 跨域问题
//* - [v12.4] _renderTaskBlock 重构：只替换标记间内容，保留区块外用户输入，移除废弃的 _userInputCache 快照机制

(function () {
    'use strict';

    /* ================================================================
     * 1. 存储与配置
     * ================================================================ */
    const SITE_DEFAULTS = {
        apiUrl: 'http://127.0.0.1:9966/agent-exec',
        selChatContainer: '',
        selInputBox: '',
        selSendButton: '',
        selAnswerItem: '.answer',
        cleanIgnoreClassKeywords: 'thinking,reasoning,probe,deepseek-reason',
        cleanRemoveButtonLike: true,
        cleanRemovePre: true,
        sendBtnBusyFingerprints: [],
        sendBtnIdleFingerprints: [],
        verifyMode: 'single',
        waitDelayAfterDone: 500,
        showAutoSendToggle: false,
        autoSendTogglePos: 'right',
        autoSendMode: 'click'
    };

    const DEFAULTS = {
        whitelist: ['https://chatglm.cn/'],
        debugMode: false,
        ...SITE_DEFAULTS
    };

    const STORE_KEY = 'low_cost_agent_config_v4';

    function _loadStore() {
        let store;
        try {
            store = GM_getValue(STORE_KEY, null);
        } catch (_) {
            store = null;
        }

        if (!store) {
            return {
                whitelist: ['https://chatglm.cn/'],
                debugMode: false,
                defaults: { ...SITE_DEFAULTS },
                perSite: {
                    'https://chatglm.cn/': {
                        ...SITE_DEFAULTS,
                        selChatContainer: 'div.chatScrollContainer'
                    }
                }
            };
        }

        return _migrateStore(store);
    }

    function _saveStore(store) {
        GM_setValue(STORE_KEY, store);
    }

    function _migrateStore(store) {
        if (store.defaults && store.perSite !== undefined) {
            const clearOld = (cfg) => {
                if (cfg.sendBtnIdleFingerprint !== undefined && cfg.sendBtnIdleFingerprint !== '') {
                    cfg.sendBtnBusyFingerprints = [];
                    cfg.sendBtnIdleFingerprints = [];
                    delete cfg.sendBtnIdleFingerprint;
                }
                if (!cfg.sendBtnBusyFingerprints) cfg.sendBtnBusyFingerprints = [];
                if (!cfg.sendBtnIdleFingerprints) cfg.sendBtnIdleFingerprints = [];
                if (!cfg.verifyMode) cfg.verifyMode = 'single';
                if (!cfg.waitDelayAfterDone) cfg.waitDelayAfterDone = 500;
                if (cfg.autoSendByEnter !== undefined && cfg.autoSendMode === undefined) {
                    cfg.autoSendMode = cfg.autoSendByEnter ? 'enter' : 'click';
                    delete cfg.autoSendByEnter;
                }
                if (!cfg.autoSendMode) cfg.autoSendMode = 'click';
                if (cfg.autoSendByEnter !== undefined) delete cfg.autoSendByEnter;
                if (!cfg.selAnswerItem) cfg.selAnswerItem = '.answer';
                if (cfg.cleanIgnoreClassKeywords === undefined) cfg.cleanIgnoreClassKeywords = 'thinking,reasoning,probe,deepseek-reason';
                if (cfg.cleanRemoveButtonLike === undefined) cfg.cleanRemoveButtonLike = true;
                if (cfg.cleanRemovePre === undefined) cfg.cleanRemovePre = true;
            };
            if (store.defaults) clearOld(store.defaults);
            if (store.perSite) Object.values(store.perSite).forEach(clearOld);
            return store;
        }

        const newStore = {
            whitelist: store.whitelist || ['https://chatglm.cn/'],
            debugMode: !!store.debugMode,
            defaults: { ...SITE_DEFAULTS },
            perSite: {}
        };

        for (const key of Object.keys(SITE_DEFAULTS)) {
            if (store[key] !== undefined) newStore.defaults[key] = store[key];
        }

        if (newStore.defaults.autoSendByEnter !== undefined && newStore.defaults.autoSendMode === undefined) {
            newStore.defaults.autoSendMode = newStore.defaults.autoSendByEnter ? 'enter' : 'click';
            delete newStore.defaults.autoSendByEnter;
        }

        return newStore;
    }

    function _matchSite() {
        const store = _loadStore();
        return store.whitelist.find(p => location.href.startsWith(p)) || null;
    }

    function _getConfigSource() {
        const store = _loadStore();
        const site = _matchSite();
        if (site && store.perSite && store.perSite[site]) return site;
        return 'defaults';
    }

    function cfgLoad() {
        const store = _loadStore();
        const site = _matchSite();
        const siteCfg = (site && store.perSite && store.perSite[site]) ? store.perSite[site] : (store.defaults || {});
        const merged = { ...DEFAULTS, whitelist: store.whitelist, debugMode: store.debugMode, ...siteCfg };
        if (merged.autoSendByEnter !== undefined && merged.autoSendMode === undefined) {
            merged.autoSendMode = merged.autoSendByEnter ? 'enter' : 'click';
        }
        return merged;
    }

    let _editTarget = 'defaults';

    function cfgSave(panelValues) {
        const store = _loadStore();
        store.debugMode = panelValues.debugMode;

        const siteData = { ...SITE_DEFAULTS };
        for (const key of Object.keys(SITE_DEFAULTS)) {
            if (panelValues[key] !== undefined) siteData[key] = panelValues[key];
        }

        if (_editTarget === 'defaults') {
            store.defaults = siteData;
        } else {
            if (!store.perSite) store.perSite = {};
            store.perSite[_editTarget] = siteData;
        }
        _saveStore(store);
    }

    function cfgSaveRuntime(partial) {
        const store = _loadStore();
        const source = _getConfigSource();
        if (source === 'defaults') {
            if (!store.defaults) store.defaults = { ...SITE_DEFAULTS };
            Object.assign(store.defaults, partial);
        } else {
            if (!store.perSite) store.perSite = {};
            if (!store.perSite[source]) store.perSite[source] = { ...SITE_DEFAULTS };
            Object.assign(store.perSite[source], partial);
        }
        _saveStore(store);
    }

    const isWhitelisted = () => cfgLoad().whitelist.some(p => location.href.startsWith(p));

    /* ================================================================
     * 2. 样式注入
     * ================================================================ */
    GM_addStyle(`
        #agent-panel{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(540px,92vw);max-height:82vh;overflow-y:auto;background:#1a1b2e;color:#d4d4d8;border:1px solid #2e3047;border-radius:0;box-shadow:0 24px 80px rgba(0,0,0,.55);z-index:2147483647;font:14px/1.5 system-ui,sans-serif}
        #agent-panel *{box-sizing:border-box;margin:0;padding:0}
        #agent-panel-head{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid #2e3047}
        #agent-panel-head b{font-size:15px;color:#818cf8}
        #agent-panel-close{background:none;border:none;color:#71717a;font-size:20px;cursor:pointer;padding:2px 8px;border-radius:0;transition:.15s}
        #agent-panel-close:hover{background:#2e3047;color:#f472b6}
        #agent-panel-body{padding:20px}
        .ag-sec{margin-bottom:18px}
        .ag-sec-title{font-size:11px;font-weight:700;color:#71717a;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
        .ag-sec-title::before{content:'';width:3px;height:13px;background:#818cf8;border-radius:0}
        .ag-field{margin-bottom:10px}
        .ag-field label{display:block;font-size:12px;color:#a1a1aa;margin-bottom:4px}
        .ag-row{display:flex;gap:6px;align-items:center}
        .ag-inp{flex:1;min-width:0;background:#2e3047;border:1px solid #3f3f46;color:#d4d4d8;padding:7px 10px;border-radius:0;font-size:12px;outline:none;transition:.15s;font-family:'SF Mono',Consolas,monospace}
        .ag-inp:focus{border-color:#818cf8}
        .ag-btn{padding:7px 13px;border:none;border-radius:0;font-size:12px;font-weight:600;cursor:pointer;transition:.15s;white-space:nowrap}
        .ag-btn-p{background:#818cf8;color:#0f0f23}.ag-btn-p:hover{background:#a5b4fc}
        .ag-btn-g{background:#2e3047;color:#d4d4d8;border:1px solid #3f3f46}.ag-btn-g:hover{border-color:#818cf8;color:#818cf8}
        .ag-wl-list{max-height:110px;overflow-y:auto;background:#232436;border-radius:0;padding:3px;margin-bottom:6px}
        .ag-wl-item{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:0;font-size:12px}
        .ag-wl-item code{flex:1;min-width:0;color:#86efac;word-break:break-all;font-family:'SF Mono',Consolas,monospace;font-size:11px}
        .ag-wl-rm{background:none;border:none;color:#f472b6;cursor:pointer;font-size:14px;padding:0 4px;opacity:.5}.ag-wl-rm:hover{opacity:1}
        .ag-match{font-size:11px;padding:3px 8px;border-radius:0;margin-top:3px}
        .ag-m-ok{background:rgba(134,239,172,.12);color:#86efac}
        .ag-m-fail{background:rgba(244,114,182,.12);color:#f472b6}
        .ag-m-none{background:rgba(161,161,170,.1);color:#71717a}
        .ag-foot{display:flex;justify-content:flex-end;gap:8px;padding-top:14px;border-top:1px solid #2e3047;margin-top:6px}
        .ag-toggle{display:flex;align-items:center;gap:10px}
        .ag-toggle input[type=checkbox]{width:16px;height:16px;accent-color:#818cf8}
        .ag-pos-group{display:flex;gap:0}
        .ag-pos-btn{padding:4px 10px;background:#2e3047;border:1px solid #3f3f46;color:#a1a1aa;font-size:12px;cursor:pointer;transition:.15s;border-radius:0}
        .ag-pos-btn+.ag-pos-btn{border-left:none}
        .ag-pos-btn.active{background:#818cf8;color:#0f0f23;border-color:#818cf8}
        .ag-pos-btn:hover:not(.active){border-color:#818cf8;color:#818cf8}
        .ag-site-info{background:#232436;padding:10px 14px;margin-bottom:10px;border:1px solid #2e3047}
        .ag-site-row{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px}
        .ag-site-row:last-child{margin-bottom:0}
        .ag-site-label{color:#71717a;min-width:56px;flex-shrink:0}
        .ag-site-value{color:#d4d4d8;word-break:break-all}
        .ag-site-badge{font-size:10px;padding:1px 6px;flex-shrink:0;border-radius:0}
        .ag-badge-ok{background:rgba(134,239,172,.12);color:#86efac}
        .ag-badge-fail{background:rgba(244,114,182,.12);color:#f472b6}
        .ag-site-actions{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
        .ag-hint{font-size:11px;color:#52525b;margin-top:2px}

        #agent-pick-dim{position:fixed;inset:0;background:rgba(0,0,0,.28);z-index:2147483645;pointer-events:none}
        #agent-pick-hl{position:fixed;border:2.5px solid #818cf8;background:rgba(129,140,248,.08);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;box-shadow:0 0 0 4000px rgba(0,0,0,.25);display:none}
        #agent-pick-lock-hl{position:fixed;border:2.5px solid #f472b6;background:rgba(244,114,182,.06);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;display:none}
        #agent-pick-tip{position:fixed;background:#1a1b2e;color:#c4b5fd;border:1px solid #3f3f46;padding:5px 10px;border-radius:0;font:11px/1.4 'SF Mono',Consolas,monospace;z-index:2147483647;pointer-events:none;max-width:560px;word-break:break-all;box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .08s;display:flex;flex-direction:column;gap:3px}
        .ag-tip-sel{display:flex;align-items:center;gap:0;flex-wrap:wrap}
        .ag-diag-line{display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:10px;color:#71717a;border-top:1px solid #2e3047;padding-top:3px}
        .ag-diag-text{color:#86efac;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
        .ag-diag-children{color:#a1a1aa}
        .ag-diag-size{color:#52525b}
        .ag-diag-ok{color:#86efac}
        .ag-diag-warn{color:#facc15}
        .ag-diag-err{color:#f472b6}
        .ag-diag-shadow{color:#818cf8;background:rgba(129,140,248,.15);padding:0 4px}
        .ag-diag-scroll{color:#facc15;background:rgba(250,204,21,.1);padding:0 4px}
        .ag-diag-sep{color:#3f3f46;margin:0 1px}

        #agent-pick-bar{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#1a1b2e;color:#d4d4d8;border:1px solid #818cf8;padding:10px 28px;border-radius:0;font-size:14px;z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.5);pointer-events:none}
        #agent-pick-level{color:#86efac; margin-left: 8px; font-weight: bold;}

        #ag-show-levels{pointer-events:auto;cursor:pointer;color:#f472b6;margin-right:8px;border-right:1px solid #3f3f46;padding-right:8px;white-space:nowrap;flex-shrink:0;transition:color .1s}
        #ag-show-levels:hover{color:#fda4af}
        .ag-level-panel{position:fixed;width:min(420px,85vw);max-height:340px;background:#1a1b2e;border:1px solid #f472b6;border-radius:0;box-shadow:0 8px 32px rgba(0,0,0,.55);z-index:2147483647;display:none;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace;color:#d4d4d8;}
        .ag-level-head{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid #2e3047;font-weight:600;color:#f472b6;flex-shrink:0;}
        .ag-level-head button{background:none;border:none;color:#71717a;cursor:pointer;font-size:16px;padding:0 4px;}
        .ag-level-head button:hover{color:#f472b6}
        .ag-level-body{flex:1;overflow-y:auto;padding:4px;}
        .ag-level-body::-webkit-scrollbar{width:4px}
        .ag-level-body::-webkit-scrollbar-thumb{background:#3f3f46;border-radius:0}
        .ag-level-item{display:flex;align-items:center;gap:6px;padding:6px 8px;cursor:pointer;border-left:2px solid transparent;transition:background .1s;}
        .ag-level-item:hover{background:rgba(244,114,182,.1);border-left-color:#f472b6;}
        .ag-level-target{background:rgba(244,114,182,.06);border-left-color:#f472b6;}
        .ag-level-idx{color:#52525b;font-size:10px;min-width:18px;text-align:right;flex-shrink:0;}
        .ag-level-tag{color:#86efac;font-weight:600;min-width:60px;flex-shrink:0;}
        .ag-level-digest{color:#93c5fd;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:italic;}
        .ag-level-sel{color:#52525b;font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;}

        #agent-debug{position:fixed;top:10px;right:10px;width:380px;max-height:60vh;background:rgba(15,15,30,.92);border:1px solid #3f3f46;border-radius:0;box-shadow:0 10px 40px rgba(0,0,0,.5);z-index:2147483644;display:flex;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace;backdrop-filter:blur(8px);color:#a1a1aa;}
        #agent-debug-head{padding:8px 12px;border-bottom:1px solid #2e3047;display:flex;justify-content:space-between;align-items:center;color:#d4d4d8}
        #agent-debug-body{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:4px}
        #agent-debug-body::-webkit-scrollbar{width:4px}
        #agent-debug-body::-webkit-scrollbar-thumb{background:#3f3f46;border-radius:0}
        .ag-log{padding:4px 6px;border-radius:0;word-break:break-all;background:rgba(255,255,255,.03);border-left:3px solid transparent}
        .ag-log-time{color:#52525b;margin-right:6px}
        .ag-log-info{border-left-color:#818cf8;color:#c4b5fd}
        .ag-log-warn{border-left-color:#facc15;color:#fde68a;background:rgba(250,204,21,.05)}
        .ag-log-err{border-left-color:#f472b6;color:#fda4af;background:rgba(244,114,182,.05)}
        .ag-log-ok{border-left-color:#86efac;color:#bbf7d0;background:rgba(134,239,172,.05)}
        #agent-debug-foot{padding:6px 12px;border-top:1px solid #2e3047;text-align:right}
        .ag-dbg-btn{background:#2e3047;border:1px solid #3f3f46;color:#a1a1aa;padding:3px 10px;border-radius:0;cursor:pointer;font-size:11px}
        .ag-dbg-btn:hover{border-color:#818cf8;color:#818cf8}

        #agent-auto-send-toggle{position:fixed;z-index:2147483640;display:flex;flex-direction:row;align-items:stretch;background:#1a1b2e;border:1px solid #3f3f46;pointer-events:auto;white-space:nowrap;user-select:none;opacity:0.85;transition:opacity .15s;}
        #agent-auto-send-toggle:hover{opacity:1}
        .ag-as-opts{display:flex;flex-direction:column;padding:4px 4px 4px 8px}
        .ag-as-opt{font-size:10px;color:#52525b;cursor:pointer;padding:5px 2px;line-height:1.3;transition:color .15s;font-family:system-ui,sans-serif}
        .ag-as-opt:hover{color:#a1a1aa}
        .ag-as-opt.active{color:#818cf8}
        .ag-as-rail{width:16px;position:relative;display:flex;justify-content:center;border-left:1px solid #2e3047;padding:4px 0}
        .ag-as-rail::before{content:'';position:absolute;top:8px;bottom:8px;width:2px;background:#3f3f46}
        .ag-as-thumb{position:absolute;left:50%;transform:translateX(-50%);width:10px;height:10px;background:#818cf8;transition:top .25s ease;z-index:1}

        #ag-calibrate-bar{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#1a1b2e;color:#fde68a;border:1px solid #facc15;padding:14px 24px;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);font:13px/1.5 system-ui,sans-serif;display:none;flex-direction:column;align-items:center;gap:10px;pointer-events:auto;width:min(600px,90vw)}
        #ag-calibrate-bar b{color:#facc15}
        #ag-calibrate-cards{position:fixed;top:100px;left:50%;transform:translateX(-50%);background:#1a1b2e;border:1px solid #3f3f46;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);width:min(220px,45vw);overflow-y:auto;overflow-x:hidden;padding:10px;cursor:move}
        #ag-calibrate-cards::-webkit-scrollbar{width:4px}
        #ag-calibrate-cards::-webkit-scrollbar-thumb{background:#3f3f46}
        .ag-cal-item{display:flex;flex-direction:column;align-items:center;gap:6px;padding:6px;background:#232436;transition:.15s;width:100%;box-sizing:border-box;overflow:hidden;position:relative;z-index:0;border:1px solid transparent;min-height:0}
        .ag-cal-item.selected-busy{background:rgba(244,114,182,.1);border-color:#f472b6}
        .ag-cal-item.selected-idle{background:rgba(134,239,172,.1);border-color:#86efac}
        .ag-cal-clone{width:100%;height:60px;max-height:60px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#0f0f23}
        .ag-cal-actions{display:flex;flex-direction:column;gap:4px;width:100%;align-items:center}
        .ag-cal-tag{font-size:10px;padding:2px 8px;border:1px solid #3f3f46;color:#71717a;cursor:pointer;background:none;white-space:nowrap;width:60%;text-align:center}
        .ag-cal-tag:hover{border-color:#818cf8;color:#818cf8}
        .ag-cal-tag.active-busy{border-color:#f472b6;color:#f472b6;background:rgba(244,114,182,.2)}
        .ag-cal-tag.active-idle{border-color:#86efac;color:#86efac;background:rgba(134,239,172,.2)}
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
            <div id="agent-debug-head">
                <span>🕵️ Agent 调试台</span>
                <button class="ag-dbg-btn" id="ag-dbg-close">隐藏</button>
            </div>
            <div id="agent-debug-body"></div>
            <div id="agent-debug-foot">
                <button class="ag-dbg-btn" id="ag-dbg-clear">清空日志</button>
            </div>`;
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
        return str.length > maxDisplay ? str.substring(0, keepLen) + `... (共 ${str.length} 字符)` : str;
    }

    function log(type, msg) {
        const c = cfgLoad();
        console.log(`[Agent-${type}] ${msg}`);
        if (!c.debugMode || !_debugBody || _debugPanel.style.display === 'none') return;
        const cls = type === 'INFO' ? 'ag-log-info' : type === 'WARN' ? 'ag-log-warn' : type === 'ERR' ? 'ag-log-err' : type === 'OK' ? 'ag-log-ok' : '';
        const time = new Date().toLocaleTimeString('zh-CN', { hour12: false });
        const div = document.createElement('div');
        div.className = `ag-log ${cls}`;
        div.innerHTML = `<span class="ag-log-time">${time}</span>${esc(_truncate(msg))}`;
        _debugBody.appendChild(div);
        _debugBody.scrollTop = _debugBody.scrollHeight;
    }

    /* ================================================================
     * 4. 元素选择器
     * ================================================================ */
    const PICKER_IDS = new Set([
        'agent-pick-dim', 'agent-pick-hl', 'agent-pick-lock-hl', 'agent-pick-tip',
        'agent-pick-bar', 'agent-panel', 'agent-debug', 'agent-auto-send-toggle',
        'ag-level-panel', 'ag-calibrate-bar'
    ]);

    let _pickActive = false, _pickType = '';
    let _pickHL, _pickTip, _pickBar, _pickDim;
    let _pickLockHL = null;
    let _lockedBaseEl = null;
    let _pickedEl = null;
    let _domStack = [];
    let _levelPanel = null;

    const TYPE_LABEL = {
        chat: '聊天记录容器', input: '输入框', send: '发送按钮',
        answer: 'AI回答元素', 'clean-class': '清理元素Class'
    };

    function _isPureHashClass(c) {
        if (/^[a-f0-9]{5,}$/i.test(c)) return true;
        if (/^(css|sc|emotion|styled)-[a-z0-9]{4,}$/i.test(c)) return true;
        return false;
    }

    function _stripClassHash(c) {
        return c.replace(/[_-][a-f0-9]{5,8}$/i, '');
    }

    function genSelector(el) {
        if (!el || el === document.body || el === document.documentElement) return '';

        if (el.id && !/\d/.test(el.id)) {
            const sel = '#' + CSS.escape(el.id);
            try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
        }

        for (const attr of ['data-testid', 'data-test-id', 'data-role', 'data-cy']) {
            const val = el.getAttribute(attr);
            if (val) {
                const sel = `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(val)}"]`;
                try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
            }
        }

        const role = el.getAttribute('role');
        if (role) {
            const sel = `${el.tagName.toLowerCase()}[role="${CSS.escape(role)}"]`;
            try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
        }

        if (el.className && typeof el.className === 'string') {
            const allCls = el.className.trim().split(/\s+/).filter(c => c);
            const cleanCls = allCls.filter(c => !_isPureHashClass(c) && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c) && !/[_-][a-f0-9]{5,8}$/i.test(c));
            if (cleanCls.length) {
                const sel = `${el.tagName.toLowerCase()}.${cleanCls.map(c => CSS.escape(c)).join('.')}`;
                try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
            }

            const hashCls = allCls.filter(c => !_isPureHashClass(c) && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c) && /[_-][a-f0-9]{5,8}$/i.test(c));
            if (hashCls.length) {
                const stripped = hashCls.map(c => _stripClassHash(c)).filter(s => s.length >= 3);
                if (stripped.length) {
                    const sel = `${el.tagName.toLowerCase()}${stripped.map(s => `[class*="${CSS.escape(s)}"]`).join('')}`;
                    try { if (document.querySelectorAll(sel).length === 1) return sel; } catch (_) {}
                }
            }
        }

        const segs = [];
        let cur = el;
        while (cur && cur !== document.body && cur !== document.documentElement && segs.length < 5) {
            let seg = cur.tagName.toLowerCase();
            if (cur.id && !/\d/.test(cur.id)) { segs.unshift('#' + CSS.escape(cur.id)); break; }
            if (cur.className && typeof cur.className === 'string') {
                const cls = cur.className.trim().split(/\s+/)
                    .filter(c => c && !_isPureHashClass(c) && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c))
                    .slice(0, 3);
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
        _pickedEl = null;
        _lockedBaseEl = null;
        _domStack = [];
        hidePanel();

        _pickDim = document.createElement('div'); _pickDim.id = 'agent-pick-dim'; document.body.appendChild(_pickDim);
        _pickHL = document.createElement('div'); _pickHL.id = 'agent-pick-hl'; document.body.appendChild(_pickHL);
        _pickLockHL = document.createElement('div'); _pickLockHL.id = 'agent-pick-lock-hl'; document.body.appendChild(_pickLockHL);
        _pickTip = document.createElement('div'); _pickTip.id = 'agent-pick-tip'; document.body.appendChild(_pickTip);
        _pickBar = document.createElement('div'); _pickBar.id = 'agent-pick-bar';
        _pickBar.innerHTML = `🎯 选择 <span class="ag-target">${TYPE_LABEL[type]}</span> | <span style="font-size:12px;opacity:0.7">左键↑ 右键↓ Shift+点击确认</span>`;
        document.body.appendChild(_pickBar);

        document.addEventListener('mousemove', _onMove, true);
        document.addEventListener('click', _onClick, true);
        document.addEventListener('contextmenu', _onCtx, true);
        document.addEventListener('keydown', _onKey, true);
        document.addEventListener('scroll', _syncHighlightPositions, true);
        window.addEventListener('resize', _syncHighlightPositions);
    }

    function pickerExit() {
        _pickActive = false;
        _pickType = '';
        _pickedEl = null;
        _lockedBaseEl = null;
        _domStack = null;
        document.removeEventListener('mousemove', _onMove, true);
        document.removeEventListener('click', _onClick, true);
        document.removeEventListener('contextmenu', _onCtx, true);
        document.removeEventListener('keydown', _onKey, true);
        document.removeEventListener('scroll', _syncHighlightPositions, true);
        window.removeEventListener('resize', _syncHighlightPositions);
        [_pickDim, _pickHL, _pickLockHL, _pickTip, _pickBar, _levelPanel].forEach(e => e && e.remove());
        _pickLockHL = null;
        _levelPanel = null;
        showPanel();
    }

    function _targetAt(x, y) {
        let el = document.elementFromPoint(x, y);
        while (el && el.shadowRoot) {
            const inner = el.shadowRoot.elementFromPoint(x, y);
            if (!inner || inner === el) break;
            el = inner;
        }
        while (el && PICKER_IDS.has(el.id)) el = el.parentElement;
        return el;
    }

    function _onMove(e) {
        e.stopPropagation();
        if (!_pickedEl) {
            const el = _targetAt(e.clientX, e.clientY);
            if (el) _highlightEl(el, e.clientX, e.clientY);
        } else {
            _updateLockHL();
        }
    }

    function _getElementDigest(el) {
        const tag = el.tagName.toLowerCase();
        if (['input','textarea','select'].includes(tag)) {
            const v = el.value;
            return v ? `${tag}[value="${v.slice(0,50)}"]` : `${tag}`;
        }
        if (tag === 'img') {
            const alt = el.alt ? `alt="${el.alt.slice(0,30)}"` : '';
            const src = el.src ? el.src.split('/').pop().slice(0,40) : '';
            return `img${alt ? ' '+alt : ''}${src ? ' src=…/'+src : ''}`;
        }
        let text = '';
        for (const node of el.childNodes) {
            if (node.nodeType === Node.TEXT_NODE) text += node.textContent;
        }
        text = text.replace(/\s+/g, ' ').trim().slice(0, 60);
        if (text) return `${tag}: "${text}"`;
        return tag;
    }

    function _highlightEl(el, mouseX, mouseY) {
        const r = el.getBoundingClientRect();
        _pickHL.style.display = 'block';
        Object.assign(_pickHL.style, { left: (r.left-2)+'px', top: (r.top-2)+'px', width: (r.width+4)+'px', height: (r.height+4)+'px' });

        const sel = genSelector(el);
        const digest = _getElementDigest(el);
        const tag = el.tagName.toLowerCase();
        const digestStr = digest !== tag ? ` <span style="color:#888">${esc(digest)}</span>` : '';

        const diagParts = [];
        const textPreview = (el.textContent || '').replace(/\s+/g, ' ').trim().slice(0, 60);
        if (textPreview) diagParts.push(`<span class="ag-diag-text">"${esc(textPreview)}"</span>`);
        const childCount = el.children.length;
        if (childCount > 0) diagParts.push(`<span class="ag-diag-children">子:${childCount}</span>`);
        const isScrollY = el.scrollHeight > el.clientHeight + 2;
        const isScrollX = el.scrollWidth > el.clientWidth + 2;
        if (isScrollY || isScrollX) {
            const dir = isScrollY && isScrollX ? 'xy' : isScrollY ? 'y' : 'x';
            diagParts.push(`<span class="ag-diag-scroll">可滚动(${dir})</span>`);
        }
        const w = Math.round(r.width), h = Math.round(r.height);
        diagParts.push(`<span class="ag-diag-size">${w}×${h}</span>`);
        if (el.shadowRoot) diagParts.push(`<span class="ag-diag-shadow">ShadowDOM</span>`);

        const diagHtml = diagParts.length ? `<div class="ag-diag-line">${diagParts.join('<span class="ag-diag-sep">|</span>')}</div>` : '';

        _pickTip.innerHTML = _lockedBaseEl ? 
            `<span class="ag-tip-sel"><span id="ag-show-levels">展开所有层级</span><span>${esc(sel)} ←${tag}${digestStr}</span></span>${diagHtml}` : 
            `<span class="ag-tip-sel"><span>${esc(sel)} ←${tag}${digestStr}</span></span>${diagHtml}`;
        _pickTip.style.opacity = '1';
        _pickTip.style.left = Math.min(mouseX + 14, innerWidth - 510) + 'px';
        _pickTip.style.top = (mouseY + 22) + 'px';
    }

    function _updateLockHL() {
        if (!_pickLockHL || !_lockedBaseEl) return;
        const r = _lockedBaseEl.getBoundingClientRect();
        _pickLockHL.style.display = 'block';
        Object.assign(_pickLockHL.style, { left: (r.left-2)+'px', top: (r.top-2)+'px', width: (r.width+4)+'px', height: (r.height+4)+'px' });
    }

    function _syncHighlightPositions() {
        if (_pickHL && _pickedEl) {
            const r = _pickedEl.getBoundingClientRect();
            Object.assign(_pickHL.style, { left: (r.left - 2) + 'px', top: (r.top - 2) + 'px', width: (r.width + 4) + 'px', height: (r.height + 4) + 'px' });
        }
        if (_pickLockHL && _lockedBaseEl) {
            const r = _lockedBaseEl.getBoundingClientRect();
            Object.assign(_pickLockHL.style, { left: (r.left - 2) + 'px', top: (r.top - 2) + 'px', width: (r.width + 4) + 'px', height: (r.height + 4) + 'px' });
        }
    }

    function _hideLockHL() {
        if (_pickLockHL) _pickLockHL.style.display = 'none';
    }

    function _showLevelPanel() {
        if (!_lockedBaseEl) return;
        if (!_levelPanel) {
            _levelPanel = document.createElement('div');
            _levelPanel.id = 'ag-level-panel';
            _levelPanel.className = 'ag-level-panel';
            document.body.appendChild(_levelPanel);
        }

        const chain = [];
        let cur = _lockedBaseEl;
        while (cur && cur !== document.documentElement) { chain.unshift(cur); cur = cur.parentElement; }
        if (chain.length > 0 && chain[0] !== document.documentElement && chain[0].parentElement === document.documentElement) chain.unshift(document.documentElement);

        let html = '<div class="ag-level-head"><span>📐 层级结构 (点击选择)</span><button id="ag-level-close">✕</button></div><div class="ag-level-body">';
        chain.forEach((el, i) => {
            const sel = genSelector(el) || '(无法生成)';
            const tag = el.tagName.toLowerCase();
            const digest = _getElementDigest(el);
            const digestStr = digest !== tag ? digest : '';
            const isTarget = el === _lockedBaseEl;
            html += `<div class="ag-level-item ${isTarget ? 'ag-level-target' : ''}" data-idx="${i}"><span class="ag-level-idx">${i}</span><span class="ag-level-tag">&lt;${tag}&gt;</span><span class="ag-level-digest">${esc(digestStr)}</span><span class="ag-level-sel" title="${esc(sel)}">${esc(sel)}</span></div>`;
        });
        html += '</div>';

        _levelPanel.innerHTML = html;
        const tipRect = _pickTip.getBoundingClientRect();
        let left = tipRect.left, top = tipRect.bottom + 6;
        if (left + 420 > innerWidth) left = innerWidth - 430;
        if (left < 10) left = 10;
        if (top + 200 > innerHeight) top = tipRect.top - 346;
        if (top < 10) top = 10;
        _levelPanel.style.left = left + 'px';
        _levelPanel.style.top = top + 'px';
        _levelPanel.style.display = 'flex';

        _levelPanel.querySelector('#ag-level-close').onclick = (e) => { e.stopPropagation(); _levelPanel.style.display = 'none'; };
        _levelPanel.querySelectorAll('.ag-level-item').forEach(item => {
            item.onclick = (e) => { e.stopPropagation(); _confirmSelection(chain[+item.dataset.idx]); };
        });
    }

    function _onClick(e) {
        if (_levelPanel && _levelPanel.style.display !== 'none' && _levelPanel.contains(e.target)) return;
        e.stopPropagation(); e.preventDefault();

        if (_levelPanel && _levelPanel.style.display !== 'none') { _levelPanel.style.display = 'none'; return; }
        if (e.target.id === 'ag-show-levels') { _showLevelPanel(); return; }

        if (e.shiftKey || e.ctrlKey) {
            if (_pickedEl) _confirmSelection(_pickedEl);
            else { const el = _targetAt(e.clientX, e.clientY); if (el) _confirmSelection(el); }
            return;
        }

        const target = _targetAt(e.clientX, e.clientY);
        if (!target) return;

        if (!_pickedEl) {
            _pickedEl = target;
            _lockedBaseEl = target;
            _domStack = [];
            _updateLockHL();
            log('INFO', `锁定起点: <${target.tagName.toLowerCase()}>`);
        } else {
            if (_lockedBaseEl.contains(target)) {
                if (_pickedEl.parentElement && _pickedEl.parentElement !== document.body) {
                    _domStack.push(_pickedEl);
                    _pickedEl = _pickedEl.parentElement;
                    log('INFO', `向上穿透至: <${_pickedEl.tagName.toLowerCase()}> (栈深度: ${_domStack.length})`);
                } else log('WARN', '已到达顶层 body，无法继续向上');
            } else {
                _hideLockHL();
                _domStack = [];
                _lockedBaseEl = target;
                _pickedEl = target;
                _updateLockHL();
                log('INFO', `点击超出锁定范围，重新选择: <${target.tagName.toLowerCase()}>`);
            }
        }
        _highlightEl(_pickedEl, e.clientX, e.clientY);
        _updateBarInfo();
    }

    function _onCtx(e) {
        if (_levelPanel && _levelPanel.style.display !== 'none' && _levelPanel.contains(e.target)) return;
        e.stopPropagation(); e.preventDefault();

        if (_levelPanel && _levelPanel.style.display !== 'none') { _levelPanel.style.display = 'none'; return; }

        if (!_pickedEl) return;
        const target = _targetAt(e.clientX, e.clientY);

        if (!target || !_lockedBaseEl.contains(target)) {
            _hideLockHL();
            _domStack = [];
            _lockedBaseEl = target || null;
            _pickedEl = target || null;
            if (_pickedEl) {
                _updateLockHL();
                log('INFO', `右键超出锁定范围，重新选择: <${_pickedEl.tagName.toLowerCase()}>`);
                _highlightEl(_pickedEl, e.clientX, e.clientY);
            } else _pickHL.style.display = 'none';
            _updateBarInfo();
            return;
        }

        if (_domStack.length > 0) {
            _pickedEl = _domStack.pop();
            log('INFO', `向下回退至: <${_pickedEl.tagName.toLowerCase()}> (栈深度: ${_domStack.length})`);
            _highlightEl(_pickedEl, e.clientX, e.clientY);
            _updateBarInfo();
        } else log('WARN', '已在最底层，无法回退');
    }

    function _updateBarInfo() {
        if (!_pickBar || !_pickedEl) return;
        _pickBar.innerHTML = `🎯 当前层级: <span style="color:#86efac">${_domStack.length}</span> (${_pickedEl.tagName.toLowerCase()}) | <span style="font-size:12px;opacity:0.7">左键↑ 右键↓ Shift+点击确认</span>`;
    }

    function _confirmSelection(el) {
        if (_pickType === 'clean-class') {
            let classes = [];
            if (el.className && typeof el.className === 'string') {
                classes = el.className.trim().split(/\s+/).filter(c => c && !_isPureHashClass(c) && !/^(_|-{2})/.test(c) );
            }
            if (classes.length === 0) { log('WARN', '该元素没有有效的class，请重新选择'); pickerExit(); return; }

            const c = cfgLoad();
            let currentKws = (c.cleanIgnoreClassKeywords || '').split(',').map(s => s.trim()).filter(s => s);
            let added = [];
            classes.forEach(cls => { if (!currentKws.includes(cls)) { currentKws.push(cls); added.push(cls); } });
            cfgSaveRuntime({ cleanIgnoreClassKeywords: currentKws.join(',') });
            log('OK', `已添加Class关键词: ${added.join(', ')}`);
            pickerExit();
            return;
        }

        const sel = genSelector(el);
        if (!sel) { log('ERR', '无法生成选择器'); return; }

        const c = cfgLoad();
        if (_pickType === 'chat') c.selChatContainer = sel;
        if (_pickType === 'input') c.selInputBox = sel;
        if (_pickType === 'send') c.selSendButton = sel;
        if (_pickType === 'answer') c.selAnswerItem = sel;
        cfgSaveRuntime(c);
        log('OK', `已选择 [${TYPE_LABEL[_pickType]}]:${sel}`);

        const ctxChain = [];
        let cur = el;
        while (cur && cur !== document.documentElement) {
            const tag = cur.tagName ? cur.tagName.toLowerCase() : '#document';
            const digest = _getElementDigest(cur);
            ctxChain.push(`${tag}${digest !== tag ? '('+digest+')' : ''}`);
            cur = cur.parentElement;
        }
        log('INFO', `上下文链: ${ctxChain.reverse().join(' > ')}`);
        log('INFO', `目标元素详情: <${el.tagName.toLowerCase()}>, class="${el.className}", id="${el.id}"`);
        pickerExit();
    }

    function _onKey(e) {
        if (e.key === 'Escape') { e.stopPropagation(); e.preventDefault(); pickerExit(); }
        if (e.key === 'Enter' && _pickedEl) { e.stopPropagation(); e.preventDefault(); _confirmSelection(_pickedEl); }
    }

    /* ================================================================
     * 5. 配置面板
     * ================================================================ */
    let _panel = null;

    function showPanel() {
        if (!_panel) {
            _panel = document.createElement('div');
            _panel.id = 'agent-panel';
            document.body.appendChild(_panel);
        }
        const store = _loadStore();
        const site = _matchSite();
        const hasSiteCfg = site && store.perSite && store.perSite[site];
        _editTarget = hasSiteCfg ? site : 'defaults';
        _renderPanel();
        _panel.style.display = 'block';
    }

    function hidePanel() {
        if (_panel) _panel.style.display = 'none';
    }

    function _renderPanel() {
        const store = _loadStore();
        const site = _matchSite();
        const inWhitelist = !!site;
        const hasSiteCfg = site && store.perSite && store.perSite[site];
        const source = _getConfigSource();

        let editCfg;
        if (_editTarget === 'defaults') editCfg = { ...SITE_DEFAULTS, ...(store.defaults || {}) };
        else editCfg = { ...SITE_DEFAULTS, ...(store.perSite?.[_editTarget] || {}) };

        const titleText = _editTarget === 'defaults' ? '🔧 Poker Agent 配置 — 默认设置' : `🔧 Poker Agent 配置 — ${_editTarget} 独立设置`;
        const saveText = _editTarget === 'defaults' ? '💾 保存默认配置' : `💾 保存独立配置`;
        const siteDisplay = site || location.hostname;
        const sourceDisplay = source === 'defaults' ? '默认配置' : `${source} 独立配置`;
        const badgeClass = inWhitelist ? 'ag-badge-ok' : 'ag-badge-fail';
        const badgeText = inWhitelist ? '在白名单内' : '不在白名单内';

        let actionsHtml = '';
        const defActive = _editTarget === 'defaults';
        actionsHtml += `<button class="ag-btn ${defActive ? 'ag-btn-p' : 'ag-btn-g'}" id="ag-edit-defaults">编辑默认配置</button>`;
        if (inWhitelist) {
            if (hasSiteCfg) {
                const siteActive = _editTarget === site;
                actionsHtml += `<button class="ag-btn ${siteActive ? 'ag-btn-p' : 'ag-btn-g'}" id="ag-edit-site">编辑当前网站配置</button>`;
                actionsHtml += `<button class="ag-btn ag-btn-g" id="ag-del-site" style="color:#f472b6">删除独立配置</button>`;
            } else {
                actionsHtml += `<button class="ag-btn ag-btn-g" id="ag-create-site">为此网站创建独立配置</button>`;
            }
        }

        _panel.innerHTML = `
            <div id="agent-panel-head"><b>${titleText}</b><button id="agent-panel-close">✕</button></div>
            <div id="agent-panel-body">
                <div class="ag-site-info">
                    <div class="ag-site-row"><span class="ag-site-label">当前网站:</span><span class="ag-site-value">${esc(siteDisplay)}</span><span class="ag-site-badge ${badgeClass}">${badgeText}</span></div>
                    <div class="ag-site-row"><span class="ag-site-label">当前使用:</span><span class="ag-site-value" style="color:#818cf8">${esc(sourceDisplay)}</span></div>
                </div>
                <div class="ag-site-actions">${actionsHtml}</div>

                <div class="ag-sec"><div class="ag-sec-title">控制台</div><div class="ag-toggle"><input type="checkbox" id="ag-debug-toggle" ${store.debugMode ? 'checked' : ''} /><label for="ag-debug-toggle" style="cursor:pointer">启用调试模式 (右侧显示日志浮窗)</label></div></div>

                <div class="ag-sec"><div class="ag-sec-title">网站白名单</div><div class="ag-wl-list" id="ag-wl-list">${store.whitelist.length ? store.whitelist.map((u, i) => `<div class="ag-wl-item"><code>${esc(u)}</code><button class="ag-wl-rm" data-i="${i}">✕</button></div>`).join('') : '<div style="padding:8px 10px;color:#52525b;font-size:12px">暂无</div>'}</div><div class="ag-row"><input class="ag-inp" id="ag-wl-new" placeholder="https://example.com/" /><button class="ag-btn ag-btn-g" id="ag-wl-add">添加</button></div></div>

                <div class="ag-sec"><div class="ag-sec-title">本地 Agent 服务</div><div class="ag-field"><label>接收指令的 HTTP 地址</label><input class="ag-inp" id="ag-api" value="${esc(editCfg.apiUrl)}" /></div></div>

                <div class="ag-sec">
                    <div class="ag-sec-title">页面元素绑定</div>
                    <div class="ag-field"><label>聊天记录容器</label><div class="ag-row"><input class="ag-inp" id="ag-s-chat" value="${esc(editCfg.selChatContainer)}" /><button class="ag-btn ag-btn-p" id="ag-pick-chat">🖱 选择</button></div><div id="ag-m-chat"></div></div>
                    <div class="ag-field"><label>AI回答元素</label><div class="ag-row"><input class="ag-inp" id="ag-s-answer" value="${esc(editCfg.selAnswerItem)}" /><button class="ag-btn ag-btn-p" id="ag-pick-answer">🖱 选择</button></div><div id="ag-m-answer"></div><div class="ag-hint">用于从聊天容器中定位AI的回复，默认 .answer；如不匹配请用选择器选取</div></div>
                    <div class="ag-field"><label>输入框</label><div class="ag-row"><input class="ag-inp" id="ag-s-input" value="${esc(editCfg.selInputBox)}" /><button class="ag-btn ag-btn-p" id="ag-pick-input">🖱 选择</button></div><div id="ag-m-input"></div></div>
                    <div class="ag-field">
                        <label>发送按钮</label><div class="ag-row"><input class="ag-inp" id="ag-s-send" value="${esc(editCfg.selSendButton)}" /><button class="ag-btn ag-btn-p" id="ag-pick-send">🖱 选择</button></div><div id="ag-m-send"></div>
                        <div class="ag-field" id="ag-calibrate-field" style="margin-top:6px; padding:8px; background:#232436; border:1px solid #2e3047; display:${editCfg.selSendButton ? 'block' : 'none'};">
                            <div style="font-size:12px; color:#a1a1aa; margin-bottom:6px">捕获按钮的各种形态，手动标记【忙碌】(AI输出时)和【空闲】态。</div>
                            <div class="ag-row"><div id="ag-calibrate-status" style="flex:1; font-size:11px; color:#52525b"> 忙碌:${(editCfg.sendBtnBusyFingerprints||[]).length}个 | 空闲:${(editCfg.sendBtnIdleFingerprints||[]).length}个 </div><button class="ag-btn ag-btn-p" id="ag-start-calibrate">${(editCfg.sendBtnBusyFingerprints||[]).length > 0 ? '重新校准' : '开始校准'}</button></div>
                        </div>
                    </div>
                    <div class="ag-field" style="margin-top:12px;padding-top:10px;border-top:1px solid #2e3047">
                        <label>输出完毕判断逻辑</label>
                        <div class="ag-row" style="margin-bottom:6px">
                            <input type="radio" name="verifyMode" id="ag-mode-single" value="single" ${editCfg.verifyMode !== 'double' ? 'checked' : ''} />
                            <label for="ag-mode-single" style="font-size:12px;cursor:pointer;margin-right:12px">单验证 (脱离忙碌即放行)</label>
                            <input type="radio" name="verifyMode" id="ag-mode-double" value="double" ${editCfg.verifyMode === 'double' ? 'checked' : ''} />
                            <label for="ag-mode-double" style="font-size:12px;cursor:pointer">双验证 (需进入空闲态)</label>
                        </div>
                        <div class="ag-row">
                            <label style="font-size:12px;color:#71717a;white-space:nowrap">放行前额外延时</label>
                            <input class="ag-inp" id="ag-wait-delay" type="number" value="${editCfg.waitDelayAfterDone || 500}" style="width:80px" />
                        </div>
                    </div>
                    <label>发送模式选择器</label>
                    <div class="ag-toggle" style="margin-bottom:6px">
                        <input type="checkbox" id="ag-show-toggle" ${editCfg.showAutoSendToggle ? 'checked' : ''} />
                        <label for="ag-show-toggle" style="cursor:pointer">在发送按钮旁显示发送模式选择器</label>
                    </div>
                    <div class="ag-row">
                        <label style="font-size:11px;color:#71717a;white-space:nowrap">位置</label>
                        <div class="ag-pos-group">
                            <button class="ag-pos-btn" data-pos="left">← 左</button>
                            <button class="ag-pos-btn" data-pos="top">↑ 上</button>
                            <button class="ag-pos-btn" data-pos="right">→ 右</button>
                            <button class="ag-pos-btn" data-pos="bottom">↓ 下</button>
                        </div>
                    </div>
                </div>
                </div>

                <div class="ag-sec" >
                    <div class="ag-sec-title" >内容清理规则 </div >
                    <div class="ag-field" >
                        <label >忽略的class关键词 (逗号分隔) </label >
                        <div class="ag-row" >
                            <input class="ag-inp" id="ag-clean-keywords" value="${esc(editCfg.cleanIgnoreClassKeywords)}" />
                            <button class="ag-btn ag-btn-p" id="ag-pick-clean-keyword" >🖱 选择 </button >
                        </div >
                        <div class="ag-hint" >包含这些关键词的class所在元素会被移除，支持用选择器直接抓取行号等干扰元素的class </div >
                    </div >
                    <div class="ag-toggle" style="margin-bottom:6px">
                        <input type="checkbox" id="ag-clean-buttons" ${editCfg.cleanRemoveButtonLike !== false ? 'checked' : ''} />
                        <label for="ag-clean-buttons" style="cursor:pointer">移除按钮/操作类元素 (copy/operate/action/toolbar)</label>
                    </div>
                    <div class="ag-toggle">
                        <input type="checkbox" id="ag-clean-pre" ${editCfg.cleanRemovePre !== false ? 'checked' : ''} />
                        <label for="ag-clean-pre" style="cursor:pointer">移除pre代码块 (除非含【CodeSTART】)</label>
                    </div>
                </div>
            </div>
            <div class="ag-foot"><button class="ag-btn ag-btn-g" id="ag-cancel">取消</button><button class="ag-btn ag-btn-p" id="ag-save">${saveText}</button></div>`;

        _panel.querySelector('#agent-panel-close').onclick = hidePanel;
        _panel.querySelector('#ag-cancel').onclick = hidePanel;

        _panel.querySelector('#ag-debug-toggle').onchange = (e) => {
            const s = _loadStore();
            s.debugMode = e.target.checked;
            _saveStore(s);
            if (e.target.checked) { showDebug(); log('INFO', '调试模式已开启'); }
            else { if (_debugPanel) _debugPanel.style.display = 'none'; }
        };

        const wlInput = _panel.querySelector('#ag-wl-new');
        const doAdd = () => {
            const v = wlInput.value.trim();
            if (!v) return;
            const s = _loadStore();
            if (!s.whitelist.includes(v)) s.whitelist.push(v);
            _saveStore(s);
            wlInput.value = '';
            _renderPanel();
        };
        _panel.querySelector('#ag-wl-add').onclick = doAdd;
        wlInput.onkeydown = e => { if (e.key === 'Enter') doAdd(); };

        _panel.querySelectorAll('.ag-wl-rm').forEach(btn => {
            btn.onclick = () => {
                const s = _loadStore();
                s.whitelist.splice(+btn.dataset.i, 1);
                _saveStore(s);
                _renderPanel();
            };
        });

        _panel.querySelector('#ag-edit-defaults').onclick = () => { _editTarget = 'defaults'; _renderPanel(); };
        if (inWhitelist && hasSiteCfg) {
            _panel.querySelector('#ag-edit-site').onclick = () => { _editTarget = site; _renderPanel(); };
        }
        if (inWhitelist && !hasSiteCfg) {
            _panel.querySelector('#ag-create-site').onclick = () => {
                const s = _loadStore();
                if (!s.perSite) s.perSite = {};
                const current = cfgLoad();
                const siteData = {};
                for (const key of Object.keys(SITE_DEFAULTS)) siteData[key] = current[key];
                s.perSite[site] = siteData;
                _saveStore(s);
                _editTarget = site;
                _renderPanel();
            };
        }
        if (inWhitelist && hasSiteCfg) {
            _panel.querySelector('#ag-del-site').onclick = () => {
                const s = _loadStore();
                if (s.perSite && s.perSite[site]) {
                    delete s.perSite[site];
                    if (Object.keys(s.perSite).length === 0) delete s.perSite;
                }
                _saveStore(s);
                _editTarget = 'defaults';
                _renderPanel();
            };
        }

        _panel.querySelector('#ag-pick-chat').onclick = () => pickerEnter('chat');
        _panel.querySelector('#ag-pick-answer').onclick = () => pickerEnter('answer');
        _panel.querySelector('#ag-pick-input').onclick = () => pickerEnter('input');
        _panel.querySelector('#ag-pick-send').onclick = () => pickerEnter('send');
        _panel.querySelector('#ag-pick-clean-keyword').onclick = () => pickerEnter('clean-class');

        if (editCfg.selSendButton) {
            _panel.querySelector('#ag-start-calibrate').onclick = () => {
                if (!editCfg.selSendButton) { alert('请先选择发送按钮'); return; }
                _startCalibration();
            };
        }

        const posBtns = _panel.querySelectorAll('.ag-pos-btn');
        posBtns.forEach(btn => {
            if (btn.dataset.pos === editCfg.autoSendTogglePos) btn.classList.add('active');
            btn.onclick = () => { posBtns.forEach(b => b.classList.remove('active')); btn.classList.add('active'); };
        });

        ['chat', 'input', 'send', 'answer'].forEach(t => {
            const key = t === 'chat' ? 'selChatContainer' : t === 'input' ? 'selInputBox' : t === 'send' ? 'selSendButton' : 'selAnswerItem';
            _panel.querySelector(`#ag-s-${t}`).addEventListener('input', function () {
                _showMatch(this.value.trim(), `ag-m-${t}`);
                if (t === 'send') {
                    _panel.querySelector('#ag-calibrate-field').style.display = this.value.trim() ? 'block' : 'none';
                }
            });
            _showMatch(editCfg[key], `ag-m-${t}`);
        });

        _panel.querySelector('#ag-save').onclick = () => {
            const s = _loadStore();
            s.debugMode = _panel.querySelector('#ag-debug-toggle').checked;
            const siteData = { ...SITE_DEFAULTS };
            siteData.apiUrl = _panel.querySelector('#ag-api').value.trim() || SITE_DEFAULTS.apiUrl;
            siteData.selChatContainer = _panel.querySelector('#ag-s-chat').value.trim();
            siteData.selAnswerItem = _panel.querySelector('#ag-s-answer').value.trim() || '.answer';
            siteData.selInputBox = _panel.querySelector('#ag-s-input').value.trim();
            siteData.selSendButton = _panel.querySelector('#ag-s-send').value.trim();
            siteData.showAutoSendToggle = _panel.querySelector('#ag-show-toggle').checked;
            const activePos = _panel.querySelector('.ag-pos-btn.active');
            siteData.autoSendTogglePos = activePos ? activePos.dataset.pos : 'right';
            siteData.verifyMode = _panel.querySelector('input[name="verifyMode"]:checked').value;
            siteData.waitDelayAfterDone = parseInt(_panel.querySelector('#ag-wait-delay').value) || 500;
            siteData.sendBtnBusyFingerprints = editCfg.sendBtnBusyFingerprints || [];
            siteData.sendBtnIdleFingerprints = editCfg.sendBtnIdleFingerprints || [];
            siteData.autoSendMode = editCfg.autoSendMode || 'click';
            siteData.cleanIgnoreClassKeywords = _panel.querySelector('#ag-clean-keywords').value.trim();
            siteData.cleanRemoveButtonLike = _panel.querySelector('#ag-clean-buttons').checked;
            siteData.cleanRemovePre = _panel.querySelector('#ag-clean-pre').checked;

            if (_editTarget === 'defaults') { s.defaults = siteData; }
            else { if (!s.perSite) s.perSite = {}; s.perSite[_editTarget] = siteData; }

            _saveStore(s);
            hidePanel();
            if (isWhitelisted()) initAgent();
            if (s.debugMode) showDebug();
        };
    }

    function _showMatch(sel, id) {
        const el = _panel.querySelector('#' + id);
        if (!sel) { el.innerHTML = '<div class="ag-match ag-m-none">未设置</div>'; return; }
        try {
            const n = document.querySelectorAll(sel).length;
            el.innerHTML = n === 0 ? '<div class="ag-match ag-m-fail">✘ 未匹配</div>' :
                n === 1 ? '<div class="ag-match ag-m-ok">✔ 精确匹配 1 个</div>' :
                `<div class="ag-match ag-m-ok">✔ 匹配 ${n} 个</div>`;
        } catch (_) { el.innerHTML = '<div class="ag-match ag-m-fail">✘ 语法错误</div>'; }
    }

    /* ================================================================
     * 6. Agent 核心逻辑
     * ================================================================ */
    let _clipboardMode = false;
    let _permissionEnabled = true;
    let _isProcessing = false;
    let _cmdQueue = [];
    let _sendPromiseChain = Promise.resolve();
    let _isCalibrating = false;

    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    // 流式任务状态机
    // ━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━
    const TASK_START = '\n=== Poker Agent Task ===\n';
    const TASK_END = '\n=== Poker Agent Task End ===\n';

    let _taskList = [];
    let _sseEventSource = null;

    function _pollConfig() {
        const c = cfgLoad();
        const pollUrl = c.apiUrl.replace('/agent-exec', '/agent-config-poll');
        GM_xmlhttpRequest({
            method: 'GET', url: pollUrl, timeout: 30000,
            onload(r) {
                if (r.status === 200) {
                    try {
                        const data = JSON.parse(r.responseText);
                        if (!!data.clipboard_mode !== _clipboardMode) {
                            _clipboardMode = !!data.clipboard_mode;
                            log('INFO', `剪贴板模式: ${_clipboardMode ? '已开启' : '已关闭'}`);
                        }
                        if (!!data.permission_enabled !== _permissionEnabled) {
                            _permissionEnabled = !!data.permission_enabled;
                            log('INFO', `目录限制: ${_permissionEnabled ? '已启用' : '已禁用'}`);
                        }
                    } catch (e) {}
                }
                _pollConfig();
            },
            onerror() { setTimeout(_pollConfig, 5000); },
            ontimeout() { setTimeout(_pollConfig, 2000); }
        });
    }

    let _pollTimer = null;
    let _lastAnswerEl = null;
    const _currentRoundSent = new Set();
    let _heartbeatCounter = 0;
    let _knownAnswers = [];
    let _noAnswerCount = 0;

    function getCleanText(el, cfg) {
        const clone = el.cloneNode(true);
        const ignoreKeywords = (cfg.cleanIgnoreClassKeywords || 'thinking,reasoning,probe,deepseek-reason')
            .split(',').map(s => s.trim()).filter(s => s);

        if (ignoreKeywords.length > 0) {
            const sel = ignoreKeywords.map(k => `[class*="${CSS.escape(k)}"]`).join(', ');
            try { clone.querySelectorAll(sel).forEach(n => n.remove()); } catch(_) {}
        }

        clone.querySelectorAll('details').forEach(n => n.remove());

        if (cfg.cleanRemoveButtonLike !== false) {
            clone.querySelectorAll('button, [class*="copy"], [class*="operate"], [class*="action"], [class*="toolbar"]').forEach(n => n.remove());
        }

        if (cfg.cleanRemovePre !== false) {
            clone.querySelectorAll('pre').forEach(n => {
                if (clone.textContent.includes('\u3010CodeSTART\u3011')) return;
                n.remove();
            });
        }

        (function injectNewlines(node) {
            for (let i = node.childNodes.length - 1; i >= 0; i--) {
                const child = node.childNodes[i];
                if (child.nodeType === 1) {
                    if (/^(P|DIV|BR|LI|H[1-6]|PRE|BLOCKQUOTE|TR|HR|TABLE|UL|OL|SECTION|ARTICLE|HEADER|FOOTER|FIGURE|DD|DT|DL|MAIN|ASIDE|NAV|ADDRESS|FIELDSET|SUMMARY|FIGCAPTION|DIALOG|SEARCH)$/.test(child.tagName)) {
                        node.insertBefore(document.createTextNode('\n'), child);
                    }
                    injectNewlines(child);
                }
            }
        })(clone);

        return clone.textContent;
    }

    function _getSendBtnFingerprint() {
        const c = cfgLoad();
        if (!c.selSendButton) return null;
        const el = document.querySelector(c.selSendButton);
        if (!el) return 'ELEMENT_MISSING';
        const style = el.getAttribute('style') || '';
        const cls = typeof el.className === 'string' ? el.className : '';
        const innerTag = el.firstElementChild ? el.firstElementChild.tagName : '';
        const disabled = el.disabled ? '1' : '0';
        const ariaDisabled = el.getAttribute('aria-disabled') || '';
        const ariaLabel = el.getAttribute('aria-label') || '';
        return `${el.tagName}|${style}|${cls}|${innerTag}|${disabled}|${ariaDisabled}|${ariaLabel}`;
    }

    function _makeDraggable(el) {
        el.addEventListener('mousedown', (e) => {
            if (e.target.closest('button') || e.target.closest('input')) return;
            if (el.style.transform !== 'none') {
                const rect = el.getBoundingClientRect();
                el.style.transform = 'none';
                el.style.left = rect.left + 'px';
                el.style.top = rect.top + 'px';
            }
            const startX = e.clientX, startY = e.clientY;
            const origLeft = el.offsetLeft, origTop = el.offsetTop;
            const onMove = (ev) => {
                el.style.left = (origLeft + ev.clientX - startX) + 'px';
                el.style.top = (origTop + ev.clientY - startY) + 'px';
            };
            const onUp = () => {
                document.removeEventListener('mousemove', onMove);
                document.removeEventListener('mouseup', onUp);
            };
            document.addEventListener('mousemove', onMove);
            document.addEventListener('mouseup', onUp);
            e.preventDefault();
        });
    }

    function _startCalibration() {
        if (_isCalibrating) return;
        _isCalibrating = true;
        hidePanel();

        const bar = document.createElement('div');
        bar.id = 'ag-calibrate-bar';
        document.body.appendChild(bar);

        const cards = document.createElement('div');
        cards.id = 'ag-calibrate-cards';
        cards.style.display = 'none';
        cards.style.flexDirection = 'column';
        cards.style.gap = '10px';
        cards.style.height = 'calc(min(220px, 45vw) * 1.5)';
        document.body.appendChild(cards);
        _makeDraggable(cards);

        const c = cfgLoad();
        let capturedMap = new Map();
        let selectedBusy = new Set(c.sendBtnBusyFingerprints || []);
        let selectedIdle = new Set(c.sendBtnIdleFingerprints || []);
        let checkInterval = null;

        const renderBar = (msg) => {
            const mode = c.verifyMode || 'single';
            const canFinish = (mode === 'single' && selectedBusy.size > 0) || (mode === 'double' && selectedBusy.size > 0 && selectedIdle.size > 0);
            let listHtml = '';
            capturedMap.forEach((snap, fp) => {
                const isBusy = selectedBusy.has(fp);
                const isIdle = selectedIdle.has(fp);
                const cls = isBusy ? 'selected-busy' : (isIdle ? 'selected-idle' : '');
                listHtml += `
                <div class="ag-cal-item ${cls}">
                    <div class="ag-cal-clone" style="background:${snap.bg};color:${snap.color}">${snap.html}</div>
                    <div class="ag-cal-actions">
                        <button class="ag-cal-tag ${isBusy ? 'active-busy' : ''}" data-fp="${fp}" data-type="busy">忙碌</button>
                        <button class="ag-cal-tag ${isIdle ? 'active-idle' : ''}" data-fp="${fp}" data-type="idle">空闲</button>
                    </div>
                </div>`;
            });
            cards.innerHTML = listHtml || '<div style="color:#52525b;font-size:12px;text-align:center;padding:16px 0">等待按钮状态变化...</div>';
            cards.style.display = 'flex';

            bar.innerHTML = `
                <div style="font-size:13px;color:#d4d4d8;text-align:center">${msg}</div>
                <div style="display:flex;gap:8px;align-items:center">
                    <button class="ag-btn ag-btn-g" id="ag-cal-stop">取消</button>
                    <button class="ag-btn ag-btn-p" id="ag-cal-finish" style="${canFinish ? '' : 'opacity:0.5;pointer-events:none'}">完成校准</button>
                </div>`;
            bar.style.display = 'flex';

            bar.querySelector('#ag-cal-stop').onclick = () => stopCalibration();
            if (canFinish) {
                bar.querySelector('#ag-cal-finish').onclick = () => {
                    cfgSaveRuntime({
                        sendBtnBusyFingerprints: [...selectedBusy],
                        sendBtnIdleFingerprints: [...selectedIdle]
                    });
                    log('OK', `校准完成！忙碌: ${selectedBusy.size}个, 空闲: ${selectedIdle.size}个`);
                    log('INFO', '注意：指纹算法已升级（新增disabled/aria属性），旧校准数据已自动适配新格式');
                    stopCalibration();
                };
            }

            cards.querySelectorAll('.ag-cal-tag').forEach(btn => {
                btn.onclick = (e) => {
                    e.stopPropagation();
                    const fp = btn.dataset.fp;
                    const type = btn.dataset.type;
                    if (type === 'busy') {
                        if (selectedBusy.has(fp)) selectedBusy.delete(fp);
                        else selectedBusy.add(fp);
                        selectedIdle.delete(fp);
                    } else {
                        if (selectedIdle.has(fp)) selectedIdle.delete(fp);
                        else selectedIdle.add(fp);
                        selectedBusy.delete(fp);
                    }
                    renderBar(msg);
                };
            });
        };

        const stopCalibration = () => {
            if (checkInterval) clearInterval(checkInterval);
            bar.remove();
            cards.remove();
            _isCalibrating = false;
            showPanel();
        };

        renderBar('👇 请在下方正常聊天，脚本会自动捕获按钮的不同状态。<br><b style="color:#f472b6">请将"停止生成"标记为【忙碌】，其他状态标记为【空闲】。</b>');

        checkInterval = setInterval(() => {
            const fp = _getSendBtnFingerprint();
            if (!fp || fp === 'ELEMENT_MISSING') return;
            if (!capturedMap.has(fp)) {
                const el = document.querySelector(c.selSendButton);
                if (!el) return;
                const cs = getComputedStyle(el);
                let inner = el.innerHTML.replace(/<(style|script|link)[\s\S]*?<\/\1>/gi, '');
                capturedMap.set(fp, { html: inner, bg: cs.backgroundColor, color: cs.color });
                log('INFO', `捕获新状态指纹 (#${capturedMap.size})`);
                renderBar('👇 继续操作，或标记已捕获的状态后点击完成。<br><b style="color:#f472b6">请将"停止生成"标记为【忙碌】</b>');
            }
        }, 300);
    }

    function _waitForLLMFinish() {
        return new Promise(resolve => {
            const c = cfgLoad();
            const busyList = c.sendBtnBusyFingerprints || [];
            if (busyList.length === 0) {
                log('WARN', '⚠️ 未校准忙碌态，直接放行(建议校准)');
                resolve();
                return;
            }

            log('INFO', '👀 监听发送按钮状态...');

            const checkPhase1 = () => {
                const fp = _getSendBtnFingerprint();
                if (fp === null || fp === 'ELEMENT_MISSING' || !busyList.includes(fp)) {
                    log('INFO', '🟢 脱离忙碌态');
                    startPhase2();
                } else {
                    setTimeout(checkPhase1, 200);
                }
            };

            const startPhase2 = () => {
                if (c.verifyMode === 'double') {
                    const idleList = c.sendBtnIdleFingerprints || [];
                    if (idleList.length === 0) {
                        log('WARN', '⚠️ 双验证模式但未设置空闲态，退化为单验证');
                        triggerDelay();
                        return;
                    }
                    log('INFO', '👀 双验证：等待进入空闲态...');
                    const checkPhase2 = () => {
                        const fp = _getSendBtnFingerprint();
                        if (fp && idleList.includes(fp)) {
                            log('INFO', '🟢 进入空闲态');
                            triggerDelay();
                        } else {
                            setTimeout(checkPhase2, 200);
                        }
                    };
                    checkPhase2();
                } else {
                    triggerDelay();
                }
            };

            const triggerDelay = () => {
                const delay = parseInt(c.waitDelayAfterDone) || 500;
                log('INFO', `⏳ 等待延时 ${delay}ms...`);
                setTimeout(resolve, delay);
            };

            checkPhase1();
        });
    }

    async function _checkAndDispatch() {
        if (_isProcessing || _cmdQueue.length === 0) return;
        _isProcessing = true;
        log('INFO', `⏳ 挂起等待 AI 彻底输出完毕... (队列: ${_cmdQueue.length} 条)`);
        await _waitForLLMFinish();

        if (_cmdQueue.length === 0) {
            _isProcessing = false;
            return;
        }

        const batch = _cmdQueue.join('\n');
        _cmdQueue = [];
        _dispatch(batch);
    }

    function _dispatch(cmdBatch) {
        const c = cfgLoad();
        log('INFO', `🚀 AI已说完，发送至本地后端...`);

        cmdBatch = cmdBatch.replace(/\u2014/g, '--').replace(/\u2013/g, '-').replace(/\u201C/g, '"').replace(/\u201D/g, '"').replace(/\u2018/g, "'").replace(/\u2019/g, "'");

        GM_xmlhttpRequest({
            method: 'POST', url: c.apiUrl,
            headers: { 'Content-Type': 'application/json' },
            data: JSON.stringify({ command: cmdBatch }),
            onload: (r) => {
                if (r.status === 200) {
                    try {
                        const data = JSON.parse(r.responseText);
                        if (data.type === 'task_batch' && data.task_ids) {
                            log('OK', `📥 后端已接收，分配 ${data.task_ids.length} 个任务ID，建立SSE监听...`);
                            _taskList = data.task_ids.map(id => ({ id, status: 'waiting', logs: [], result: '' }));
                            _initSSE();
                            _renderTaskBlock();
                            return;
                        }
                    } catch(e) {
                        log('ERR', '解析task_batch失败: ' + e);
                    }
                }
                log('ERR', `HTTP ${r.status} 或响应格式错误`);
                _isProcessing = false;
                _checkAndDispatch();
            },
            onerror() {
                log('ERR', '无法连接本地服务');
                _isProcessing = false;
                _checkAndDispatch();
            }
        });
    }

    /**
     * 渲染专属区块：只替换两个标记之间的内容，保留用户在区块外的输入
     */
    function _renderTaskBlock() {
        const c = cfgLoad();
        const input = document.querySelector(c.selInputBox);
        if (!input) return;

        // 1. 拼接新的区块内容
        let block = TASK_START;
        _taskList.forEach((task, idx) => {
            if (task.status === 'done') {
                block += `[Poker Agent] [done]\n${task.result}\n`;
            } else if (task.status === 'running') {
                block += `[Poker Agent] [running]\n${task.logs.join('\n')}\n`;
            } else { // waiting
                block += `[Poker Agent] [waiting]\n\n`;
            }
            if (idx < _taskList.length - 1) block += '\n';
        });

        const allDone = _taskList.length > 0 && _taskList.every(t => t.status === 'done');
        if (allDone) {
            block += `\n[Poker Agent]\nAll tasks done!`;
        }
        block += TASK_END;

        // 2. 读取当前输入框的完整文本
        let currentText = '';
        if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
            currentText = input.value;
        } else {
            currentText = input.textContent || '';
        }

        // 3. 寻找标记，切分出用户输入的"前缀"和"后缀"
        let prefix = '';
        let suffix = '';
        const startIdx = currentText.indexOf(TASK_START);
        if (startIdx !== -1) {
            // 找到了起始标记：前缀是标记之前的内容
            prefix = currentText.substring(0, startIdx);
            const endIdx = currentText.indexOf(TASK_END, startIdx + TASK_START.length);
            if (endIdx !== -1) {
                // 找到了结束标记：后缀是结束标记之后的内容
                suffix = currentText.substring(endIdx + TASK_END.length);
            }
        } else {
            // 首次渲染或标记被删了：把当前所有文本当作前缀
            prefix = currentText.trim();
            if (prefix) prefix += '\n'; // 保证用户内容和区块间有换行
        }

        // 4. 拼接 前缀 + 新区块 + 后缀，写入输入框
        const finalText = prefix + block + suffix;
        _directInput(input, finalText, false);
    }

    /**
     * 建立 SSE 长连接，实时更新任务状态
     */
    function _initSSE() {
        if (_sseEventSource) {
            try { _sseEventSource.abort(); } catch(e) {}
            _sseEventSource = null;
        }

        const c = cfgLoad();
        const streamUrl = c.apiUrl.replace('/agent-exec', '/agent-stream');
        let _sseBuffer = '';

        _sseEventSource = GM_xmlhttpRequest({
            method: 'GET', url: streamUrl,
            headers: { 'Accept': 'text/event-stream' },
            timeout: 0,
            onprogress: (resp) => {
                const newData = resp.responseText.slice(_sseBuffer.length);
                _sseBuffer = resp.responseText;

                const events = newData.split('\n\n');
                for (const evt of events) {
                    if (!evt.trim() || evt.startsWith(':')) continue;
                    const dataLine = evt.split('\n').find(l => l.startsWith('data:'));
                    if (!dataLine) continue;

                    const jsonStr = dataLine.slice(5).trim();
                    if (!jsonStr) continue;

                    try {
                        const data = JSON.parse(jsonStr);
                        _handleSSEData(data);
                    } catch(err) {
                        log('ERR', `SSE 解析失败: ${err}, 原始: ${jsonStr.substring(0, 100)}`);
                    }
                }
            },
            onload: () => {
                log('INFO', 'SSE 连接正常关闭');
                _sseEventSource = null;
            },
            onerror: (err) => {
                log('ERR', `SSE 连接异常断开: ${err.error || ''}`);
                _sseEventSource = null;
                _isProcessing = false;
            },
            ontimeout: () => {
                log('WARN', 'SSE 连接超时（不应发生）');
            }
        });
    }

    function _handleSSEData(data) {
        if (data.id === 'all') return;

        const task = _taskList.find(t => t.id === data.id);
        if (!task) return;

        if (data.type === 'status') {
            if (data.status === 'running') {
                task.status = 'running';
            } else if (data.status === 'done') {
                task.status = 'done';
                task.result = data.result || '';
            }
        } else if (data.type === 'log') {
            if (task.status === 'waiting') task.status = 'running';
            task.logs.push(data.data);
        }

        _renderTaskBlock();

        if (_taskList.length > 0 && _taskList.every(t => t.status === 'done')) {
            if (_sseEventSource) {
                try { _sseEventSource.abort(); } catch(e) {}
                _sseEventSource = null;
            }
            _finalizeAndSend();
        }
    }

    async function _finalizeAndSend() {
        log('INFO', '✅ 所有任务完成，等待 LLM 输出完毕...');
        await _waitForLLMFinish();
        _renderTaskBlock();

        const c = cfgLoad();
        const input = document.querySelector(c.selInputBox);
        log('INFO', '🚀 触发最终发送');
        _executeSend(input);

        _isProcessing = false;
        _taskList = [];
        _checkAndDispatch();
    }

    function _trySendByClick() {
        const c = cfgLoad();
        if (!c.selSendButton) { log('WARN', '未配置发送按钮选择器，无法点击发送'); return false; }
        const btn = document.querySelector(c.selSendButton);
        if (!btn) { log('ERR', '找不到发送按钮，无法点击发送'); return false; }
        btn.click();
        log('INFO', '👆 点击发送按钮发送');
        return true;
    }

    function _executeSend(input) {
        const c = cfgLoad();
        const mode = c.autoSendMode || 'click';
        switch (mode) {
            case 'none':
                log('INFO', '⏸️ 自动发送已关闭，仅填入输入框');
                break;
            case 'enter':
                try {
                    _trySendByEnter(input);
                    log('INFO', '⏎ 回车发送');
                } catch (err) {
                    log('ERR', `回车发送失败: ${err.message}`);
                }
                break;
            case 'click':
            default:
                const clicked = _trySendByClick();
                if (!clicked) {
                    log('WARN', '发送按钮不可用，回退到回车发送');
                    try { _trySendByEnter(input); } catch (err) { log('ERR', `回车发送也失败: ${err.message}`); }
                }
                break;
        }
    }

    function _directInput(input, text, append = false) {
        input.focus();
        if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
            const proto = input.tagName === 'TEXTAREA' ? HTMLTextAreaElement.prototype : HTMLInputElement.prototype;
            const setter = Object.getOwnPropertyDescriptor(proto, 'value')?.set;
            if (setter) {
                const newValue = append ? input.value + text : text;
                setter.call(input, newValue);
            }
            input.dispatchEvent(new Event('input', { bubbles: true }));
            input.dispatchEvent(new Event('change', { bubbles: true }));
        } else {
            if (!append) { document.execCommand('selectAll', false, null); }
            document.execCommand('insertText', false, text);
        }
    }

    function _trySendByEnter(input) {
        ['keydown', 'keypress', 'keyup'].forEach(evtType => {
            input.dispatchEvent(new KeyboardEvent(evtType, {
                key: 'Enter', code: 'Enter', keyCode: 13, which: 13,
                charCode: evtType === 'keypress' ? 13 : 0, bubbles: true, cancelable: true, composed: true
            }));
        });
    }

    function _smartWait(input, opts = {}) {
        const { expectValue, checkDOM = false, maxWait = 3000, interval = 50, stableNeed = 3 } = opts;
        return new Promise(resolve => {
            let stable = 0;
            let domSnap = checkDOM ? input.parentElement?.children.length ?? -1 : -1;
            const t = setInterval(() => {
                const valOk = !expectValue || input.value === expectValue;
                const domOk = !checkDOM || (input.parentElement?.children.length ?? -1) === domSnap;
                if (valOk && domOk) {
                    stable++;
                } else {
                    stable = 0;
                    domSnap = checkDOM ? input.parentElement?.children.length ?? -1 : -1;
                }
                if (stable >= stableNeed) { clearInterval(t); clearTimeout(safety); resolve(); }
            }, interval);
            const safety = setTimeout(() => { clearInterval(t); resolve(); }, maxWait);
        });
    }

    /* ================================================================
     * 6.5 发送模式选择器
     * ================================================================ */
    let _toggleEl = null;
    let _togglePosTimer = null;

    function _initAutoSendToggle() {
        _destroyAutoSendToggle();
        let c;
        try { c = cfgLoad(); } catch(e) { console.error('[Agent] cfgLoad异常:', e); return; }
        if (!c.showAutoSendToggle || !c.selSendButton) return;

        const mode = c.autoSendMode || 'click';
        _toggleEl = document.createElement('div');
        _toggleEl.id = 'agent-auto-send-toggle';
        _toggleEl.innerHTML = `
            <div class="ag-as-opts">
                <div class="ag-as-opt ${mode === 'none' ? 'active' : ''}" data-mode="none">不自动发送</div>
                <div class="ag-as-opt ${mode === 'click' ? 'active' : ''}" data-mode="click">点击按钮</div>
                <div class="ag-as-opt ${mode === 'enter' ? 'active' : ''}" data-mode="enter">回车发送</div>
            </div>
            <div class="ag-as-rail">
                <div class="ag-as-thumb"></div>
            </div>
        `;
        document.body.appendChild(_toggleEl);

        _toggleEl.querySelectorAll('.ag-as-opt').forEach(opt => {
            opt.onclick = (e) => {
                e.stopPropagation();
                const newMode = opt.dataset.mode;
                cfgSaveRuntime({ autoSendMode: newMode });
                _toggleEl.querySelectorAll('.ag-as-opt').forEach(o => o.classList.remove('active'));
                opt.classList.add('active');
                _updateSliderPos();
                const modeLabels = { none: '不自动发送', click: '点击按钮', enter: '回车发送' };
                log('INFO', `发送模式切换为: ${modeLabels[newMode] || newMode}`);
            };
        });

        _toggleEl.onclick = (e) => e.stopPropagation();

        setTimeout(() => {
            _updateTogglePosition();
            _updateSliderPos();
            _togglePosTimer = setInterval(() => {
                _updateTogglePosition();
                _updateSliderPos();
            }, 500);
        }, 100);
    }

    function _updateSliderPos() {
        if (!_toggleEl) return;
        const c = cfgLoad();
        const mode = c.autoSendMode || 'click';
        const modes = ['none', 'click', 'enter'];
        const idx = modes.indexOf(mode);
        if (idx < 0) return;

        const opts = _toggleEl.querySelectorAll('.ag-as-opt');
        const thumb = _toggleEl.querySelector('.ag-as-thumb');
        const rail = _toggleEl.querySelector('.ag-as-rail');
        if (!opts[idx] || !thumb || !rail) return;

        const optRect = opts[idx].getBoundingClientRect();
        const railRect = rail.getBoundingClientRect();
        const top = optRect.top - railRect.top + optRect.height / 2 - 5;
        thumb.style.top = top + 'px';
    }

    function _updateTogglePosition() {
        if (!_toggleEl) return;
        const c = cfgLoad();
        const btn = document.querySelector(c.selSendButton);
        if (!btn) return;
        const br = btn.getBoundingClientRect();
        if (br.width === 0 && br.height === 0) return;
        if (br.bottom < 0 || br.top > innerHeight || br.right < 0 || br.left > innerWidth) {
            _toggleEl.style.display = 'none';
            return;
        }
        _toggleEl.style.display = 'flex';

        const tr = _toggleEl.getBoundingClientRect();
        const pos = c.autoSendTogglePos || 'right';
        let left, top;
        switch (pos) {
            case 'right':
                left = br.right;
                top = br.top + br.height / 2 - tr.height / 2;
                break;
            case 'left':
                left = br.left - tr.width;
                top = br.top + br.height / 2 - tr.height / 2;
                break;
            case 'top':
                left = br.left + br.width / 2 - tr.width / 2;
                top = br.top - tr.height;
                break;
            case 'bottom':
                left = br.left + br.width / 2 - tr.width / 2;
                top = br.bottom;
                break;
        }
        _toggleEl.style.left = left + 'px';
        _toggleEl.style.top = top + 'px';
    }

    function _destroyAutoSendToggle() {
        if (_togglePosTimer) {
            clearInterval(_togglePosTimer);
            _togglePosTimer = null;
        }
        if (_toggleEl) {
            _toggleEl.remove();
            _toggleEl = null;
        }
    }

    /* ================================================================
     * 7. 启动入口
     * ================================================================ */
    GM_registerMenuCommand('⚙️ Agent 配置面板', showPanel);

    if (cfgLoad().debugMode) setTimeout(initDebugUI, 500);

    function initAgent() {
        if (_pollTimer) {
            clearInterval(_pollTimer);
            _pollTimer = null;
        }
        _lastAnswerEl = null;
        _currentRoundSent.clear();
        _noAnswerCount = 0;
        _initAutoSendToggle();

        const c = cfgLoad();
        const selector = c.selChatContainer;
        let currentContainer = document.querySelector(selector);

        if (!currentContainer) {
            log('WARN', `找不到容器 ${selector}，5秒后重试...`);
            setTimeout(initAgent, 5000);
            return;
        }

        const answerSel = c.selAnswerItem || '.answer';
        _knownAnswers = [...currentContainer.querySelectorAll(answerSel)];
        _lastAnswerEl = null;
        _currentRoundSent.clear();
        _pollConfig();

        log('OK', `✅ 监听已启动！回答元素选择器: "${answerSel}"`);

        _pollTimer = setInterval(() => {
            try {
                _heartbeatCounter++;
                const freshContainer = document.querySelector(selector);
                if (!freshContainer) return;

                if (freshContainer !== currentContainer) {
                    log('WARN', '🚨 检测到聊天容器被替换，重置状态...');
                    currentContainer = freshContainer;
                    _lastAnswerEl = null;
                    _currentRoundSent.clear();
                    _cmdQueue = [];
                    _sendPromiseChain = Promise.resolve();
                    _isProcessing = false;
                    _initAutoSendToggle();
                    return;
                }

                const answers = [...currentContainer.querySelectorAll(answerSel)];

                if (answers.length === 0) {
                    _noAnswerCount++;
                    if (_noAnswerCount === 5) {
                        log('WARN', `⚠️ 已连续 5 次轮询未找到回答元素 ("${answerSel}")`);
                    } else if (_noAnswerCount >= 10 && _noAnswerCount % 10 === 0) {
                        log('WARN', `⚠️ 已连续 ${_noAnswerCount} 次轮询未找到回答元素 ("${answerSel}")，请检查选择器配置`);
                    }
                } else {
                    if (_noAnswerCount > 0) {
                        log('INFO', `🟢 回答元素重新出现 ("${answerSel}")，之前连续缺失 ${_noAnswerCount} 次`);
                    }
                    _noAnswerCount = 0;
                }

                if (_knownAnswers.length > 0 && !_knownAnswers.some(el => new Set(answers).has(el))) {
                    _lastAnswerEl = null;
                    _currentRoundSent.clear();
                    _cmdQueue = [];
                    _sendPromiseChain = Promise.resolve();
                    _isProcessing = false;
                    log('WARN', '🚨 对话被清空，重置状态...');
                }

                _knownAnswers = answers;

                if (_heartbeatCounter % 20 === 0)
                    log('INFO', `💓 心跳 | 队列${_cmdQueue.length}条 | 锁定:${_isProcessing} | 回答:${answers.length}个`);

                if (answers.length === 0) return;

                const lastAnswer = answers[answers.length - 1];

                if (lastAnswer !== _lastAnswerEl) {
                    _lastAnswerEl = lastAnswer;
                    _currentRoundSent.clear();
                }

                const re = /【cmd】([\s\S]*?)【\/cmd】/g;
                const text = getCleanText(lastAnswer, c);
                re.lastIndex = 0;
                let m;
                while ((m = re.exec(text)) !== null) {
                    const cmdStr = m[1].trim();
                    const normKey = m[0].replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n');
                    if (_currentRoundSent.has(normKey)) continue;
                    _currentRoundSent.add(normKey);
                    _cmdQueue.push(cmdStr);
                    log('OK', `🎉 捕获指令入队: ${cmdStr.substring(0, 60)}...`);
                    if (!_isProcessing) _checkAndDispatch();
                }
            } catch (err) {
                console.error('[Agent-ERR] 轮询异常:', err);
            }
        }, 800);
    }

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

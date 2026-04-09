// ==UserScript==
// @name         PokerAgent
// @namespace    http://tampermonkey.net/
// @version      5.0
// @author       LMaxRouterCN
// @description  Poker Agent 网页端配套脚本，支持层级穿透选择(左键向上,右键向下)，直角UI风格。
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
   * v4.1 重构：增加发送按钮状态指纹，实现精准判断 LLM 输出完毕
   * ================================================================ */
  
  // [新增] 站点级可配置项的默认值（不含全局项 whitelist/debugMode）
  const SITE_DEFAULTS = {
    apiUrl: 'http://127.0.0.1:9966/agent-exec' ,
    selChatContainer: '',
    selInputBox: '',
    selSendButton: '',
    // 废弃旧的 sendBtnIdleFingerprint，改为多指纹列表
    sendBtnBusyFingerprints: [], // 忙碌态指纹数组
    sendBtnIdleFingerprints: [],  // 空闲态指纹数组
    verifyMode: 'single',         // 'single' 单验证 | 'double' 双验证
    waitDelayAfterDone: 500,      // 放行前额外延时
    showAutoSendToggle: false,
    autoSendTogglePos: 'right',
    autoSendByEnter: false
};
  
  // 旧版兼容：完整默认值（含全局项），仅作 cfgLoad 兜底
  const DEFAULTS = {
      whitelist: ['https://chatglm.cn/'],
      debugMode: false,
      ...SITE_DEFAULTS
  };
  
  // [修改] 存储键升级，触发旧配置自动迁移
  const STORE_KEY = 'low_cost_agent_config_v4';

  function _loadStore() {
      let store;
      try { store = GM_getValue(STORE_KEY, null); } catch (_) { store = null; }
      if (!store) {
          return { whitelist: ['https://chatglm.cn/'] , debugMode: false, defaults: { ...SITE_DEFAULTS }, perSite: { 'https://chatglm.cn/' : { ...SITE_DEFAULTS, selChatContainer: 'div.chatScrollContainer' } } };
      }
      return _migrateStore(store);
  }
  
  function _saveStore(store) { GM_setValue(STORE_KEY, store); }
  
  function _migrateStore(store) {
      if (store.defaults && store.perSite !== undefined) {
          // 兼容旧版单指纹：如果有旧字段，强制清空，提示用户重新校准
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
          };
          if (store.defaults) clearOld(store.defaults);
          if (store.perSite) Object.values(store.perSite).forEach(clearOld);
          return store;
      }
      const newStore = { whitelist: store.whitelist || ['https://chatglm.cn/'] , debugMode: !!store.debugMode, defaults: { ...SITE_DEFAULTS }, perSite: {} };
      for (const key of Object.keys(SITE_DEFAULTS)) { if (store[key] !== undefined) newStore.defaults[key] = store[key]; }
      return newStore;
  }
  
  // [新增] 匹配当前页面 URL 对应的白名单条目，未匹配返回 null
  function _matchSite() { 
      const store = _loadStore(); 
      return store.whitelist.find(p => location.href.startsWith(p)) || null; 
  }
  
  // [新增] 获取当前页面配置来源：'defaults' 或具体网站条目
  function _getConfigSource() {
      const store = _loadStore(); 
      const site = _matchSite();
      if (site && store.perSite && store.perSite[site]) return site;
      return 'defaults';
  }
  
  // [修改] 从双层结构读取合并后的完整配置（接口不变，下游无需改动）
  function cfgLoad() {
      const store = _loadStore(); 
      const site = _matchSite();
      // 优先使用网站独立配置，否则使用默认配置
      const siteCfg = (site && store.perSite && store.perSite[site]) ? store.perSite[site] : (store.defaults || {});
      return { 
          ...DEFAULTS, // 兜底默认值
          whitelist: store.whitelist, 
          debugMode: store.debugMode, 
          ...siteCfg // 站点级配置覆盖默认值
      };
  }
  
  // [新增] 面板编辑目标：'defaults' 或具体网站白名单条目
  let _editTarget = 'defaults';
  
  // [修改] 面板保存：全局项始终存顶层，站点项存到 _editTarget
  function cfgSave(panelValues) {
      const store = _loadStore(); 
      // 全局项始终保存到 store 顶层
      store.debugMode = panelValues.debugMode;
      // whitelist 在增删 handler 中已实时保存，这里不再重复处理
      // 提取站点级字段
      const siteData = { ...SITE_DEFAULTS };
      for (const key of Object.keys(SITE_DEFAULTS)) { 
          if (panelValues[key] !== undefined) siteData[key] = panelValues[key]; 
      }
      // 按编辑目标保存
      if (_editTarget === 'defaults') { 
          store.defaults = siteData; 
      } else { 
          if (!store.perSite) store.perSite = {}; 
          store.perSite[_editTarget] = siteData; 
      }
      _saveStore(store);
  }
  
  // [新增] 运行时保存（如滑块开关）：自动保存到当前网站的实际配置源
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
   * 2. 样式注入 (已移除所有圆角)
   * ================================================================ */
  
  GM_addStyle(`
  /* --- 配置面板 --- */
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
  /* --- 选择器 --- */
  #agent-pick-dim{position:fixed;inset:0;background:rgba(0,0,0,.28);z-index:2147483645;pointer-events:none}
  #agent-pick-hl{position:fixed;border:2.5px solid #818cf8;background:rgba(129,140,248,.08);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;box-shadow:0 0 0 4000px rgba(0,0,0,.25);display:none}
  #agent-pick-lock-hl{position:fixed;border:2.5px solid #f472b6;background:rgba(244,114,182,.06);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;display:none}
  #agent-pick-tip{position:fixed;background:#1a1b2e;color:#c4b5fd;border:1px solid #3f3f46;padding:5px 10px;border-radius:0;font:11px/1.4 'SF Mono',Consolas,monospace;z-index:2147483647;pointer-events:none;max-width:500px;word-break:break-all;box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .08s;display:flex;align-items:center;gap:0}
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
  .ag-level-cls{color:#a1a1aa;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;}
  .ag-level-sel{color:#52525b;font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;}
  /* --- 调试浮窗 --- */
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
  /* 目标页面上的浮动自动发送滑块样式 */
  #agent-auto-send-toggle{position:fixed;z-index:2147483640;display:flex;align-items:center;gap:6px;background:#1a1b2e;border:1px solid #3f3f46;padding:3px 7px;pointer-events:auto;white-space:nowrap;user-select:none;opacity:0.85;transition:opacity .15s;}
  #agent-auto-send-toggle:hover{opacity:1}
  .ag-as-label{font-size:11px;color:#a1a1aa;font-family:system-ui,sans-serif}
  .ag-as-track{width:28px;height:14px;background:#3f3f46;position:relative;cursor:pointer;transition:background .15s;border-radius:0;}
  .ag-as-track.active{background:#818cf8}
  .ag-as-thumb{width:12px;height:12px;background:#d4d4d8;position:absolute;top:1px;left:1px;transition:left .15s;border-radius:0;}
  .ag-as-track.active .ag-as-thumb{left:15px;background:#0f0f23}
/* --- 校准引导条 --- */
#ag-calibrate-bar{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#1a1b2e;color:#fde68a;border:1px solid #facc15;padding:14px 24px;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);font:13px/1.5 system-ui,sans-serif;display:none;flex-direction:column;align-items:center;gap:10px;pointer-events:auto;width:min(600px,90vw)}
#ag-calibrate-bar b{color:#facc15}

#ag-calibrate-cards{position:fixed;top:100px;left:50%;transform:translateX(-50%);background:#1a1b2e;border:1px solid #3f3f46;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);width:min(220px,45vw);overflow-y:auto;overflow-x:hidden;padding:10px;cursor:move}
#ag-calibrate-cards::-webkit-scrollbar{width:4px}
#ag-calibrate-cards::-webkit-scrollbar-thumb{background:#3f3f46}
.ag-cal-item{display:flex;flex-direction:column;align-items:center;gap:6px;padding:6px;background:#232436;transition:.15s;width:100%;box-sizing:border-box;overflow:hidden}
.ag-cal-item.selected-busy{background:rgba(244,114,182,.1);outline:1px solid #f472b6}
.ag-cal-item.selected-idle{background:rgba(134,239,172,.1);outline:1px solid #86efac}
.ag-cal-clone{width:100%;min-height:40px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#0f0f23}
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
   * 4. 元素选择器 (推栈模式：左键向上，右键向下)
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
              const cls = cur.className.trim().split(/\s+/)
                  .filter(c => c && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c))
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
      [_pickDim, _pickHL, _pickLockHL, _pickTip, _pickBar, _levelPanel].forEach(e => e && e.remove()); 
      _pickLockHL = null; 
      _levelPanel = null; 
      showPanel();
  }
  
  function _targetAt(x, y) { 
      let el = document.elementFromPoint(x, y); 
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
  
  function _highlightEl(el, mouseX, mouseY) {
      const r = el.getBoundingClientRect(); 
      _pickHL.style.display = 'block';
      Object.assign(_pickHL.style, { 
          left: (r.left-2)+'px', top: (r.top-2)+'px', 
          width: (r.width+4)+'px', height: (r.height+4)+'px' 
      });
      const sel = genSelector(el);
      _pickTip.innerHTML = _lockedBaseEl 
          ? `<span id="ag-show-levels">展开所有层级</span><span>${esc(sel)} ← ${el.tagName.toLowerCase()}</span>` 
          : `<span>${esc(sel)} ← ${el.tagName.toLowerCase()}</span>`;
      _pickTip.style.opacity = '1'; 
      _pickTip.style.left = Math.min(mouseX + 14, innerWidth - 510) + 'px'; 
      _pickTip.style.top = (mouseY + 22) + 'px';
  }
  
  function _updateLockHL() { 
      if (!_pickLockHL || !_lockedBaseEl) return; 
      const r = _lockedBaseEl.getBoundingClientRect(); 
      _pickLockHL.style.display = 'block'; 
      Object.assign(_pickLockHL.style, { 
          left: (r.left-2)+'px', top: (r.top-2)+'px', 
          width: (r.width+4)+'px', height: (r.height+4)+'px' 
      }); 
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
      while (cur && cur !== document.documentElement) { 
          chain.unshift(cur); 
          cur = cur.parentElement; 
      }
      if (chain.length > 0 && chain[0] !== document.documentElement && chain[0].parentElement === document.documentElement) {
          chain.unshift(document.documentElement);
      }
      
      let html = '<div class="ag-level-head"><span>📐 层级结构 (点击选择)</span><button id="ag-level-close">✕</button></div><div class="ag-level-body">';
      chain.forEach((el, i) => { 
          const sel = genSelector(el) || '(无法生成)'; 
          const tag = el.tagName.toLowerCase(); 
          const rawCls = el.className && typeof el.className === 'string' ? el.className.trim() : ''; 
          const clsSnippet = rawCls ? rawCls.split(/\s+/).filter(c => c).slice(0, 3).join('.') : ''; 
          const isTarget = el === _lockedBaseEl; 
          html += `<div class="ag-level-item ${isTarget ? 'ag-level-target' : ''}" data-idx="${i}"><span class="ag-level-idx">${i}</span><span class="ag-level-tag">&lt;${tag}&gt;</span><span class="ag-level-cls">${esc(clsSnippet)}</span><span class="ag-level-sel" title="${esc(sel)}">${esc(sel)}</span></div>`; 
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
      e.stopPropagation(); 
      e.preventDefault();
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
      e.stopPropagation(); 
      e.preventDefault();
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
      const sel = genSelector(el); 
      if (!sel) { log('ERR', '无法生成选择器'); return; }
      const c = cfgLoad(); 
      if (_pickType === 'chat') c.selChatContainer = sel; 
      if (_pickType === 'input') c.selInputBox = sel; 
      if (_pickType === 'send') c.selSendButton = sel;
      cfgSaveRuntime(c); 
      log('OK', `已选择 [${TYPE_LABEL[_pickType]}]: ${sel}`); 
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
          <div class="ag-sec">
              <div class="ag-sec-title">控制台</div>
              <div class="ag-toggle">
                  <input type="checkbox" id="ag-debug-toggle" ${store.debugMode ? 'checked' : ''} />
                  <label for="ag-debug-toggle" style="cursor:pointer">启用调试模式 (右侧显示日志浮窗)</label>
              </div>
          </div>
          <div class="ag-sec">
              <div class="ag-sec-title">网站白名单</div>
              <div class="ag-wl-list" id="ag-wl-list">${store.whitelist.length ? store.whitelist.map((u, i) => `<div class="ag-wl-item"><code>${esc(u)}</code><button class="ag-wl-rm" data-i="${i}">✕</button></div>`).join('') : '<div style="padding:8px 10px;color:#52525b;font-size:12px">暂无</div>'}</div>
              <div class="ag-row"><input class="ag-inp" id="ag-wl-new" placeholder="https://example.com/" /><button class="ag-btn ag-btn-g" id="ag-wl-add">添加</button></div>
          </div>
          <div class="ag-sec">
              <div class="ag-sec-title">本地 Agent 服务</div>
              <div class="ag-field"><label>接收指令的 HTTP 地址</label><input class="ag-inp" id="ag-api" value="${esc(editCfg.apiUrl)}" /></div>
          </div>
          <div class="ag-sec">
              <div class="ag-sec-title">页面元素绑定</div>
              <div class="ag-field">
                  <label>聊天记录容器</label>
                  <div class="ag-row"><input class="ag-inp" id="ag-s-chat" value="${esc(editCfg.selChatContainer)}" /><button class="ag-btn ag-btn-p" id="ag-pick-chat">🖱 选择</button></div>
                  <div id="ag-m-chat"></div>
              </div>
              <div class="ag-field">
                  <label>输入框</label>
                  <div class="ag-row"><input class="ag-inp" id="ag-s-input" value="${esc(editCfg.selInputBox)}" /><button class="ag-btn ag-btn-p" id="ag-pick-input">🖱 选择</button></div>
                  <div id="ag-m-input"></div>
              </div>
              <div class="ag-field">
                  <label>发送按钮</label>
                  <div class="ag-row"><input class="ag-inp" id="ag-s-send" value="${esc(editCfg.selSendButton)}" /><button class="ag-btn ag-btn-p" id="ag-pick-send">🖱 选择</button></div>
                  <div id="ag-m-send"></div>
                  <div class="ag-field" id="ag-calibrate-field" style="margin-top:6px; padding:8px; background:#232436; border:1px solid #2e3047; display:${editCfg.selSendButton ? 'block' : 'none'};">
                      <div style="font-size:12px; color:#a1a1aa; margin-bottom:6px">捕获按钮的各种形态，手动标记【忙碌】(AI输出时)和【空闲】态。</div>
                      <div class="ag-row">
                          <div id="ag-calibrate-status" style="flex:1; font-size:11px; color:#52525b">
                              忙碌:${(editCfg.sendBtnBusyFingerprints||[]).length}个 | 空闲:${(editCfg.sendBtnIdleFingerprints||[]).length}个
                          </div>
                          <button class="ag-btn ag-btn-p" id="ag-start-calibrate">${(editCfg.sendBtnBusyFingerprints||[]).length > 0 ? '重新校准' : '开始校准'}</button>
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

                  <label>自动发送滑块</label>
                  <div class="ag-toggle" style="margin-bottom:6px">
                      <input type="checkbox" id="ag-show-toggle" ${editCfg.showAutoSendToggle ? 'checked' : ''} />
                      <label for="ag-show-toggle" style="cursor:pointer">在发送按钮旁显示回车发送开关</label>
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
          <div class="ag-foot"><button class="ag-btn ag-btn-g" id="ag-cancel">取消</button><button class="ag-btn ag-btn-p" id="ag-save">${saveText}</button></div>
      </div>`;
  
      // --- 事件绑定 ---
      _panel.querySelector('#agent-panel-close').onclick = hidePanel;
      _panel.querySelector('#ag-cancel').onclick = hidePanel;
      
      // 调试模式切换直接操作 store 全局项
      _panel.querySelector('#ag-debug-toggle').onchange = (e) => { 
          const s = _loadStore(); 
          s.debugMode = e.target.checked; 
          _saveStore(s); 
          if (e.target.checked) { showDebug(); log('INFO', '调试模式已开启'); } 
          else { if (_debugPanel) _debugPanel.style.display = 'none'; } 
      };
      
      // 白名单增删直接操作 store 全局项
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
          btn.onclick = () => { const s = _loadStore(); s.whitelist.splice(+btn.dataset.i, 1); _saveStore(s); _renderPanel(); }; 
      });
      
      // 编辑目标切换按钮事件
      _panel.querySelector('#ag-edit-defaults').onclick = () => { _editTarget = 'defaults'; _renderPanel(); };
      if (inWhitelist && hasSiteCfg) { 
          _panel.querySelector('#ag-edit-site').onclick = () => { _editTarget = site; _renderPanel(); }; 
      }
      // 创建独立配置
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
      // 删除独立配置
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
      
      // 元素选择器
      _panel.querySelector('#ag-pick-chat').onclick = () => pickerEnter('chat');
      _panel.querySelector('#ag-pick-input').onclick = () => pickerEnter('input');
      _panel.querySelector('#ag-pick-send').onclick = () => pickerEnter('send');
      
      // 校准按钮
      if (editCfg.selSendButton) {
          _panel.querySelector('#ag-start-calibrate').onclick = () => { 
              if (!editCfg.selSendButton) { alert('请先选择发送按钮'); return; } 
              _startCalibration(); 
          };
      }
  
      // 滑块位置选择器
      const posBtns = _panel.querySelectorAll('.ag-pos-btn');
      posBtns.forEach(btn => { 
          if (btn.dataset.pos === editCfg.autoSendTogglePos) btn.classList.add('active'); 
          btn.onclick = () => { posBtns.forEach(b => b.classList.remove('active')); btn.classList.add('active'); }; 
      });
      
      // 选择器匹配状态实时检测
      ['chat', 'input', 'send'].forEach(t => { 
          const key = t === 'chat' ? 'selChatContainer' : t === 'input' ? 'selInputBox' : 'selSendButton'; 
          _panel.querySelector(`#ag-s-${t}`).addEventListener('input', function () { 
              _showMatch(this.value.trim(), `ag-m-${t}`); 
              if (t === 'send') { 
                  _panel.querySelector('#ag-calibrate-field').style.display = this.value.trim() ? 'block' : 'none'; 
              } 
          }); 
          _showMatch(editCfg[key], `ag-m-${t}`); 
      });
      
      // 保存配置
      _panel.querySelector('#ag-save').onclick = () => {
          const s = _loadStore(); 
          s.debugMode = _panel.querySelector('#ag-debug-toggle').checked;
          const siteData = { ...SITE_DEFAULTS };
          siteData.apiUrl = _panel.querySelector('#ag-api').value.trim() || SITE_DEFAULTS.apiUrl;
          siteData.selChatContainer = _panel.querySelector('#ag-s-chat').value.trim();
          siteData.selInputBox = _panel.querySelector('#ag-s-input').value.trim();
          siteData.selSendButton = _panel.querySelector('#ag-s-send').value.trim();
          siteData.showAutoSendToggle = _panel.querySelector('#ag-show-toggle').checked;
          const activePos = _panel.querySelector('.ag-pos-btn.active'); 
          siteData.autoSendTogglePos = activePos ? activePos.dataset.pos : 'right';
          // 保存验证模式与延时
          siteData.verifyMode = _panel.querySelector('input[name="verifyMode"]:checked').value;
          siteData.waitDelayAfterDone = parseInt(_panel.querySelector('#ag-wait-delay').value) || 500;
          // 指纹列表在校准时已通过 cfgSaveRuntime 实时保存，这里直接透传防止被清空
          siteData.sendBtnBusyFingerprints = editCfg.sendBtnBusyFingerprints || [];
          siteData.sendBtnIdleFingerprints = editCfg.sendBtnIdleFingerprints || [];

          
          if (_editTarget === 'defaults') { 
              s.defaults = siteData; 
          } else { 
              if (!s.perSite) s.perSite = {}; 
              s.perSite[_editTarget] = siteData; 
          }
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
          el.innerHTML = n === 0 ? '<div class="ag-match ag-m-fail">✘ 未匹配</div>' : n === 1 ? '<div class="ag-match ag-m-ok">✔ 精确匹配 1 个</div>' : `<div class="ag-match ag-m-ok">✔ 匹配 ${n} 个</div>`; 
      } catch (_) { 
          el.innerHTML = '<div class="ag-match ag-m-fail">✘ 语法错误</div>'; 
      }
  }

  /* ================================================================
 * 6. Agent 核心逻辑 (终极重构：状态指纹 + 串行追加)
 * ================================================================ */

let _clipboardMode = false;
let _permissionEnabled = true;
let _isProcessing = false;      // 闭环状态锁
let _cmdQueue = [];             // 待发送指令队列
let _sendPromiseChain = Promise.resolve(); // 串行输入队列，解决吃回执
let _isCalibrating = false;

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

function getCleanText(el) {
    const clone = el.cloneNode(true);
    // 移除思考过程等无关节点
    clone.querySelectorAll('[class*="thinking"], [class*="reasoning"], [class*="probe"], [class*="deepseek-reason"], details')
        .forEach(n => n.remove());
    // 移除 UI 操作按钮
    clone.querySelectorAll('button, [class*="copy"], [class*="operate"], [class*="action"], [class*="toolbar"]')
        .forEach(n => n.remove());
    // 移除代码块，但保留我们的指令代码块
    clone.querySelectorAll('pre').forEach(n => {
        if (n.closest('.answer')?.textContent.includes('\u3010CodeSTART\u3011')) return;
        n.remove();
    });
    
    // 注入换行符以保证文本结构
    (function injectNewlines(node) {
        for (let i = node.childNodes.length - 1; i >= 0; i--) {
            const child = node.childNodes[i];
            if (child.nodeType === 1) {
                if (/^(P|DIV|BR|LI|H[1-6]|PRE|BLOCKQUOTE|TR|HR|TABLE|UL|OL|SECTION|ARTICLE|HEADER|FOOTER|FIGURE|DD|DT|DL)$/.test(child.tagName)) {
                    node.insertBefore(document.createTextNode('\n'), child);
                }
                injectNewlines(child);
            }
        }
    })(clone);
    
    return clone.textContent;
}

// --- 指纹提取与校准 ---

function _getSendBtnFingerprint() {
    const c = cfgLoad();
    if (!c.selSendButton) return null;
    const el = document.querySelector(c.selSendButton);
    // 元素不存在(被隐藏/替换)的统一标识
    if (!el) return 'ELEMENT_MISSING';
    
    const style = el.getAttribute('style') || '';
    const cls = typeof el.className === 'string' ? el.className : '';
    const innerTag = el.firstElementChild ? el.firstElementChild.tagName : '';
    
    return `${el.tagName}|${style}|${cls}|${innerTag}`;
}

// --- 通用拖动：mousedown 时注册监听，mouseup 时自动注销，不会泄漏 ---
function _makeDraggable(el) {
  el.addEventListener('mousedown', (e) => {
      // 不拦截内部按钮的点击
      if (e.target.closest('button') || e.target.closest('input')) return;
      // 首次拖动时把 transform 居中转为绝对定位，避免后续偏移计算出错
      if (el.style.transform !== 'none') {
          const rect = el.getBoundingClientRect();
          el.style.transform = 'none';
          el.style.left = rect.left + 'px';
          el.style.top = rect.top + 'px';
      }
      const startX = e.clientX;
      const startY = e.clientY;
      const origLeft = el.offsetLeft;
      const origTop = el.offsetTop;
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
  // 关键改动：所有 flex 和尺寸属性通过内联样式设置，避免和 CSS 里的 display:none 冲突
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

      // 直接往 cards 里塞条目，不套额外的 list 包裹层
      let listHtml = '';
      capturedMap.forEach((snap, fp) => {
          const isBusy = selectedBusy.has(fp);
          const isIdle = selectedIdle.has(fp);
          const cls = isBusy ? 'selected-busy' : (isIdle ? 'selected-idle' : '');
          // 不再套 .ag-cal-clone-inner，直接放 HTML
          listHtml += `
            <div class="ag-cal-item ${cls}">
              <div class="ag-cal-clone" style="background:${snap.bg};color:${snap.color}">
                ${snap.html}
              </div>
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
              cfgSaveRuntime({ sendBtnBusyFingerprints: [...selectedBusy], sendBtnIdleFingerprints: [...selectedIdle] });
              log('OK', `校准完成！忙碌: ${selectedBusy.size}个, 空闲: ${selectedIdle.size}个`);
              stopCalibration();
          };
      }
      cards.querySelectorAll('.ag-cal-tag').forEach(btn => {
          btn.onclick = (e) => {
              e.stopPropagation();
              const fp = btn.dataset.fp;
              const type = btn.dataset.type;
              if (type === 'busy') {
                  if (selectedBusy.has(fp)) selectedBusy.delete(fp); else selectedBusy.add(fp);
                  selectedIdle.delete(fp);
              } else {
                  if (selectedIdle.has(fp)) selectedIdle.delete(fp); else selectedIdle.add(fp);
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
          let inner = el.innerHTML;
          if (inner.length > 500) inner = inner.substring(0, 500);
          capturedMap.set(fp, { html: inner, bg: cs.backgroundColor, color: cs.color });
          log('INFO', `捕获新状态指纹 (#${capturedMap.size})`);
          renderBar('👇 继续操作，或标记已捕获的状态后点击完成。<br><b style="color:#f472b6">请将"停止生成"标记为【忙碌】</b>');
      }
  }, 300);
}



// --- 等待与调度 ---

function _waitForLLMFinish() {
  return new Promise(resolve => {
      const c = cfgLoad();
      const busyList = c.sendBtnBusyFingerprints || [];

      // 如果没有校准过，直接放行
      if (busyList.length === 0) {
          log('WARN', '⚠️ 未校准忙碌态，直接放行(建议校准)');
          resolve();
          return;
      }

      log('INFO', '👀 监听发送按钮状态...');

      // 阶段 1：等待脱离忙碌态 (如果不处于忙碌态，说明AI秒回或已结束，直接放行)
      const checkPhase1 = () => {
          const fp = _getSendBtnFingerprint();
          if (fp === null || fp === 'ELEMENT_MISSING' || !busyList.includes(fp)) {
              log('INFO', '🟢 脱离忙碌态');
              startPhase2();
          } else {
              setTimeout(checkPhase1, 200);
          }
      };

      // 阶段 2：双验证 或 延时
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

      // 阶段 3：延时
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

function _dispatch(cmd) {
    const c = cfgLoad();
    log('INFO', `🚀 AI已说完，发送至本地后端...`);
    
    // 统一处理全角半角标点
    cmd = cmd.replace(/\u2014/g, '--')
             .replace(/\u2013/g, '-')
             .replace(/\u201C/g, '"')
             .replace(/\u201D/g, '"')
             .replace(/\u2018/g, "'")
             .replace(/\u2019/g, "'");
    
    GM_xmlhttpRequest({
        method: 'POST', 
        url: c.apiUrl, 
        headers: { 'Content-Type': 'application/json' }, 
        data: JSON.stringify({ command: cmd }),
        onload: async (r) => {
            let resultText = '';
            if (r.status === 200) {
                try {
                    const data = JSON.parse(r.responseText);
                    if (data.type === 'clipboard_file') {
                        log('OK', `准备粘贴文件: ${data.filename}`);
                        await _pasteFile(data.filename, data.data);
                        return; // _pasteFile 内部会处理 _isProcessing 释放
                    }
                } catch (e) {}
                resultText = r.responseText;
            } else {
                resultText = `[Agent 错误] HTTP ${r.status}`;
                log('ERR', `HTTP ${r.status}`);
            }
            
            log('OK', `📥 后端返回完成，排队输入回执...`);
            await _sendToChat(resultText);
            
            // 彻底闭环完成，解锁
            _isProcessing = false;
            // 再看一眼有没有新来的指令
            _checkAndDispatch();
        },
        onerror() {
            log('ERR', '无法连接本地服务');
            _sendToChat('[Agent 错误] 无法连接本地服务').then(() => {
                _isProcessing = false;
                _checkAndDispatch();
            });
        }
    });
}

// --- 串行输入与发送 ---

async function _sendToChat(text) {
    const finalText = '[Poker Agent] ' + text;
    _sendPromiseChain = _sendPromiseChain.then(() => _doSend(finalText));
    return _sendPromiseChain;
}

async function _doSend(text) {
    const c = cfgLoad();
    const input = document.querySelector(c.selInputBox);
    const btn = document.querySelector(c.selSendButton);
    if (!input || !btn) { 
        log('ERR', '找不到输入框或发送按钮'); 
        return; 
    }
    
    log('INFO', '⌨️ 正在模拟输入并点击发送...');
    _directInput(input, text);
    await _smartWait(input, { expectValue: text });
    await new Promise(r => setTimeout(r, 100));
    
    if (c.autoSendByEnter) { 
        try { _trySendByEnter(input); } catch (err) { log('ERR', `模拟回车发送失败: ${err.message}`); } 
    }
    try { btn.click(); } catch (err) { log('ERR', `点击发送失败: ${err.message}`); }
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

function _trySendByEnter(input) {
    ['keydown', 'keypress', 'keyup'].forEach(evtType => {
        input.dispatchEvent(new KeyboardEvent(evtType, { 
            key: 'Enter', code: 'Enter', keyCode: 13, which: 13, 
            charCode: evtType === 'keypress' ? 13 : 0, 
            bubbles: true, cancelable: true, composed: true 
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
            if (stable >= stableNeed) { 
                clearInterval(t); 
                clearTimeout(safety); 
                resolve(); 
            }
        }, interval);
        const safety = setTimeout(() => { clearInterval(t); resolve(); }, maxWait);
    });
}

/* ================================================================
 * 6.5 自动发送滑块
 * ================================================================ */

let _toggleEl = null;
let _togglePosTimer = null;

function _initAutoSendToggle() {
  _destroyAutoSendToggle();
  let c;
  try { c = cfgLoad(); } catch(e) { console.error('[Agent] cfgLoad异常:', e); return; }
  if (!c.showAutoSendToggle || !c.selSendButton) return;
  log('INFO', `🔧 初始化滑块 | showToggle=${c.showAutoSendToggle} | btn=${c.selSendButton.substring(0, 40)}...`);
  _toggleEl = document.createElement('div');
  _toggleEl.id = 'agent-auto-send-toggle';
  _toggleEl.innerHTML = `
      <span class="ag-as-label">回车发送</span>
      <div class="ag-as-track ${c.autoSendByEnter ? 'active' : ''}">
          <div class="ag-as-thumb"></div>
      </div>`;
  document.body.appendChild(_toggleEl);
  const track = _toggleEl.querySelector('.ag-as-track');
  track.onclick = (e) => {
      e.stopPropagation();
      const cfg = cfgLoad();
      cfg.autoSendByEnter = !cfg.autoSendByEnter;
      cfgSaveRuntime({ autoSendByEnter: cfg.autoSendByEnter });
      track.classList.toggle('active', cfg.autoSendByEnter);
      log('INFO', `回车发送: ${cfg.autoSendByEnter ? '已开启' : '已关闭'}`);
  };
  _toggleEl.onclick = (e) => e.stopPropagation();
  // 关键修复：用 setTimeout 代替 requestAnimationFrame，并确保 interval 一定注册
  setTimeout(() => {
      try { _updateTogglePosition(); } catch(e) { console.error('[Agent] 首次定位异常:', e); }
      // 不管首次是否成功，interval 必须注册，后续会持续修正
      _togglePosTimer = setInterval(() => {
          try { _updateTogglePosition(); } catch(e) { console.error('[Agent] 定位轮询异常:', e); }
      }, 500);
  }, 100);
}


function _updateTogglePosition() {
  if (!_toggleEl) return;
  const c = cfgLoad();
  const btn = document.querySelector(c.selSendButton);
  if (!btn) {
      // 不再直接隐藏，而是等下一轮轮询再试
      // 页面可能还在渲染，按钮元素可能暂时不存在
      return;
  }
  const br = btn.getBoundingClientRect();
  if (br.width === 0 && br.height === 0) return; // 元素存在但还没渲染出尺寸
  if (br.bottom < 0 || br.top > innerHeight || br.right < 0 || br.left > innerWidth) {
      _toggleEl.style.display = 'none'; 
      return;
  }
  _toggleEl.style.display = 'flex';
  const tr = _toggleEl.getBoundingClientRect();
  const pos = c.autoSendTogglePos || 'right';
  let left, top;
  switch (pos) { 
      case 'right': left = br.right; top = br.top + br.height / 2 - tr.height / 2; break; 
      case 'left': left = br.left - tr.width; top = br.top + br.height / 2 - tr.height / 2; break; 
      case 'top': left = br.left + br.width / 2 - tr.width / 2; top = br.top - tr.height; break; 
      case 'bottom': left = br.left + br.width / 2 - tr.width / 2; top = br.bottom; break; 
  }
  _toggleEl.style.left = left + 'px'; 
  _toggleEl.style.top = top + 'px';
}

function _destroyAutoSendToggle() { 
    if (_togglePosTimer) { clearInterval(_togglePosTimer); _togglePosTimer = null; } 
    if (_toggleEl) { _toggleEl.remove(); _toggleEl = null; } 
}

// --- 文件粘贴处理 ---

async function _pasteFile(filename, b64Data) {
    const c = cfgLoad();
    const input = document.querySelector(c.selInputBox);
    const btn = document.querySelector(c.selSendButton);
    if (!input || !btn) { 
        log('ERR', '找不到输入框或发送按钮'); 
        _isProcessing = false; 
        _checkAndDispatch(); 
        return; 
    }
    
    try {
        const byteChars = atob(b64Data); 
        const byteArr = new Uint8Array(byteChars.length); 
        for (let i = 0; i < byteChars.length; i++) byteArr[i] = byteChars.charCodeAt(i);
        
        const ext = filename.split('.').pop().toLowerCase();
        const mimeMap = { 
            'js': 'text/javascript', 'ts': 'text/typescript', 'html': 'text/html', 
            'css': 'text/css', 'json': 'application/json', 'md': 'text/markdown', 
            'py': 'text/x-python', 'txt': 'text/plain', 'xml': 'text/xml', 'csv': 'text/csv' 
        };
        const file = new File([byteArr], filename, { type: mimeMap[ext] || 'text/plain' });
        
        input.focus();
        const dt = new DataTransfer(); 
        dt.items.add(file);
        const pasteEvt = new ClipboardEvent('paste', { bubbles: true, cancelable: true }); 
        Object.defineProperty(pasteEvt, 'clipboardData', { get() { return dt; } }); 
        input.dispatchEvent(pasteEvt);
        
        log('OK', '已触发文件粘贴事件');
        await _smartWait(input, { checkDOM: true, minWait: 300 });
        
        if (c.autoSendByEnter) { 
            try { _trySendByEnter(input); } catch (err) { log('ERR', `模拟回车发送失败: ${err.message}`); } 
        }
        try { btn.click(); } catch (err) { log('ERR', `点击发送失败: ${err.message}`); }
    } catch (err) {
        log('ERR', `文件粘贴失败: ${err.message}`);
    }
    
    // 文件处理完毕，释放锁并检查后续指令
    _isProcessing = false;
    if (_cmdQueue.length > 0) _checkAndDispatch();
}

/* ================================================================
 * 7. 启动入口
 * ================================================================ */

GM_registerMenuCommand('⚙️ Agent 配置面板', showPanel);
if (cfgLoad().debugMode) setTimeout(initDebugUI, 500);

function initAgent() {
    if (_pollTimer) { clearInterval(_pollTimer); _pollTimer = null; }
    _lastAnswerEl = null; 
    _currentRoundSent.clear();
    
    _initAutoSendToggle();

    const c = cfgLoad(); 
    const selector = c.selChatContainer;
    let currentContainer = document.querySelector(selector);
    
    if (!currentContainer) { 
        log('WARN', `找不到容器 ${selector}，5秒后重试...`); 
        setTimeout(initAgent, 5000); 
        return; 
    }
    
    _knownAnswers = [...currentContainer.querySelectorAll('.answer')]; 
    _lastAnswerEl = null; 
    _currentRoundSent.clear();
    _pollConfig();  
    log('OK', `✅ 监听已启动！`);
    
    // 核心轮询
    _pollTimer = setInterval(() => {
        try {
            _heartbeatCounter++;
            const freshContainer = document.querySelector(selector); 
            if (!freshContainer) return;
            
            // 容器被替换（如新对话）
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
            
            const answers = [...currentContainer.querySelectorAll('.answer')];
            // 对话被清空
            if (_knownAnswers.length > 0 && !_knownAnswers.some(el => new Set(answers).has(el))) {
                _lastAnswerEl = null; 
                _currentRoundSent.clear(); 
                _cmdQueue = []; 
                _sendPromiseChain = Promise.resolve(); 
                _isProcessing = false;
                log('WARN', '🚨 对话被清空，重置状态...');
            }
            
            _knownAnswers = answers;
            if (_heartbeatCounter % 20 === 0) log('INFO', `💓 心跳 | 队列${_cmdQueue.length}条 | 锁定:${_isProcessing}`);
            if (answers.length === 0) return;
            
            const lastAnswer = answers[answers.length - 1];
            if (lastAnswer !== _lastAnswerEl) { 
                _lastAnswerEl = lastAnswer; 
                _currentRoundSent.clear(); 
            }
            
            // 正则提取指令
            const re = /【cmd】([\s\S]*?)【\/cmd】/g; 
            const text = getCleanText(lastAnswer); 
            re.lastIndex = 0; 
            let m;
            
            while ((m = re.exec(text)) !== null) {
                const cmdStr = m[1].trim(); 
                const normKey = m[0].replace(/[ \t]+/g, ' ').replace(/ *\n */g, '\n');
                if (_currentRoundSent.has(normKey)) continue;
                
                _currentRoundSent.add(normKey); 
                _cmdQueue.push(cmdStr);
                log('OK', `🎉 捕获指令入队: ${cmdStr.substring(0, 60)}...`);
                
                // 核心改变：只要抓到新指令，就尝试唤醒调度器
                if (!_isProcessing) _checkAndDispatch();
            }
        } catch (err) { 
            console.error('[Agent-ERR] 轮询异常:', err); 
        }
    }, 800); // 缩短到 0.8 秒，更灵敏抓取
}

// 初始化入口
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

// ==UserScript==
// @name         PokerAgent
// @namespace    http://tampermonkey.net/
// @version      49
// @author       LMaxRouterCN
// @description  PokerAgent的浏览器端核心脚本，提供元素选择、配置管理、调试日志等功能，支持多站点独立配置和自动发送功能。
// @match        *://*/*
// @grant        GM_registerMenuCommand
// @grant        GM_unregisterMenuCommand
// @grant        GM_xmlhttpRequest
// @grant        GM_getValue
// @grant        GM_setValue
// @grant        GM_addStyle
// @grant        GM_addValueChangeListener
// @run-at       document-start
// 【新增】@run-at document-start：最早时机注入，抢在页面bundle缓存任何API引用之前完成补丁安装
// @connect      localhost
// @connect      127.0.0.1
// ==/UserScript==

//UI风格:黑灰白黄红橙绿,主黑金配色
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
      selSendButtonContainer: '',
      selAnswerItem: '.answer',
      selCodeContentElement: '',
      selCodeCopyButton: '',
      cleanIgnoreClassKeywords: 'thinking,reasoning,probe,deepseek-reason',
      cleanRemoveButtonLike: true,
      cleanRemovePre: true,
      textCleanRules: [],
      codeTrimStart: 0,
      codeTrimEnd: 0,
      sendBtnBusyFingerprints: [],
      sendBtnIdleFingerprints: [],
      sendBtnSendableFingerprints: [],
      sendDebounceDelay: 100,
      copyInterceptTimeout: 800,
      // 【新增】复制拦截消费窗口毫秒数：超时则放弃拦截回退读元素文本。竞态规则是先到先得(settled即回收)，放宽不增加正常路径耗时
      verifyMode: 'single',
      verifyRetryTimes: 30,
      verifyRetryInterval: 1000,
      waitDelayAfterDone: 500,
      showAutoSendToggle: false,
      autoSendTogglePos: 'right',
      autoSendMode: 'click',
      memoryInjectFrequency: 1
    };
  
    const DEFAULTS = {
      whitelist: ['https://chatglm.cn/'],
      debugMode: false,
      ...SITE_DEFAULTS
    };
  
    const STORE_KEY = 'low_cost_agent_config_v4';
  
    let _storeCache = null; // 【新增·改动12】存储内存缓存：热路径(每mutation/每log一次cfgLoad)免GM读+迁移+三层合并
    let _storeCacheDirty = true; // 【新增·改动12】写时失效：本页写同步更新缓存，他页写由值变更监听置脏
  
    // 【新增·改动12】跨标签页配置同步(事件驱动)：他页保存时置脏本地缓存，下次读取重载
    if (typeof GM_addValueChangeListener === 'function') {
      GM_addValueChangeListener(STORE_KEY, (key, oldVal, newVal, remote) => {
        if (remote) _storeCacheDirty = true;
      });
    }
  
    function _loadStore() {
      // 【改·改动12】缓存命中短路：未置脏直接复用；迁移(_migrateStore)只随失效执行，不再每读一遍
      if (_storeCache && !_storeCacheDirty) return _storeCache;
      let store;
      try {
        store = GM_getValue(STORE_KEY, null);
      } catch (_) {
        store = null;
      }
      if (!store) {
        store = {
          whitelist: ['https://chatglm.cn/'],
          debugMode: false,
          defaults: { ...SITE_DEFAULTS },
          perSite: {
            'https://chatglm.cn/': { ...SITE_DEFAULTS, selChatContainer: 'div.chatScrollContainer' }
          }
        };
      } else {
        store = _migrateStore(store);
      }
      _storeCache = store;
      _storeCacheDirty = false;
      return store;
    }
  
    function _saveStore(store) {
      GM_setValue(STORE_KEY, store);
      _storeCache = store; // 【新增·改动12】写穿缓存：同引用同步，写后读一致
      _storeCacheDirty = false;
    }
  
    function _migrateStore(store) {
      const clearOld = (cfg) => {
        if (cfg.sendBtnIdleFingerprint !== undefined && cfg.sendBtnIdleFingerprint !== '') {
          cfg.sendBtnBusyFingerprints = [];
          cfg.sendBtnIdleFingerprints = [];
          delete cfg.sendBtnIdleFingerprint;
        }
        if (!cfg.sendBtnBusyFingerprints) cfg.sendBtnBusyFingerprints = [];
        if (!cfg.sendBtnIdleFingerprints) cfg.sendBtnIdleFingerprints = [];
        if (!cfg.sendBtnSendableFingerprints) cfg.sendBtnSendableFingerprints = [];
        if (cfg.sendDebounceDelay === undefined) cfg.sendDebounceDelay = 100;
        if (!cfg.verifyMode) cfg.verifyMode = 'single';
        if (cfg.verifyRetryTimes === undefined) cfg.verifyRetryTimes = 30;
        if (cfg.verifyRetryInterval === undefined) cfg.verifyRetryInterval = 1000;
        if (!cfg.waitDelayAfterDone) cfg.waitDelayAfterDone = 500;
        if (cfg.autoSendByEnter !== undefined && cfg.autoSendMode === undefined) {
          cfg.autoSendMode = cfg.autoSendByEnter ? 'enter' : 'click';
          delete cfg.autoSendByEnter;
        }
        if (!cfg.autoSendMode) cfg.autoSendMode = 'click';
        if (cfg.autoSendByEnter !== undefined) delete cfg.autoSendByEnter;
        if (!cfg.selAnswerItem) cfg.selAnswerItem = '.answer';
        if (!cfg.selCodeContentElement) cfg.selCodeContentElement = '';
        if (cfg.selCodeCopyButton === undefined) cfg.selCodeCopyButton = '';
        if (cfg.selSendButtonContainer === undefined) cfg.selSendButtonContainer = '';
        if (cfg.cleanIgnoreClassKeywords === undefined) cfg.cleanIgnoreClassKeywords = 'thinking,reasoning,probe,deepseek-reason';
        if (cfg.cleanRemoveButtonLike === undefined) cfg.cleanRemoveButtonLike = true;
        if (cfg.cleanRemovePre === undefined) cfg.cleanRemovePre = true;
        if (!cfg.textCleanRules) cfg.textCleanRules = [];
        if (cfg.codeTrimStart === undefined) cfg.codeTrimStart = 0;
        if (cfg.codeTrimEnd === undefined) cfg.codeTrimEnd = 0;
        if (cfg.memoryInjectFrequency === undefined) cfg.memoryInjectFrequency = 1;
        if (cfg.copyInterceptTimeout === undefined) cfg.copyInterceptTimeout = 800; // 【新增】存量配置补发默认拦截窗口
      };
      if (store.defaults && store.perSite !== undefined) {
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
  
    // 【删·改动17】cfgSave() 整函数删除：全项目零调用点（已核实）
  
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
     * 1.5 启用状态管理
     * ================================================================ */
    const ENABLE_MODE_KEY = 'pokeragent_enable_mode';
    const PAGE_SESSION_KEY = '__PokerAgent_PageEnabled__';
    let _sessionEnabled = false;
    let _pollConfigActive = false;
  
    function _getEnableState() {
      if (_sessionEnabled) return 'session';
      if (sessionStorage.getItem(PAGE_SESSION_KEY) === '1') return 'page';
      const globalMode = GM_getValue(ENABLE_MODE_KEY, 'disabled');
      if (globalMode === 'always') return 'always';
      return 'disabled';
    }
  
    function _setEnableState(state) {
      _sessionEnabled = false;
      GM_setValue(ENABLE_MODE_KEY, 'disabled');
      sessionStorage.removeItem(PAGE_SESSION_KEY);
      switch (state) {
        case 'always': GM_setValue(ENABLE_MODE_KEY, 'always'); break;
        case 'session': _sessionEnabled = true; break;
        case 'page': sessionStorage.setItem(PAGE_SESSION_KEY, '1'); break;
      }
    }
  
    const ENABLE_LABELS = {
      disabled: '不启用',
      always: '默认启用',
      session: '此次会话启用',
      page: '当前页面启用'
    };
  
    function _stopAgent() {
      _initToken++; // 【新增·改动2】作废所有在途初始化与重试（原逻辑拦不住挂起中的initAgent苏醒）
      ++_sessionEpoch; // 【新增·修复J】停止也推进会话代际：在途扫描苏醒后被守卫丢弃（防僵尸入列+派发）。
      // 认领：改动11的🪦守卫注释写着"停止/重初始化"，但stop路径从未推进epoch——
      // 守卫对stop一直是死代码，我在v45/v46两轮复审都没发现，此行补上闭环
      if (_containerWaitStop) {
        _containerWaitStop();
        _containerWaitStop = null;
      } // 【新增·修复E】停止时清理容器等待观察器
      _pollConfigActive = false; // 【删·改动2】原 _pollTimer clearInterval 死代码
      if (_domObserver) {
        _domObserver.disconnect();
        _domObserver = null;
      }
      _scanPending = false; // 【改·修复J】勿重置_scanRunning：旧泵在途会自然排空，强行清零会放出第二泵重现并发
      _tasksFinished = false;
      _destroyAutoSendToggle();
      _isProcessing = false;
      _finalSendInProgress = false; // 【新增·修复M·自决】停止时释放收口互斥：防止挂在_waitForLLMFinish硬等中的旧收口器把新会话的收口永久锁死
      _cmdQueue = [];
      _taskList = [];
      if (_sseEventSource) {
        try {
          _sseEventSource.abort();
        } catch (e) { }
        _sseEventSource = null;
      }
      log('INFO', '⏹ Agent 已停止');
    }
  
    let _enableMenuIds = [];
  
    function _registerEnableMenus() {
      _enableMenuIds.forEach(id => {
        try {
          GM_unregisterMenuCommand(id);
        } catch (e) { }
      });
      _enableMenuIds = [];
      const current = _getEnableState();
      const modes = ['disabled', 'always', 'session', 'page'];
      modes.forEach(mode => {
        const prefix = current === mode ? '✓ ' : '';
        const id = GM_registerMenuCommand(`${prefix}${ENABLE_LABELS[mode]}`, () => _switchEnableState(mode));
        _enableMenuIds.push(id);
      });
    }
  
    function _switchEnableState(mode) {
      const current = _getEnableState();
      if (current === mode) return;
      _setEnableState(mode);
      log('INFO', `启用状态: ${ENABLE_LABELS[current]} → ${ENABLE_LABELS[mode]}`);
      if (mode === 'disabled') {
        _stopAgent();
        if (_debugPanel) _debugPanel.style.display = 'none';
      } else {
        if (cfgLoad().debugMode) showDebug();
        initAgent().catch(e => {
          log('ERR', `热启动异常: ${e.message}`);
          console.error('[Agent] initAgent failed:', e);
        });
      }
      _registerEnableMenus();
    }
  
    /* ================================================================
     * 2. 样式注入
     * ================================================================ */
    GM_addStyle(`
  #agent-panel{position:fixed;top:50%;left:50%;transform:translate(-50%,-50%);width:min(540px,92vw);max-height:82vh;overflow-y:auto;background:#0a0a0a;color:#d4d4d4;border:1px solid #2a2a2a;border-radius:0;box-shadow:0 24px 80px rgba(0,0,0,.55);z-index:2147483647;font:14px/1.5 system-ui,sans-serif}
  #agent-panel *{box-sizing:border-box;margin:0;padding:0}
  #agent-panel-head{display:flex;align-items:center;justify-content:space-between;padding:14px 20px;border-bottom:1px solid #2a2a2a}
  #agent-panel-head b{font-size:15px;color:#facc15}
  #agent-panel-close{background:none;border:none;color:#a0a0a0;font-size:20px;cursor:pointer;padding:2px 8px;border-radius:0;transition:.15s}
  #agent-panel-close:hover{background:#2a2a2a;color:#ef4444}
  #agent-panel-body{padding:20px}
  .ag-sec{margin-bottom:18px}
  .ag-sec-title{font-size:11px;font-weight:700;color:#a0a0a0;text-transform:uppercase;letter-spacing:.8px;margin-bottom:8px;display:flex;align-items:center;gap:6px}
  .ag-sec-title::before{content:'';width:3px;height:13px;background:#facc15;border-radius:0}
  .ag-field{margin-bottom:10px}
  .ag-field label{display:block;font-size:12px;color:#d4d4d4;margin-bottom:4px}
  .ag-row{display:flex;gap:6px;align-items:center}
  .ag-inp{flex:1;min-width:0;background:#1a1a1a;border:1px solid #2a2a2a;color:#ffffff;padding:7px 10px;border-radius:0;font-size:12px;outline:none;transition:.15s;font-family:'SF Mono',Consolas,monospace}
  .ag-inp:focus{border-color:#facc15}
  .ag-btn{padding:7px 13px;border:none;border-radius:0;font-size:12px;font-weight:600;cursor:pointer;transition:.15s;white-space:nowrap}
  .ag-btn-p{background:#facc15;color:#0a0a0a}
  .ag-btn-p:hover{background:#fde047}
  .ag-btn-g{background:#1a1a1a;color:#d4d4d4;border:1px solid #2a2a2a}
  .ag-btn-g:hover{border-color:#facc15;color:#facc15}
  .ag-wl-list{max-height:110px;overflow-y:auto;background:#1a1a1a;border-radius:0;padding:3px;margin-bottom:6px}
  .ag-wl-item{display:flex;align-items:center;gap:6px;padding:5px 10px;border-radius:0;font-size:12px}
  .ag-wl-item code{flex:1;min-width:0;color:#22c55e;word-break:break-all;font-family:'SF Mono',Consolas,monospace;font-size:11px}
  .ag-wl-rm{background:none;border:none;color:#ef4444;cursor:pointer;font-size:14px;padding:0 4px;opacity:.5}
  .ag-wl-rm:hover{opacity:1}
  .ag-match{font-size:11px;padding:3px 8px;border-radius:0;margin-top:3px}
  .ag-m-ok{background:rgba(34,197,94,.12);color:#22c55e}
  .ag-m-fail{background:rgba(239,68,68,.12);color:#ef4444}
  .ag-m-none{background:rgba(160,160,160,.1);color:#a0a0a0}
  .ag-foot{display:flex;justify-content:flex-end;gap:8px;padding-top:14px;border-top:1px solid #2a2a2a;margin-top:6px}
  .ag-toggle{display:flex;align-items:center;gap:10px}
  .ag-toggle input[type=checkbox]{width:16px;height:16px;accent-color:#facc15}
  .ag-pos-group{display:flex;gap:0}
  .ag-pos-btn{padding:4px 10px;background:#1a1a1a;border:1px solid #2a2a2a;color:#a0a0a0;font-size:12px;cursor:pointer;transition:.15s;border-radius:0}
  .ag-pos-btn+.ag-pos-btn{border-left:none}
  .ag-pos-btn.active{background:#facc15;color:#0a0a0a;border-color:#facc15}
  .ag-pos-btn:hover:not(.active){border-color:#facc15;color:#facc15}
  .ag-site-info{background:#1a1a1a;padding:10px 14px;margin-bottom:10px;border:1px solid #2a2a2a}
  .ag-site-row{display:flex;align-items:center;gap:8px;margin-bottom:4px;font-size:12px}
  .ag-site-row:last-child{margin-bottom:0}
  .ag-site-label{color:#a0a0a0;min-width:56px;flex-shrink:0}
  .ag-site-value{color:#d4d4d4;word-break:break-all}
  .ag-site-badge{font-size:10px;padding:1px 6px;flex-shrink:0;border-radius:0}
  .ag-badge-ok{background:rgba(34,197,94,.12);color:#22c55e}
  .ag-badge-fail{background:rgba(239,68,68,.12);color:#ef4444}
  .ag-site-actions{display:flex;gap:6px;margin-bottom:14px;flex-wrap:wrap}
  .ag-hint{font-size:11px;color:#737373;margin-top:2px}
  .ag-rule-list{max-height:220px;overflow-y:auto;background:#1a1a1a;border-radius:0;padding:6px;margin-bottom:6px;display:flex;flex-direction:column;gap:8px}
  .ag-rule-item{background:#1a1a1a;border:1px solid #2a2a2a;padding:8px;border-radius:0}
  .ag-rule-item .ag-inp{font-size:11px;padding:5px 8px}
  #agent-pick-dim{position:fixed;inset:0;background:rgba(0,0,0,.28);z-index:2147483645;pointer-events:none}
  #agent-pick-hl{position:fixed;border:2.5px solid #facc15;background:rgba(250,204,21,.08);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;box-shadow:0 0 0 4000px rgba(0,0,0,.25);display:none}
  #agent-pick-lock-hl{position:fixed;border:2.5px solid #ef4444;background:rgba(239,68,68,.06);border-radius:0;pointer-events:none;z-index:2147483646;transition:left .06s,top .06s,width .06s,height .06s;display:none}
  #agent-pick-tip{position:fixed;background:#0a0a0a;color:#fef08a;border:1px solid #2a2a2a;padding:5px 10px;border-radius:0;font:11px/1.4 'SF Mono',Consolas,monospace;z-index:2147483647;pointer-events:none;max-width:560px;word-break:break-all;box-shadow:0 4px 16px rgba(0,0,0,.4);opacity:0;transition:opacity .08s;display:flex;flex-direction:column;gap:3px}
  .ag-tip-sel{display:flex;align-items:center;gap:0;flex-wrap:wrap}
  .ag-diag-line{display:flex;align-items:center;gap:4px;flex-wrap:wrap;font-size:10px;color:#a0a0a0;border-top:1px solid #2a2a2a;padding-top:3px}
  .ag-diag-text{color:#22c55e;max-width:200px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap}
  .ag-diag-children{color:#d4d4d4}
  .ag-diag-size{color:#737373}
  .ag-diag-ok{color:#22c55e}
  .ag-diag-warn{color:#facc15}
  .ag-diag-err{color:#ef4444}
  .ag-diag-shadow{color:#f97316;background:rgba(249,115,22,.15);padding:0 4px}
  .ag-diag-scroll{color:#facc15;background:rgba(250,204,21,.1);padding:0 4px}
  .ag-diag-sep{color:#2a2a2a;margin:0 1px}
  #agent-pick-bar{position:fixed;top:14px;left:50%;transform:translateX(-50%);background:#0a0a0a;color:#d4d4d4;border:1px solid #facc15;padding:10px 28px;border-radius:0;font-size:14px;z-index:2147483647;box-shadow:0 6px 24px rgba(0,0,0,.5);pointer-events:none}
  #agent-pick-level{color:#22c55e; margin-left: 8px; font-weight: bold;}
  #ag-show-levels{pointer-events:auto;cursor:pointer;color:#ef4444;margin-right:8px;border-right:1px solid #2a2a2a;padding-right:8px;white-space:nowrap;flex-shrink:0;transition:color .1s}
  #ag-show-levels:hover{color:#fca5a5}
  .ag-level-panel{position:fixed;width:min(420px,85vw);max-height:340px;background:#0a0a0a;border:1px solid #ef4444;border-radius:0;box-shadow:0 8px 32px rgba(0,0,0,.55);z-index:2147483647;display:none;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace;color:#d4d4d4;}
  .ag-level-head{display:flex;align-items:center;justify-content:space-between;padding:8px 12px;border-bottom:1px solid #2a2a2a;font-weight:600;color:#ef4444;flex-shrink:0;}
  .ag-level-head button{background:none;border:none;color:#a0a0a0;cursor:pointer;font-size:16px;padding:0 4px;}
  .ag-level-head button:hover{color:#ef4444}
  .ag-level-body{flex:1;overflow-y:auto;padding:4px;}
  .ag-level-body::-webkit-scrollbar{width:4px}
  .ag-level-body::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:0}
  .ag-level-item{display:flex;align-items:center;gap:6px;padding:6px 8px;cursor:pointer;border-left:2px solid transparent;transition:background .1s;}
  .ag-level-item:hover{background:rgba(239,68,68,.1);border-left-color:#ef4444;}
  .ag-level-target{background:rgba(239,68,68,.06);border-left-color:#ef4444;}
  .ag-level-idx{color:#737373;font-size:10px;min-width:18px;text-align:right;flex-shrink:0;}
  .ag-level-tag{color:#22c55e;font-weight:600;min-width:60px;flex-shrink:0;}
  .ag-level-digest{color:#fde68a;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;font-style:italic;}
  .ag-level-sel{color:#737373;font-size:10px;max-width:120px;overflow:hidden;text-overflow:ellipsis;white-space:nowrap;flex-shrink:0;}
  #agent-debug{position:fixed;top:10px;right:10px;width:380px;max-height:60vh;background:rgba(10,10,10,.92);border:1px solid #2a2a2a;border-radius:0;box-shadow:0 10px 40px rgba(0,0,0,.5);z-index:2147483644;display:flex;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace;backdrop-filter:blur(8px);color:#a0a0a0;}
  #agent-debug-head{padding:8px 12px;border-bottom:1px solid #2a2a2a;display:flex;justify-content:space-between;align-items:center;color:#d4d4d4}
  #agent-debug-body{flex:1;overflow-y:auto;padding:8px;display:flex;flex-direction:column;gap:4px}
  #agent-debug-body::-webkit-scrollbar{width:4px}
  #agent-debug-body::-webkit-scrollbar-thumb{background:#2a2a2a;border-radius:0}
  .ag-log{padding:4px 6px;border-radius:0;word-break:break-all;background:rgba(255,255,255,.03);border-left:3px solid transparent}
  .ag-log-time{color:#737373;margin-right:6px}
  .ag-log-info{border-left-color:#facc15;color:#fef08a}
  .ag-log-warn{border-left-color:#f97316;color:#fed7aa;background:rgba(249,115,22,.05)}
  .ag-log-err{border-left-color:#ef4444;color:#fca5a5;background:rgba(239,68,68,.05)}
  .ag-log-ok{border-left-color:#22c55e;color:#bbf7d0;background:rgba(34,197,94,.05)}
  #agent-debug-foot{padding:6px 12px;border-top:1px solid #2a2a2a;text-align:right}
  .ag-dbg-btn{background:#1a1a1a;border:1px solid #2a2a2a;color:#a0a0a0;padding:3px 10px;border-radius:0;cursor:pointer;font-size:11px}
  .ag-dbg-btn:hover{border-color:#facc15;color:#facc15}
  #agent-auto-send-toggle{position:fixed;z-index:2147483640;display:flex;flex-direction:column;align-items:stretch;background:#0a0a0a;border:1px solid #2a2a2a;pointer-events:auto;white-space:nowrap;user-select:none;opacity:0.85;transition:opacity .15s;}
  #agent-auto-send-toggle:hover{opacity:1}
  .ag-as-opts{display:flex;flex-direction:column;padding:4px 4px 4px 8px}
  .ag-as-opt{font-size:10px;color:#737373;cursor:pointer;padding:5px 2px;line-height:1.3;transition:color .15s;font-family:system-ui,sans-serif}
  .ag-as-opt:hover{color:#d4d4d4}
  .ag-as-opt.active{color:#facc15}
  .ag-as-rail{width:16px;position:relative;display:flex;justify-content:center;border-left:1px solid #2a2a2a;padding:4px 0}
  .ag-as-rail::before{content:'';position:absolute;top:8px;bottom:8px;width:2px;background:#2a2a2a}
  .ag-as-thumb{position:absolute;left:50%;transform:translateX(-50%);width:10px;height:10px;background:#facc15;transition:top .25s ease;z-index:1}
  .ag-as-main{display:flex;flex-direction:row;align-items:stretch}
  .ag-as-mem{display:flex;justify-content:space-between;align-items:center;gap:12px;padding:5px 4px 5px 8px;cursor:pointer;border-bottom:1px solid #2a2a2a}
  .ag-as-mem:hover{background:#1a1a1a}
  .ag-as-mem-label{font-size:10px;color:#737373;font-family:system-ui,sans-serif}
  .ag-as-mem-val{font-size:10px;color:#facc15;font-family:system-ui,sans-serif}
  .ag-as-mem-body{display:none;padding:5px 8px;border-bottom:1px solid #2a2a2a}
  .ag-as-mem-opts{display:flex;flex-wrap:wrap;gap:2px 8px;max-width:150px}
  .ag-as-mem-opt{font-size:10px;color:#737373;cursor:pointer;padding:3px 0;font-family:system-ui,sans-serif;transition:color .15s}
  .ag-as-mem-opt:hover{color:#d4d4d4}
  .ag-as-mem-opt.active{color:#facc15}
  .ag-as-mem-custom{display:flex;gap:4px;margin-top:5px;align-items:center}
  .ag-as-mem-custom input{flex:1;min-width:60px;background:#1a1a1a;border:1px solid #2a2a2a;color:#d4d4d4;font-size:10px;padding:3px 5px;outline:none;border-radius:0}
  .ag-as-mem-custom input:focus{border-color:#facc15}
  .ag-as-mem-custom button{background:none;border:1px solid #2a2a2a;color:#facc15;font-size:10px;cursor:pointer;padding:3px 8px;border-radius:0}
  .ag-as-mem-custom button:hover{border-color:#facc15}
  #ag-calibrate-bar{position:fixed;top:20px;left:50%;transform:translateX(-50%);background:#0a0a0a;color:#fed7aa;border:1px solid #facc15;padding:14px 24px;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);font:13px/1.5 system-ui,sans-serif;display:none;flex-direction:column;align-items:center;gap:10px;pointer-events:auto;width:min(600px,90vw)}
  #ag-calibrate-bar b{color:#facc15}
  #ag-calibrate-cards{position:fixed;top:100px;left:50%;transform:translateX(-50%);background:#0a0a0a;border:1px solid #2a2a2a;z-index:2147483647;box-shadow:0 8px 32px rgba(0,0,0,.6);width:min(220px,45vw);overflow-y:auto;overflow-x:hidden;padding:10px;cursor:move}
  #ag-calibrate-cards::-webkit-scrollbar{width:4px}
  #ag-calibrate-cards::-webkit-scrollbar-thumb{background:#2a2a2a}
  .ag-cal-item{display:flex;flex-direction:column;align-items:center;gap:6px;padding:6px;background:#1a1a1a;transition:.15s;width:100%;box-sizing:border-box;overflow:hidden;position:relative;z-index:0;border:1px solid transparent;min-height:0;flex-shrink:0}
  .ag-cal-item.selected-busy{background:rgba(239,68,68,.1);border-color:#ef4444}
  .ag-cal-item.selected-idle{background:rgba(34,197,94,.1);border-color:#22c55e}
  .ag-cal-item.selected-sendable{background:rgba(250,204,21,.1);border-color:#facc15}
  .ag-cal-clone{width:100%;height:60px;max-height:60px;overflow:hidden;display:flex;align-items:center;justify-content:center;background:#0a0a0a}
  .ag-cal-actions{display:flex;flex-direction:column;gap:4px;width:100%;align-items:center}
  .ag-cal-tag{font-size:10px;padding:2px 8px;border:1px solid #2a2a2a;color:#a0a0a0;cursor:pointer;background:none;white-space:nowrap;width:60%;text-align:center}
  .ag-cal-tag:hover{border-color:#facc15;color:#facc15}
  .ag-cal-tag.active-busy{border-color:#ef4444;color:#ef4444;background:rgba(239,68,68,.2)}
  .ag-cal-tag.active-idle{border-color:#22c55e;color:#22c55e;background:rgba(34,197,94,.2)}
  .ag-cal-tag.active-sendable{border-color:#facc15;color:#facc15;background:rgba(250,204,21,.2)}
  #ag-test-pop{position:fixed;z-index:2147483647;background:#0a0a0a;border:1px solid #facc15;box-shadow:0 8px 32px rgba(0,0,0,.6);width:420px;height:300px;min-width:240px;min-height:150px;max-width:90vw;max-height:80vh;resize:both;overflow:hidden;display:none;flex-direction:column;font:12px/1.5 'SF Mono',Consolas,monospace;color:#d4d4d4}
  #ag-test-pop-head{display:flex;justify-content:space-between;align-items:center;padding:6px 10px;border-bottom:1px solid #2a2a2a;color:#facc15;background:#1a1a1a;flex-shrink:0;cursor:move;user-select:none}
  #ag-test-pop-head button{background:none;border:none;color:#a0a0a0;cursor:pointer;font-size:16px;padding:0 4px}
  #ag-test-pop-head button:hover{color:#ef4444}
  #ag-test-pop-body{flex:1;overflow-y:auto;overflow-x:hidden;white-space:pre-wrap;word-break:break-word;padding:10px}
  #ag-test-pop-body::-webkit-scrollbar{width:4px}
  #ag-test-pop-body::-webkit-scrollbar-thumb{background:#2a2a2a}
  #ag-test-pop::-webkit-resizer{background:#0a0a0a linear-gradient(135deg,transparent 50%,#facc15 50%)}
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
      _debugPanel.innerHTML = `<div id="agent-debug-head"><span>🕵️ Agent 调试台</span><button class="ag-dbg-btn" id="ag-dbg-close">隐藏</button></div><div id="agent-debug-body"></div><div id="agent-debug-foot"><button class="ag-dbg-btn" id="ag-dbg-clear">清空日志</button></div>`;
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
      const now = new Date();
      const time = now.toLocaleTimeString('zh-CN', { hour12: false }) + '.' + String(now.getMilliseconds()).padStart(3, '0');
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
      chat: '聊天记录容器',
      input: '输入框',
      send: '发送按钮',
      'send-container': '发送按钮容器',
      answer: 'AI回答元素',
      'clean-class': '清理元素Class',
      'code-content': '代码内容元素',
      'code-copy-button': '代码复制按钮'
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
        try {
          if (document.querySelectorAll(sel).length === 1) return sel;
        } catch (_) { }
      }
      for (const attr of ['data-testid', 'data-test-id', 'data-role', 'data-cy']) {
        const val = el.getAttribute(attr);
        if (val) {
          const sel = `${el.tagName.toLowerCase()}[${attr}="${CSS.escape(val)}"]`;
          try {
            if (document.querySelectorAll(sel).length === 1) return sel;
          } catch (_) { }
        }
      }
      const role = el.getAttribute('role');
      if (role) {
        const sel = `${el.tagName.toLowerCase()}[role="${CSS.escape(role)}"]`;
        try {
          if (document.querySelectorAll(sel).length === 1) return sel;
        } catch (_) { }
      }
      if (el.className && typeof el.className === 'string') {
        const allCls = el.className.trim().split(/\s+/).filter(c => c);
        const cleanCls = allCls.filter(c => !_isPureHashClass(c) && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c) && !/[_-][a-f0-9]{5,8}$/i.test(c));
        if (cleanCls.length) {
          const sel = `${el.tagName.toLowerCase()}.${cleanCls.map(c => CSS.escape(c)).join('.')}`;
          try {
            if (document.querySelectorAll(sel).length === 1) return sel;
          } catch (_) { }
        }
        const hashCls = allCls.filter(c => !_isPureHashClass(c) && !/^(_|-{2})/.test(c) && !/^(is|has|can|should)/.test(c) && /[_-][a-f0-9]{5,8}$/i.test(c));
        if (hashCls.length) {
          const stripped = hashCls.map(c => _stripClassHash(c)).filter(s => s.length >= 3);
          if (stripped.length) {
            const sel = `${el.tagName.toLowerCase()}${stripped.map(s => `[class*="${CSS.escape(s)}"]`).join('')}`;
            try {
              if (document.querySelectorAll(sel).length === 1) return sel;
            } catch (_) { }
          }
        }
      }
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
      try {
        if (document.querySelectorAll(sel).length === 1) return sel;
      } catch (_) { }
      return sel;
    }
  
    function pickerEnter(type) {
      _pickActive = true;
      _pickType = type;
      _pickedEl = null;
      _lockedBaseEl = null;
      _domStack = [];
      hidePanel();
      _pickDim = document.createElement('div');
      _pickDim.id = 'agent-pick-dim';
      document.body.appendChild(_pickDim);
      _pickHL = document.createElement('div');
      _pickHL.id = 'agent-pick-hl';
      document.body.appendChild(_pickHL);
      _pickLockHL = document.createElement('div');
      _pickLockHL.id = 'agent-pick-lock-hl';
      document.body.appendChild(_pickLockHL);
      _pickTip = document.createElement('div');
      _pickTip.id = 'agent-pick-tip';
      document.body.appendChild(_pickTip);
      _pickBar = document.createElement('div');
      _pickBar.id = 'agent-pick-bar';
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
      if (['input', 'textarea', 'select'].includes(tag)) {
        const v = el.value;
        return v ? `${tag}[value="${v.slice(0, 50)}"]` : `${tag}`;
      }
      if (tag === 'img') {
        const alt = el.alt ? `alt="${el.alt.slice(0, 30)}"` : '';
        const src = el.src ? el.src.split('/').pop().slice(0, 40) : '';
        return `img${alt ? ' ' + alt : ''}${src ? ' src=…/' + src : ''}`;
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
      Object.assign(_pickHL.style, {
        left: (r.left - 2) + 'px',
        top: (r.top - 2) + 'px',
        width: (r.width + 4) + 'px',
        height: (r.height + 4) + 'px'
      });
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
      _pickTip.innerHTML = _lockedBaseEl
        ? `<span class="ag-tip-sel"><span id="ag-show-levels">展开所有层级</span><span>${esc(sel)} ←${tag}${digestStr}</span></span>${diagHtml}`
        : `<span class="ag-tip-sel"><span>${esc(sel)} ←${tag}${digestStr}</span></span>${diagHtml}`;
      _pickTip.style.opacity = '1';
      _pickTip.style.left = Math.min(mouseX + 14, innerWidth - 510) + 'px';
      _pickTip.style.top = (mouseY + 22) + 'px';
    }
  
    function _updateLockHL() {
      if (!_pickLockHL || !_lockedBaseEl) return;
      const r = _lockedBaseEl.getBoundingClientRect();
      _pickLockHL.style.display = 'block';
      Object.assign(_pickLockHL.style, {
        left: (r.left - 2) + 'px',
        top: (r.top - 2) + 'px',
        width: (r.width + 4) + 'px',
        height: (r.height + 4) + 'px'
      });
    }
  
    function _syncHighlightPositions() {
      if (_pickHL && _pickedEl) {
        const r = _pickedEl.getBoundingClientRect();
        Object.assign(_pickHL.style, {
          left: (r.left - 2) + 'px',
          top: (r.top - 2) + 'px',
          width: (r.width + 4) + 'px',
          height: (r.height + 4) + 'px'
        });
      }
      if (_pickLockHL && _lockedBaseEl) {
        const r = _lockedBaseEl.getBoundingClientRect();
        Object.assign(_pickLockHL.style, {
          left: (r.left - 2) + 'px',
          top: (r.top - 2) + 'px',
          width: (r.width + 4) + 'px',
          height: (r.height + 4) + 'px'
        });
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
      while (cur && cur !== document.documentElement) {
        chain.unshift(cur);
        cur = cur.parentElement;
      }
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
      _levelPanel.querySelector('#ag-level-close').onclick = (e) => {
        e.stopPropagation();
        _levelPanel.style.display = 'none';
      };
      _levelPanel.querySelectorAll('.ag-level-item').forEach(item => {
        item.onclick = (e) => {
          e.stopPropagation();
          _confirmSelection(chain[+item.dataset.idx]);
        };
      });
    }
  
    function _onClick(e) {
      if (_levelPanel && _levelPanel.style.display !== 'none' && _levelPanel.contains(e.target)) return;
      e.stopPropagation();
      e.preventDefault();
      if (_levelPanel && _levelPanel.style.display !== 'none') {
        _levelPanel.style.display = 'none';
        return;
      }
      if (e.target.id === 'ag-show-levels') {
        _showLevelPanel();
        return;
      }
      if (e.shiftKey || e.ctrlKey) {
        if (_pickedEl) _confirmSelection(_pickedEl);
        else {
          const el = _targetAt(e.clientX, e.clientY);
          if (el) _confirmSelection(el);
        }
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
          } else {
            log('WARN', '已到达顶层 body，无法继续向上');
          }
        } else {
          _hideLockHL();
          _domStack = [];
          _lockedBaseEl = target || null;
          _pickedEl = target || null;
          if (_pickedEl) {
            _updateLockHL();
            log('INFO', `点击超出锁定范围，重新选择: <${_pickedEl.tagName.toLowerCase()}>`);
            _highlightEl(_pickedEl, e.clientX, e.clientY);
          } else {
            _pickHL.style.display = 'none';
          }
          _updateBarInfo();
        }
      }
      _highlightEl(_pickedEl, e.clientX, e.clientY);
      _updateBarInfo();
    }
  
    function _onCtx(e) {
      if (_levelPanel && _levelPanel.style.display !== 'none' && _levelPanel.contains(e.target)) return;
      e.stopPropagation();
      e.preventDefault();
      if (_levelPanel && _levelPanel.style.display !== 'none') {
        _levelPanel.style.display = 'none';
        return;
      }
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
        } else {
          _pickHL.style.display = 'none';
        }
        _updateBarInfo();
        return;
      }
      if (_domStack.length > 0) {
        _pickedEl = _domStack.pop();
        log('INFO', `向下回退至: <${_pickedEl.tagName.toLowerCase()}> (栈深度: ${_domStack.length})`);
        _highlightEl(_pickedEl, e.clientX, e.clientY);
        _updateBarInfo();
      } else {
        log('WARN', '已在最底层，无法回退');
      }
    }
  
    function _updateBarInfo() {
      if (!_pickBar || !_pickedEl) return;
      _pickBar.innerHTML = `🎯 当前层级: <span style="color:#86efac">${_domStack.length}</span> (${_pickedEl.tagName.toLowerCase()}) | <span style="font-size:12px;opacity:0.7">左键↑ 右键↓ Shift+点击确认</span>`;
    }
  
    function _confirmSelection(el) {
      // 【新增·改动10】ShadowDOM守卫：选择器生成器无法表达穿越Shadow边界的路径，
      // 保存的配置在外层querySelectorAll中必然命中不了——提前拦截，避免存入死配置
      if (el.getRootNode && el.getRootNode() !== document) {
        log('ERR', '目标位于ShadowDOM内部，无法生成可用的CSS选择器，本次选择已取消。请改选Shadow宿主元素或其外层元素');
        pickerExit();
        return;
      }
      if (_pickType === 'clean-class') {
        let classes = [];
        if (el.className && typeof el.className === 'string') {
          classes = el.className.trim().split(/\s+/).filter(c => c && !_isPureHashClass(c) && !/^(_|-{2})/.test(c));
        }
        if (classes.length === 0) {
          log('WARN', '该元素没有有效的class，请重新选择');
          pickerExit();
          return;
        }
        const c = cfgLoad();
        let currentKws = (c.cleanIgnoreClassKeywords || '').split(',').map(s => s.trim()).filter(s => s);
        let added = [];
        classes.forEach(cls => {
          if (!currentKws.includes(cls)) {
            currentKws.push(cls);
            added.push(cls);
          }
        });
        cfgSaveRuntime({ cleanIgnoreClassKeywords: currentKws.join(',') });
        log('OK', `已添加Class关键词: ${added.join(', ')}`);
        pickerExit();
        return;
      }
      const sel = genSelector(el);
      if (!sel) {
        log('ERR', '无法生成选择器');
        return;
      }
      // 【改·改动3】类型→配置键显式映射，只写当前类型的单个键。
      // 原写法把cfgLoad()的全量merged对象（含whitelist/debugMode）回写进存储，
      // 且defaults.debugMode会在cfgLoad合并顺序中覆盖全局调试开关
      const KEY_MAP = {
        'chat': 'selChatContainer',
        'input': 'selInputBox',
        'send': 'selSendButton',
        'send-container': 'selSendButtonContainer',
        'answer': 'selAnswerItem',
        'code-content': 'selCodeContentElement',
        'code-copy-button': 'selCodeCopyButton'
      };
      const key = KEY_MAP[_pickType];
      if (!key) {
        log('ERR', `未知选择类型: ${_pickType}`);
        return;
      }
      cfgSaveRuntime({ [key]: sel });
      // 【新增·修复B】Agent因缺容器未启动时，旧版靠5s轮询兜底拾取新配置；轮询删除后需显式接管。
      // 仅在"未启动"(_domObserver为空)时重启：运行中重启会清去重表→历史指令重放
      if (_getEnableState() !== 'disabled' && !_domObserver) {
        initAgent();
      }
      log('OK', `已选择 [${TYPE_LABEL[_pickType]}]:${sel}`);
      const ctxChain = [];
      let cur = el;
      while (cur && cur !== document.documentElement) {
        const tag = cur.tagName ? cur.tagName.toLowerCase() : '#document';
        const digest = _getElementDigest(cur);
        ctxChain.push(`${tag}${digest !== tag ? '(' + digest + ')' : ''}`);
        cur = cur.parentElement;
      }
      log('INFO', `上下文链: ${ctxChain.reverse().join(' > ')}`);
      log('INFO', `目标元素详情: <${el.tagName.toLowerCase()}>, class="${el.className}", id="${el.id}"`);
      pickerExit();
    }
  
    function _onKey(e) {
      if (e.key === 'Escape') {
        e.stopPropagation();
        e.preventDefault();
        pickerExit();
      }
      if (e.key === 'Enter' && _pickedEl) {
        e.stopPropagation();
        e.preventDefault();
        _confirmSelection(_pickedEl);
      }
    }
  
    /* ================================================================
     * 5. 配置面板
     * ================================================================ */
    let _panel = null;
    let _editTarget = 'defaults';
  
    function showPanel() {
      if (!_panel) {
        _panel = document.createElement('div');
        _panel.id = 'agent-panel';
        document.body.appendChild(_panel);
      }
      const store = _loadStore();
      const site = _matchSite();
      const inWhitelist = !!site;
      const hasSiteCfg = site && store.perSite && store.perSite[site];
      _editTarget = hasSiteCfg ? site : 'defaults';
      _renderPanel();
      _panel.style.display = 'block';
    }
  
    function hidePanel() {
      if (_panel) _panel.style.display = 'none';
    }
  
    function _renderRules(rules) {
      const list = _panel.querySelector('#ag-rule-list');
      if (!list) return;
      if (!rules || rules.length === 0) {
        list.innerHTML = '<div style="padding:8px 10px;color:#52525b;font-size:12px;text-align:center">暂无规则，请点击下方按钮添加</div>';
        return;
      }
      list.innerHTML = rules.map((rule, idx) => `
        <div class="ag-rule-item" data-idx="${idx}">
          <div class="ag-row" style="margin-bottom:6px">
            <input class="ag-inp rule-find" placeholder="查找内容" value="${esc(rule.find || '')}" style="flex:1.2">
            <input class="ag-inp rule-replace" placeholder="替换为" value="${esc(rule.replace || '')}" style="flex:1">
          </div>
          <div class="ag-row" style="flex-wrap:wrap">
            <label class="ag-toggle" style="font-size:11px;margin:0;cursor:pointer"><input type="checkbox" class="rule-regex" ${rule.isRegex ? 'checked' : ''}> 正则</label>
            <label class="ag-toggle" style="font-size:11px;margin:0;cursor:pointer"><input type="checkbox" class="rule-unicode" ${rule.isUnicode ? 'checked' : ''}> Unicode</label>
            <label class="ag-toggle" style="font-size:11px;margin:0;cursor:pointer"><input type="checkbox" class="rule-enabled" ${rule.enabled !== false ? 'checked' : ''}> 启用</label>
            <button class="ag-btn ag-btn-g rule-del" style="padding:3px 8px;color:#ef4444;margin-left:auto">✕ 删除</button>
          </div>
        </div>
      `).join('');
    }
  
    function _collectRulesFromDOM() {
      const items = _panel.querySelectorAll('.ag-rule-item');
      const rules = [];
      items.forEach(item => {
        rules.push({
          find: item.querySelector('.rule-find').value,
          replace: item.querySelector('.rule-replace').value,
          isRegex: item.querySelector('.rule-regex').checked,
          isUnicode: item.querySelector('.rule-unicode').checked,
          enabled: item.querySelector('.rule-enabled').checked
        });
      });
      return rules;
    }
  
    function _renderPanel() {
      const store = _loadStore();
      const site = _matchSite();
      const inWhitelist = !!site;
      const hasSiteCfg = site && store.perSite && store.perSite[site];
      const source = _getConfigSource();
      let editCfg;
      if (_editTarget === 'defaults') {
        editCfg = { ...SITE_DEFAULTS, ...(store.defaults || {}) };
      } else {
        editCfg = { ...SITE_DEFAULTS, ...(store.perSite?.[_editTarget] || {}) };
      }
      // 【改·改动5】_editTarget 来自白名单键，可含任意用户输入，须经esc
      const titleText = _editTarget === 'defaults' ? '🔧 Poker Agent 配置 — 默认设置' : `🔧 Poker Agent 配置 — ${esc(_editTarget)} 独立设置`;
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
          actionsHtml += `<button class="ag-btn ag-btn-g" id="ag-del-site" style="color:#ef4444">删除独立配置</button>`;
        } else {
          actionsHtml += `<button class="ag-btn ag-btn-g" id="ag-create-site">为此网站创建独立配置</button>`;
        }
      }
      _panel.innerHTML = `<div id="agent-panel-head"><b>${titleText}</b><button id="agent-panel-close">✕</button></div><div id="agent-panel-body"><div class="ag-site-info"><div class="ag-site-row"><span class="ag-site-label">当前网站:</span><span class="ag-site-value">${esc(siteDisplay)}</span><span class="ag-site-badge ${badgeClass}">${badgeText}</span></div><div class="ag-site-row"><span class="ag-site-label">当前使用:</span><span class="ag-site-value" style="color:#818cf8">${esc(sourceDisplay)}</span></div></div><div class="ag-site-actions">${actionsHtml}</div><div class="ag-sec"><div class="ag-sec-title">控制台</div><div class="ag-toggle"><input type="checkbox" id="ag-debug-toggle" ${store.debugMode ? 'checked' : ''} /><label for="ag-debug-toggle" style="cursor:pointer">启用调试模式 (右侧显示日志浮窗)</label></div></div><div class="ag-sec"><div class="ag-sec-title">网站白名单</div><div class="ag-wl-list" id="ag-wl-list">${store.whitelist.length ? store.whitelist.map((u, i) => `<div class="ag-wl-item"><code>${esc(u)}</code><button class="ag-wl-rm" data-i="${i}">✕</button></div>`).join('') : '<div style="padding:8px 10px;color:#52525b;font-size:12px">暂无</div>'}</div><div class="ag-row"><input class="ag-inp" id="ag-wl-new" placeholder="https://example.com/" /><button class="ag-btn ag-btn-g" id="ag-wl-add">添加</button></div></div><div class="ag-sec"><div class="ag-sec-title">本地 Agent 服务</div><div class="ag-field"><label>接收指令的 HTTP 地址</label><input class="ag-inp" id="ag-api" value="${esc(editCfg.apiUrl)}" /><div class="ag-hint">默认仅本机(localhost/127.0.0.1)开箱即用；指向其他主机需在脚本头部补对应 @connect 声明</div></div></div><div class="ag-sec"><div class="ag-sec-title">页面元素绑定</div><div class="ag-field"><label>聊天记录容器</label><div class="ag-row"><input class="ag-inp" id="ag-s-chat" value="${esc(editCfg.selChatContainer)}" /><button class="ag-btn ag-btn-p" id="ag-pick-chat">🖱 选择</button></div><div id="ag-m-chat"></div></div><div class="ag-field"><label>AI回答元素</label><div class="ag-row"><input class="ag-inp" id="ag-s-answer" value="${esc(editCfg.selAnswerItem)}" /><button class="ag-btn ag-btn-p" id="ag-pick-answer">🖱 选择</button><button class="ag-btn ag-btn-g" id="ag-test-answer">🧪 测试</button></div><div id="ag-m-answer"></div><div class="ag-hint">用于从聊天容器中定位AI的回复，默认 .answer；如不匹配请用选择器选取</div></div><div class="ag-field"><label>代码内容元素 (必需)</label><div class="ag-row"><input class="ag-inp" id="ag-s-code-content" value="${esc(editCfg.selCodeContentElement)}" placeholder="如：pre, .code-block" /><button class="ag-btn ag-btn-p" id="ag-pick-code-content">🖱 选择</button><button class="ag-btn ag-btn-g" id="ag-test-code-content">🧪 测试</button></div><div id="ag-m-code-content"></div></div><div class="ag-field"><label>代码复制按钮 (可选)</label><div class="ag-row"><input class="ag-inp" id="ag-s-code-copy-btn" value="${esc(editCfg.selCodeCopyButton)}" placeholder="如：button.copy, .icon-copy" /><button class="ag-btn ag-btn-p" id="ag-pick-code-copy-btn">🖱 选择</button><button class="ag-btn ag-btn-g" id="ag-test-code-copy-btn">🧪 测试</button></div><div id="ag-m-code-copy-btn"></div><div class="ag-hint">如果配置，将点击此按钮拦截剪贴板内容；失败则回退到读取代码元素文本。</div></div><div class="ag-field" style="margin-top:8px"><label>代码文本裁剪</label><div class="ag-row"><input class="ag-inp" id="ag-trim-start" type="number" value="${editCfg.codeTrimStart || 0}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0;margin-right:12px">去掉开头字符数</span><input class="ag-inp" id="ag-trim-end" type="number" value="${editCfg.codeTrimEnd || 0}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">去掉末尾字符数</span></div></div><div class="ag-field"><label>复制拦截窗口 (毫秒)</label><div class="ag-row"><input class="ag-inp" id="ag-clip-timeout" type="number" min="0" value="${editCfg.copyInterceptTimeout ?? 800}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">点击复制按钮后等待闸门广播的最大时长</span></div></div><div class="ag-field"><label>输入框</label><div class="ag-row"><input class="ag-inp" id="ag-s-input" value="${esc(editCfg.selInputBox)}" /><button class="ag-btn ag-btn-p" id="ag-pick-input">🖱 选择</button></div><div id="ag-m-input"></div></div><div class="ag-field"><label>发送按钮</label><div class="ag-row"><input class="ag-inp" id="ag-s-send" value="${esc(editCfg.selSendButton)}" /><button class="ag-btn ag-btn-p" id="ag-pick-send">🖱 选择</button></div><div id="ag-m-send"></div><div class="ag-field" style="margin-top:6px"><label>发送按钮容器 (可选)</label><div class="ag-row"><input class="ag-inp" id="ag-s-send-container" value="${esc(editCfg.selSendButtonContainer)}" /><button class="ag-btn ag-btn-p" id="ag-pick-send-container">🖱 选择</button></div><div id="ag-m-send-container"></div><div class="ag-hint">如果网站在不同状态下会完全替换按钮元素（而非修改属性），请选择按钮的父容器。填写后指纹基于容器内容生成，selSendButton 仍可用于容器内精确定位点击目标。</div></div><div class="ag-field" id="ag-calibrate-field" style="margin-top:6px; padding:8px; background:#232436; border:1px solid #2a2a2a; display:${(editCfg.selSendButton || editCfg.selSendButtonContainer) ? 'block' : 'none'};"><div style="font-size:12px; color:#a1a1aa; margin-bottom:6px">捕获按钮的各种形态，手动标记【忙碌】(AI输出时)和【空闲】态。</div><div class="ag-row"><div id="ag-calibrate-status" style="flex:1; font-size:11px; color:#52525b"> 忙碌:${(editCfg.sendBtnBusyFingerprints || []).length}个 | 空闲:${(editCfg.sendBtnIdleFingerprints || []).length}个 | 可发送:${(editCfg.sendBtnSendableFingerprints || []).length}个 </div><button class="ag-btn ag-btn-p" id="ag-start-calibrate">${(editCfg.sendBtnBusyFingerprints || []).length > 0 ? '重新校准' : '开始校准'}</button></div></div></div><div class="ag-field" style="margin-top:12px;padding-top:10px;border-top:1px solid #2a2a2a"><label>输出完毕判断逻辑</label><div class="ag-row" style="margin-bottom:6px"><input type="radio" name="verifyMode" id="ag-mode-single" value="single" ${editCfg.verifyMode !== 'double' ? 'checked' : ''} /><label for="ag-mode-single" style="font-size:12px;cursor:pointer;margin-right:12px">单验证 (脱离忙碌即放行)</label><input type="radio" name="verifyMode" id="ag-mode-double" value="double" ${editCfg.verifyMode === 'double' ? 'checked' : ''} /><label for="ag-mode-double" style="font-size:12px;cursor:pointer">双验证 (需进入空闲态)</label></div><div class="ag-row"><label style="font-size:12px;color:#a0a0a0;white-space:nowrap">放行前额外延时</label><input class="ag-inp" id="ag-wait-delay" type="number" value="${editCfg.waitDelayAfterDone ?? 500}" style="width:80px" /></div><div class="ag-field" style="margin-top:8px"><label>回执验证重试次数</label><div class="ag-row"><input class="ag-inp" id="ag-verify-retry-times" type="number" value="${editCfg.verifyRetryTimes ?? 30}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">LLM说完后若输入框内容被破坏，尝试强制覆盖的次数</span></div></div><div class="ag-field"><label>回执验证重试间隔</label><div class="ag-row"><input class="ag-inp" id="ag-verify-retry-interval" type="number" value="${editCfg.verifyRetryInterval ?? 1000}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">每次强制覆盖后的等待毫秒数</span></div></div></div><label>发送模式选择器</label><div class="ag-toggle" style="margin-bottom:6px"><input type="checkbox" id="ag-show-toggle" ${editCfg.showAutoSendToggle ? 'checked' : ''} /><label for="ag-show-toggle" style="cursor:pointer">在发送按钮旁显示发送模式选择器</label></div><div class="ag-row"><label style="font-size:11px;color:#a0a0a0;white-space:nowrap">位置</label><div class="ag-pos-group"><button class="ag-pos-btn" data-pos="left">← 左</button><button class="ag-pos-btn" data-pos="top">↑ 上</button><button class="ag-pos-btn" data-pos="right">→ 右</button><button class="ag-pos-btn" data-pos="bottom">↓ 下</button></div></div><div class="ag-field" style="margin-top:8px"><label>发送前防抖延时</label><div class="ag-row"><input class="ag-inp" id="ag-debounce-delay" type="number" value="${editCfg.sendDebounceDelay ?? 100}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">检测到可发送状态后等待的毫秒数，0=不等待，默认100</span></div></div></div><div class="ag-sec"><div class="ag-sec-title">内容清理规则</div><div class="ag-field"><label>忽略的class关键词 (逗号分隔)</label><div class="ag-row"><input class="ag-inp" id="ag-clean-keywords" value="${esc(editCfg.cleanIgnoreClassKeywords)}" /><button class="ag-btn ag-btn-p" id="ag-pick-clean-keyword">🖱 选择</button></div><div class="ag-hint">包含这些关键词的class所在元素会被移除，支持用选择器直接抓取行号等干扰元素的class</div></div><div class="ag-toggle" style="margin-bottom:6px"><input type="checkbox" id="ag-clean-buttons" ${editCfg.cleanRemoveButtonLike !== false ? 'checked' : ''} /><label for="ag-clean-buttons" style="cursor:pointer">移除按钮/操作类元素</label></div><div class="ag-toggle"><input type="checkbox" id="ag-clean-pre" ${editCfg.cleanRemovePre !== false ? 'checked' : ''} /><label for="ag-clean-pre" style="cursor:pointer">移除pre代码块 (除非含【CodeSTART】)</label></div></div><div class="ag-sec"><div class="ag-sec-title">记忆系统</div><div class="ag-field"><label>记忆注入频率（每N轮，0=关闭）</label><div class="ag-row"><input class="ag-inp" id="ag-memory-freq" type="number" value="${editCfg.memoryInjectFrequency ?? 1}" style="width:80px" /><span style="font-size:11px;color:#a0a0a0">每N轮对话注入一次记忆上下文，0=关闭</span></div></div></div><div class="ag-sec"><div class="ag-sec-title">指令文本清洗规则</div><div class="ag-hint" style="margin-bottom:6px">在指令发送给后端前，按顺序执行以下替换规则。开启 Unicode 可解析 \\uXXXX 或 U+XXXX。</div><div class="ag-rule-list" id="ag-rule-list"></div><div class="ag-row" style="margin-top:8px"><button class="ag-btn ag-btn-g" id="ag-add-rule">➕ 添加规则</button></div></div><div class="ag-foot"><button class="ag-btn ag-btn-g" id="ag-cancel">取消</button><button class="ag-btn ag-btn-p" id="ag-save">${saveText}</button></div></div>`;
      _panel.querySelector('#agent-panel-close').onclick = hidePanel;
      _panel.querySelector('#ag-cancel').onclick = hidePanel;
      _panel.querySelector('#ag-debug-toggle').onchange = (e) => {
        const s = _loadStore();
        s.debugMode = e.target.checked;
        _saveStore(s);
        if (e.target.checked) {
          showDebug();
          log('INFO', '调试模式已开启');
        } else {
          if (_debugPanel) _debugPanel.style.display = 'none';
        }
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
      _panel.querySelector('#ag-pick-chat').onclick = () => pickerEnter('chat');
      _panel.querySelector('#ag-pick-answer').onclick = () => pickerEnter('answer');
      _panel.querySelector('#ag-pick-code-content').onclick = () => pickerEnter('code-content');
      _panel.querySelector('#ag-pick-code-copy-btn').onclick = () => pickerEnter('code-copy-button');
      _panel.querySelector('#ag-pick-input').onclick = () => pickerEnter('input');
      _panel.querySelector('#ag-pick-send').onclick = () => pickerEnter('send');
      _panel.querySelector('#ag-pick-send-container').onclick = () => pickerEnter('send-container');
      _panel.querySelector('#ag-pick-clean-keyword').onclick = () => pickerEnter('clean-class');
      _panel.querySelector('#ag-test-answer').onclick = (e) => _runExtractTest('answer', e.currentTarget);
      _panel.querySelector('#ag-test-code-content').onclick = (e) => _runExtractTest('code-content', e.currentTarget);
      _panel.querySelector('#ag-test-code-copy-btn').onclick = (e) => _runExtractTest('code-copy-btn', e.currentTarget);
      if (editCfg.selSendButton || editCfg.selSendButtonContainer) {
        _panel.querySelector('#ag-start-calibrate').onclick = () => {
          if (!editCfg.selSendButton && !editCfg.selSendButtonContainer) {
            alert('请先选择发送按钮或容器');
            return;
          }
          _startCalibration();
        };
      }
      const posBtns = _panel.querySelectorAll('.ag-pos-btn');
      posBtns.forEach(btn => {
        if (btn.dataset.pos === editCfg.autoSendTogglePos) btn.classList.add('active');
        btn.onclick = () => {
          posBtns.forEach(b => b.classList.remove('active'));
          btn.classList.add('active');
        };
      });
      ['chat', 'input', 'send', 'send-container', 'answer', 'code-content', 'code-copy-btn'].forEach(t => {
        const key = t === 'chat' ? 'selChatContainer' : t === 'input' ? 'selInputBox' : t === 'send' ? 'selSendButton' : t === 'send-container' ? 'selSendButtonContainer' : t === 'answer' ? 'selAnswerItem' : t === 'code-content' ? 'selCodeContentElement' : 'selCodeCopyButton';
        _panel.querySelector(`#ag-s-${t}`).addEventListener('input', function () {
          _showMatch(this.value.trim(), `ag-m-${t}`);
          if (t === 'send' || t === 'send-container') {
            const sendVal = _panel.querySelector('#ag-s-send').value.trim();
            const containerVal = _panel.querySelector('#ag-s-send-container').value.trim();
            _panel.querySelector('#ag-calibrate-field').style.display = (sendVal || containerVal) ? 'block' : 'none';
          }
        });
        _showMatch(editCfg[key], `ag-m-${t}`);
      });
      _renderRules(editCfg.textCleanRules || []);
      _panel.querySelector('#ag-add-rule').onclick = () => {
        const current = _collectRulesFromDOM();
        current.push({ find: '', replace: '', isRegex: false, isUnicode: false, enabled: true });
        _renderRules(current);
      };
      _panel.querySelector('#ag-rule-list').onclick = (e) => {
        if (e.target.classList.contains('rule-del')) {
          const item = e.target.closest('.ag-rule-item');
          const idx = parseInt(item.dataset.idx);
          const current = _collectRulesFromDOM();
          current.splice(idx, 1);
          _renderRules(current);
        }
      };
      _panel.querySelector('#ag-save').onclick = () => {
        const s = _loadStore();
        s.debugMode = _panel.querySelector('#ag-debug-toggle').checked;
        const siteData = { ...SITE_DEFAULTS };
        siteData.apiUrl = _panel.querySelector('#ag-api').value.trim() || SITE_DEFAULTS.apiUrl;
        siteData.selChatContainer = _panel.querySelector('#ag-s-chat').value.trim();
        siteData.selAnswerItem = _panel.querySelector('#ag-s-answer').value.trim() || '.answer';
        siteData.selCodeContentElement = _panel.querySelector('#ag-s-code-content').value.trim();
        siteData.selCodeCopyButton = _panel.querySelector('#ag-s-code-copy-btn').value.trim();
        siteData.selInputBox = _panel.querySelector('#ag-s-input').value.trim();
        siteData.selSendButton = _panel.querySelector('#ag-s-send').value.trim();
        siteData.selSendButtonContainer = _panel.querySelector('#ag-s-send-container').value.trim();
        siteData.showAutoSendToggle = _panel.querySelector('#ag-show-toggle').checked;
        const activePos = _panel.querySelector('.ag-pos-btn.active');
        siteData.autoSendTogglePos = activePos ? activePos.dataset.pos : 'right';
        siteData.verifyMode = _panel.querySelector('input[name="verifyMode"]:checked').value;
        // 【改·改动4】0值语义统一：与运行时同规则对齐
        const _wd2 = parseInt(_panel.querySelector('#ag-wait-delay').value);
        siteData.waitDelayAfterDone = isNaN(_wd2) ? 500 : Math.max(0, _wd2); // 0为合法值，仅NaN回落500
        const _vt2 = parseInt(_panel.querySelector('#ag-verify-retry-times').value);
        siteData.verifyRetryTimes = isNaN(_vt2) ? 30 : Math.max(1, _vt2); // 与运行时同规则(至少1次)
        const _vi2 = parseInt(_panel.querySelector('#ag-verify-retry-interval').value);
        siteData.verifyRetryInterval = isNaN(_vi2) ? 1000 : Math.max(0, _vi2);
        siteData.sendBtnBusyFingerprints = editCfg.sendBtnBusyFingerprints || [];
        siteData.sendBtnIdleFingerprints = editCfg.sendBtnIdleFingerprints || [];
        siteData.sendBtnSendableFingerprints = editCfg.sendBtnSendableFingerprints || [];
        siteData.sendDebounceDelay = Math.max(0, parseInt(_panel.querySelector('#ag-debounce-delay').value) || 0); // 【改·改动4】钳制负值
        siteData.autoSendMode = cfgLoad().autoSendMode || 'click'; // 【改·改动6】读实时存储而非面板渲染快照，避免面板打开期间浮窗切换被保存回滚
        siteData.cleanIgnoreClassKeywords = _panel.querySelector('#ag-clean-keywords').value.trim();
        siteData.cleanRemoveButtonLike = _panel.querySelector('#ag-clean-buttons').checked;
        siteData.cleanRemovePre = _panel.querySelector('#ag-clean-pre').checked;
        siteData.textCleanRules = _collectRulesFromDOM();
        siteData.codeTrimStart = parseInt(_panel.querySelector('#ag-trim-start').value) || 0;
        siteData.codeTrimEnd = parseInt(_panel.querySelector('#ag-trim-end').value) || 0;
        const _clipT = parseInt(_panel.querySelector('#ag-clip-timeout').value);
        siteData.copyInterceptTimeout = isNaN(_clipT) ? 800 : Math.max(0, _clipT); // 0为合法值，仅空/非法输入回落默认800
        siteData.memoryInjectFrequency = Math.max(0, parseInt(_panel.querySelector('#ag-memory-freq').value) || 0); // 【改·改动4】钳制负值
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
      if (!sel) {
        el.innerHTML = '<div class="ag-match ag-m-none">未设置</div>';
        return;
      }
      try {
        const n = document.querySelectorAll(sel).length;
        el.innerHTML = n === 0 ? '<div class="ag-match ag-m-fail">✘ 未匹配</div>' : n === 1 ? '<div class="ag-match ag-m-ok">✔ 精确匹配 1 个</div>' : `<div class="ag-match ag-m-ok">✔ 匹配 ${n} 个</div>`;
      } catch (_) {
        el.innerHTML = '<div class="ag-match ag-m-fail">✘ 语法错误</div>';
      }
    }
  
    /* ================================================================
     * 5.5 提取链路测试 (🧪 按钮调试用悬浮框)
     * ================================================================ */
    let _testPop = null;
  
    function _showTestPop(anchorBtn, title, text) {
      if (!_testPop) {
        _testPop = document.createElement('div');
        _testPop.id = 'ag-test-pop';
        _testPop.innerHTML = `<div id="ag-test-pop-head"><span id="ag-test-pop-title"></span><button id="ag-test-pop-close">✕</button></div><div id="ag-test-pop-body"></div>`;
        document.body.appendChild(_testPop);
        _makeDraggable(_testPop, _testPop.querySelector('#ag-test-pop-head'));
        _testPop.querySelector('#ag-test-pop-close').onclick = () => _testPop.style.display = 'none';
      }
      _testPop.style.display = 'flex';
      _testPop.querySelector('#ag-test-pop-title').textContent = title;
      _testPop.querySelector('#ag-test-pop-body').textContent = text;
      if (anchorBtn) {
        const br = anchorBtn.getBoundingClientRect();
        const w = _testPop.offsetWidth || 420, h = _testPop.offsetHeight || 300;
        let left = br.right + 8;
        if (left + w > innerWidth - 8) left = Math.max(8, br.left - w - 8);
        let top = br.top;
        if (top + h > innerHeight - 8) top = Math.max(8, innerHeight - h - 8);
        _testPop.style.left = left + 'px';
        _testPop.style.top = top + 'px';
      }
    }
  
    function _buildLiveTestCfg() {
      const c = cfgLoad();
      c.selChatContainer = _panel.querySelector('#ag-s-chat').value.trim();
      c.selAnswerItem = _panel.querySelector('#ag-s-answer').value.trim() || '.answer';
      c.selCodeContentElement = _panel.querySelector('#ag-s-code-content').value.trim();
      c.selCodeCopyButton = _panel.querySelector('#ag-s-code-copy-btn').value.trim();
      return c;
    }
  
    function _queryAllSafe(root, sel) {
      try {
        return [...root.querySelectorAll(sel)];
      } catch (e) {
        return null;
      }
    }
  
    function _locateLastAnswer(c) {
      let scope = document;
      let note = '';
      if (c.selChatContainer) {
        try {
          scope = document.querySelector(c.selChatContainer) || null;
        } catch (e) {
          scope = null;
        }
        if (!scope) return { err: `❌ 聊天容器选择器未命中: "${c.selChatContainer}"` };
      } else {
        note = '⚠ 未配置聊天容器，已全局查询（与运行时行为不同）\n\n';
      }
      const answers = _queryAllSafe(scope, c.selAnswerItem);
      if (answers === null) return { err: `❌ 回答选择器语法错误: "${c.selAnswerItem}"` };
      if (answers.length === 0) return { err: `❌ 回答选择器 "${c.selAnswerItem}" 未命中任何元素` };
      return { el: answers[answers.length - 1], note, count: answers.length };
    }
  
    async function _runExtractTest(type, anchor) {
      const c = _buildLiveTestCfg();
      const pop = (title, text) => _showTestPop(anchor, title, text);
      if (type === 'answer') {
        const loc = _locateLastAnswer(c);
        if (loc.err) return pop('🧪 AI回答元素测试', loc.err);
        const logs = [];
        const text = await getCleanText(loc.el, c, logs);
        const logText = logs.map(([lv, msg]) => `[${lv}] ${msg}`).join('\n');
        return pop('🧪 AI回答元素测试', `${loc.note}📊 命中 ${loc.count} 个回答元素，处理最后一个\n` +
          `━━━ 提取链路日志 ━━━\n${logText || '(无日志)'}\n` +
          `━━━ 最终文本 (${text.length} 字符) ━━━\n${text}`);
      }
      if (type === 'code-content') {
        if (!c.selCodeContentElement) return pop('🧪 代码内容元素测试', '❌ 未填写代码内容元素选择器');
        const loc = _locateLastAnswer(c);
        if (loc.err) return pop('🧪 代码内容元素测试', loc.err);
        const codeEls = _queryAllSafe(loc.el, c.selCodeContentElement);
        if (codeEls === null) return pop('🧪 代码内容元素测试', `❌ 语法错误: "${c.selCodeContentElement}"`);
        if (codeEls.length === 0) return pop('🧪 代码内容元素测试', `❌ 在最后一个回答内未命中: "${c.selCodeContentElement}"`);
        let out = `${loc.note}📊 最后一个回答内命中 ${codeEls.length} 个代码元素`;
        codeEls.forEach((el, i) => {
          const t = el.textContent || '';
          out += `\n\n━━━ 元素 [${i + 1}/${codeEls.length}] (${t.length} 字符) ━━━\n${t}`;
        });
        return pop('🧪 代码内容元素测试', out);
      }
      if (type === 'code-copy-btn') {
        if (!c.selCodeCopyButton) return pop('🧪 复制按钮测试', '❌ 未填写代码复制按钮选择器');
        if (!c.selCodeContentElement) return pop('🧪 复制按钮测试', '❌ 复制按钮依赖代码内容元素定位范围，请先填写代码内容元素选择器');
        const loc = _locateLastAnswer(c);
        if (loc.err) return pop('🧪 复制按钮测试', loc.err);
        const codeEls = _queryAllSafe(loc.el, c.selCodeContentElement);
        if (codeEls === null) return pop('🧪 复制按钮测试', `❌ 代码内容元素语法错误: "${c.selCodeContentElement}"`);
        if (codeEls.length === 0) return pop('🧪 复制按钮测试', '❌ 代码内容元素未命中，无法定位复制按钮范围');
        let out = `${loc.note}📊 最后一个回答内命中 ${codeEls.length} 个代码块，逐个点击复制按钮拦截…`;
        for (let i = 0; i < codeEls.length; i++) {
          const found = _findCopyButton(codeEls[i], c.selCodeCopyButton, c.selCodeContentElement, loc.el);
          out += `\n\n━━━ 代码块 [${i + 1}/${codeEls.length}] ━━━\n`;
          if (!found || !found.btn) {
            out += (found && found.blocked) ? `⚠ 找到候选按钮但归属校验拦截（该层含多个代码元素，可能属于相邻代码块）` : `⚠ 未找到复制按钮 (选择器: "${c.selCodeCopyButton}")`;
            continue;
          }
          if (found.depth > 0) out += `📍 按钮位于代码元素外第 ${found.depth} 层（块头部栏结构）\n`;
          try {
            const captured = await _interceptCopy(found.btn);
            if (captured && captured.trim().length > 0) {
              out += `✅ 拦截成功 (${captured.length} 字符)\n${captured}`;
            } else {
              out += `⚠ 点击后未拦截到复制内容`;
            }
          } catch (err) {
            out += `⚠ 点击异常: ${err.message}`;
          }
        }
        return pop('🧪 复制按钮测试', out);
      }
    }
  
    /* ================================================================
     * 6. Agent 核心逻辑
     * ================================================================ */
    let _clipboardMode = false;
    let _permissionEnabled = true;
    let _isProcessing = false;
    let _finalSendInProgress = false; // 【新增·修复M】收口互斥：多个传输批次完成都会触发收口尝试，防双发送器
    let _cmdQueue = [];
    // 【删·改动2】let _sendPromiseChain = Promise.resolve(); （死代码）
    let _isCalibrating = false;
    let _dispatchRetryCount = 0;
    const MAX_DISPATCH_RETRIES = 3;
    const TASK_START = '\n<|im_start|>pokeragent-system\n=== Poker Agent Task ===\n';
    const TASK_END = '\n=== Poker Agent Task End ===\n<|im_end|>\n';
  
    function _agentEndpoint(path) {
      // 【新增·改动7】端点统一派生：七处散落的 apiUrl.replace('/agent-exec', ...) 收敛到单一出口。
      // apiUrl 配置格式向后兼容(仍填完整exec地址)，仅剥离尾部后拼显式路径
      const base = cfgLoad().apiUrl.replace(/\/agent-exec\/?$/, '');
      return base + path;
    }
  
    let _taskList = [];
    let _sseEventSource = null;
    let _roundCount = 0;
    let _lastMemoryInjectRound = 0;
    let _domObserver = null;
    let _containerWaitStop = null; // 【新增·修复E】容器等待观察器句柄
    let _initToken = 0; // 【新增·改动2】初始化令牌：每次initAgent自增；旧链在挂起恢复点自检后作废
    let _pollConfigSeq = 0; // 【新增·改动2】轮询链令牌：与_initToken配对，旧轮询链自杀
    let _scanPending = false; // 【改·修复J】待处理mutation批次标志（替代_scanScheduled）
    let _scanRunning = false; // 【新增·修复J】扫描串行锁：覆盖整个异步扫描周期直至泵排空
    let _tasksFinished = false;
    let _lastAnswerEl = null;
    let _lastAnswerCount = 0;
    const _currentRoundSent = new Set();
    let _heartbeatCounter = 0;
    let _knownAnswers = [];
    let _noAnswerCount = 0;
    let _cmdScanCursor = 0; // 指令扫描游标（lastAnswer.textContent 字符坐标）：最后一个已消费【/cmd】的结束位置
    let _sessionEpoch = 0; // 会话代际：仅 会话清空 / initAgent 时自增；作废所有在途扫描
  
    function _pollConfig(seq) {
      // 【改·改动2】签名加令牌参数
      if (!_pollConfigActive || seq !== _pollConfigSeq) return; // 【改·改动2】令牌不匹配即自杀，防双轮询链
      const pollUrl = _agentEndpoint('/agent-config-poll'); // 【改·改动7】
      GM_xmlhttpRequest({
        method: 'GET',
        url: pollUrl,
        timeout: 30000,
        onload(r) {
          if (!_pollConfigActive || seq !== _pollConfigSeq) return; // 【改·改动2】
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
            } catch (e) { }
          }
          _pollConfig(seq); // 【改·改动2】传递令牌
        },
        onerror() {
          if (!_pollConfigActive || seq !== _pollConfigSeq) return; // 【改·改动2】
          setTimeout(() => _pollConfig(seq), 5000); // 【改·改动2】传递令牌（入口处自检，无需预判）
        },
        ontimeout() {
          if (!_pollConfigActive || seq !== _pollConfigSeq) return; // 【改·改动2】
          _pollConfig(seq); // 【改·改动2】
        }
      });
    }
  
    function _syncInitialConfig() {
      return new Promise(resolve => {
        GM_xmlhttpRequest({
          method: 'GET',
          url: _agentEndpoint('/agent-exec'), // 【改·改动7】
          timeout: 3000,
          onload(r) {
            if (r.status === 200) {
              try {
                const data = JSON.parse(r.responseText);
                if (!!data.clipboard_mode !== _clipboardMode) _clipboardMode = !!data.clipboard_mode;
                if (!!data.permission_enabled !== _permissionEnabled) _permissionEnabled = !!data.permission_enabled;
              } catch (e) { }
            }
            resolve();
          },
          ontimeout() { resolve(); },
          onerror() { resolve(); }
        });
      });
    }
  
    /* ================================================================
     * 6.0 剪贴板总闸门（常驻Hook · 唯一可信出口）
     * 原理：无论页面用何种姿势复制（execCommand选区 / writeText / write富格式），
     * 物理上都必须经由这三条API通道之一。在此设卡，一夫当关。
     * - 事件(copy)监听已废弃：探针证实本站有中间层stopPropagation掐断冒泡，
     *   且copy事件内getData()恒为空串，属双重死路，永不复活。
     * - 平时零开销：无消费者时纯直通转发；广播仅在等待收割时发生。
     * ================================================================ */
    let _clipHooksInstalled = false; // 幂等哨兵：防止initAgent反复调用造成包装套娃
    let _clipConsumers = []; // 一次性消费者队列：等待收割的本次请求们
    let _gateEverFired = false; // 【新增·改动9】闸门是否成功广播过：用于拦截超时时判定环境异常
  
    /** 【新增】复制拦截窗口统一读取：0为合法值(只拦同步链、不等待异步回包)，仅未设置/非法值回落默认800 */
    function _getClipInterceptTimeout() {
      const t = parseInt(cfgLoad().copyInterceptTimeout);
      return isNaN(t) ? 800 : Math.max(0, t);
    }
  
    /** 向所有在候消费者广播剪贴板载荷；广播即清场(一次性)，空白负载不过闸 */
    function _broadcastClipboard(text) {
      if (!text || !String(text).trim()) return; // 空白内容：不惊动消费者(空代码块由上方超时回退兜底)
      _gateEverFired = true; // 【新增·改动9】
      // 【改·修复K】FIFO单投递：一份载荷只交付给最早入队的消费者。合法流程等待者恒≤1
      // （修复J后扫描串行，块间提取本就先后执行）；若意外并发（如面板🧪测试撞上扫描），
      // 单投递保证各等待者要么拿到自己的载荷、要么超时回退读元素文本——而非全体收到
      // 同一份张冠李戴的内容。旧版全体广播正是本次指令内容错乱的直接推手
      const consume = _clipConsumers.shift();
      if (consume) {
        try {
          consume(String(text));
        } catch (e) { /* 单点异常不连坐 */ }
      }
    }
  
    /** 安装三口总闸门：startAgent时执行一次。全程使用unsafeWindow确保作用域命中页面真实环境 */
    function _installClipboardHooks() {
      if (_clipHooksInstalled) return; // 幂等闸：包装套娃会导致广播双发
      _clipHooksInstalled = true;
      const W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window; // 页面真实作用域
      /* ── 闸门①：execCommand('copy'|'cut')【一号主闸·探针实证本站主通路】── */
      const pageDoc = W.document;
      const origExec = pageDoc.execCommand.bind(pageDoc); // 预bind保this，防重入丢上下文
      pageDoc.execCommand = function (cmd, ui, value) { // 薄代理：签名透传
        if (cmd === 'copy' || cmd === 'cut') {
          try {
            _broadcastClipboard(String(W.getSelection()));
          } catch (e) { }
        }
        return origExec(cmd, ui, value); // 无条件透传：页面自己的复制功能毫发无损
      };
      /* ── 闸门②③：Async Clipboard API（防御性副闸：站点未来切换姿势时自动接管）── */
      try {
        const clip = W.navigator.clipboard;
        if (clip) {
          const origWriteText = clip.writeText.bind(clip); // writeText纯文本口
          clip.writeText = function (text) {
            _broadcastClipboard(text);
            return origWriteText(text); // 无条件透传真写，绝不阻挠页面自身功能
          };
          if (typeof clip.write === 'function') { // write富格式口
            const origWrite = clip.write.bind(clip);
            clip.write = function (items) {
              try {
                [...items].forEach(item => { // ClipboardItem.types枚举各mime
                  if ([...item.types].includes('text/plain')) {
                    Promise.resolve(item.getType('text/plain')) // getType返回Promise<Blob>
                      .then(blob => blob.text())
                      .then(_broadcastClipboard)
                      .catch(() => { /* 读不出blob则罢，正常路径不受影响 */ });
                  }
                });
              } catch (e) { /* 枚举失败不阻断原调用 */ }
              return origWrite(items);
            };
          }
        }
      } catch (e) {
        log('WARN', `剪贴板副闸安装失败(不影响主闸): ${e.message}`);
      }
      log('INFO', '🛃 剪贴板总闸门已常驻布防 (execCommand/writeText/write)');
    }
  
    function _gateSelfTest() {
      // 【新增·改动9】闸门自检：临时以"只广播不透传"模式替换闸门，触发一次隐藏选区copy，
      // 验证 选区→闸门→广播→消费者 全链路。不调用origExec → 真实剪贴板零污染，结束原样还原。
      try {
        const W = (typeof unsafeWindow !== 'undefined') ? unsafeWindow : window;
        const pageDoc = W.document;
        if (_clipConsumers.length > 0) {
          log('WARN', '🧪 自检中止：当前有拦截请求在等待，避免互相干扰');
          return;
        }
        const gated = pageDoc.execCommand; // 当前已闸门化的execCommand
        let got = null;
        const consume = (t) => { got = t; };
        _clipConsumers.push(consume);
        pageDoc.execCommand = function (cmd, ui, value) { // 自检模式闸门：广播但绝不经手真实剪贴板
          if (cmd === 'copy' || cmd === 'cut') {
            try {
              _broadcastClipboard(String(W.getSelection()));
            } catch (e) { }
          }
          return false;
        };
        const probe = document.createElement('span');
        probe.textContent = '__AGENT_GATE_PROBE__';
        probe.style.cssText = 'position:fixed;left:-9999px;top:-9999px;';
        (document.body || document.documentElement).appendChild(probe);
        const sel = W.getSelection();
        const saved = sel.rangeCount ? sel.getRangeAt(0) : null; // 保存用户原选区
        const range = document.createRange();
        range.selectNodeContents(probe);
        sel.removeAllRanges();
        sel.addRange(range);
        let called = false;
        try {
          called = !!pageDoc.execCommand('copy', false, null);
        } catch (e) { }
        sel.removeAllRanges();
        if (saved) {
          try {
            sel.addRange(saved);
          } catch (e) { }
        } // 还原用户选区
        probe.remove();
        pageDoc.execCommand = gated; // 还原正式闸门
        const idx = _clipConsumers.indexOf(consume);
        if (idx !== -1) _clipConsumers.splice(idx, 1); // 未广播时手动清队，防残留消费者吃掉下一次真实载荷
        if (got && got.includes('__AGENT_GATE_PROBE__')) {
          log('OK', `🧪 剪贴板闸门自检通过：广播→消费者链路正常 (execCommand返回${called})`);
        } else {
          log('WARN', '🧪 剪贴板闸门自检失败：闸门广播未达消费者。复制按钮拦截将依赖回退链路(读元素文本)，功能不受致命影响');
        }
      } catch (e) {
        log('WARN', `🧪 自检异常(不影响正常功能): ${e.message}`);
      }
    }
  
    /* 拦截流程重构：由"装卸补丁的收费站"降级为纯订阅消费者 */
    function _interceptCopy(btn) {
      return new Promise(resolve => {
        let settled = false; // 先达即结算锁：消费者/定时器双入口竞争保护
        const timeout = _getClipInterceptTimeout(); // 统一走公共读取，0语义不再被吞
        const done = (val) => {
          if (settled) return; // 幂等：消费达成or超时只生效其一
          settled = true;
          clearTimeout(timer);
          const idx = _clipConsumers.indexOf(consume); // 无论败退还是收割都要离开队列
          if (idx !== -1) _clipConsumers.splice(idx, 1);
          resolve(val); // val为null表示窗口期颗粒无收
        };
        const consume = (text) => done(text); // 收割回调：闸门广播直达此处
        _clipConsumers.push(consume); // ★先占位后点击：确保click同步链内的广播不脱靶
        const timer = setTimeout(() => {
          done(null);
          // 【新增·改动9】环境警示：闸门自启动以来从未广播过任何内容 → hook可能未命中页面真实环境
          if (!_gateEverFired) {
            log('WARN', '🛃 闸门自启动以来从未广播过复制内容，当前环境可能未命中页面复制通道(常见于脚本管理器沙箱差异)。可通过油猴菜单"🧪 剪贴板闸门自检"验证');
          }
        }, timeout); // 【注·改动9】变量名保持timer，等待窗口值来自_getClipInterceptTimeout()不变
        btn.click(); // 唯一动源：其余交给总闸门
      });
    }
  
    function _findCopyButton(codeEl, btnSel, codeSel, scopeEl) {
      const inner = codeEl.querySelector(btnSel);
      if (inner) return { btn: inner, depth: 0 };
      let cur = codeEl.parentElement;
      let depth = 1;
      while (cur) {
        const cand = cur.querySelector(btnSel);
        if (cand) {
          return (cur.querySelectorAll(codeSel).length === 1) ? { btn: cand, depth } : { blocked: true };
        }
        if (cur === scopeEl) break;
        cur = cur.parentElement;
        depth++;
      }
      return null;
    }
  
    /**
     * 按 rawText 字符偏移物理截断克隆 DOM：删除 offset 之前的所有内容。
     * 坐标基准成立前提：cloneNode(true) 后、清理前，clone.textContent === el.textContent
     * （调用点保证从取快照到截断之间无任何 await）
     */
    function _truncateClone(clone, offset) {
      if (!(offset > 0)) return;
      const walker = document.createTreeWalker(clone, NodeFilter.SHOW_TEXT);
      let acc = 0, node;
      while ((node = walker.nextNode())) {
        const len = node.nodeValue.length;
        if (acc + len > offset) {
          node.nodeValue = node.nodeValue.substring(offset - acc);
          let prev = node.previousSibling;
          while (prev) {
            const p = prev.previousSibling;
            prev.remove();
            prev = p;
          }
          // 沿祖先链向上删除各级祖先的前置兄弟（祖先本身保留——它还装着 offset 之后的内容）
          let anc = node.parentElement;
          while (anc && anc !== clone) {
            let pa = anc.previousSibling;
            while (pa) {
              const p = pa.previousSibling;
              pa.remove();
              pa = p;
            }
            anc = anc.parentElement;
          }
          return;
        }
        acc += len;
      }
      // 防御：offset ≥ 全文长度（正常时序触发不到）——等价于清空
      while (clone.firstChild) clone.firstChild.remove();
    }
  
    async function getCleanText(el, cfg, logBuf, opts) {
      const _log = (lv, msg) => { if (logBuf) logBuf.push([lv, msg]); };
      const clone = el.cloneNode(true);
      const codeBlocks = [];
      // ===== 游标截断：物理删除 offset 之前的前缀（旧指令连同其【cmd】标签、代码块一起消失）=====
      let codeIdxOffset = 0;
      const fromOffset = opts?.fromOffset || 0; // 调用方只传数字，无需再 parseInt 兜底
      if (fromOffset > 0) {
        const lenBefore = clone.textContent.length;
        const liveCodeTotal = cfg.selCodeContentElement ? el.querySelectorAll(cfg.selCodeContentElement).length : 0;
        _truncateClone(clone, fromOffset);
        if (cfg.selCodeContentElement) {
          // 删的是文档序前缀块 → 克隆第 j 块对应 live 第 (j + codeIdxOffset) 块
          // 刀口切在块中间也成立：残块(克隆块0) ↔ live块 codeIdxOffset，占位符整体替换无碍
          codeIdxOffset = liveCodeTotal - clone.querySelectorAll(cfg.selCodeContentElement).length;
        }
        _log('DEBUG', `✂️ 克隆已按游标截断: offset=${fromOffset}，文本 ${lenBefore}→${clone.textContent.length} 字符，跳过旧代码块 ${codeIdxOffset} 个`);
      }
      if (cfg.selCodeContentElement) {
        const liveCodeEls = el.querySelectorAll(cfg.selCodeContentElement);
        const cloneCodeEls = clone.querySelectorAll(cfg.selCodeContentElement);
        const expectCloneCnt = liveCodeEls.length - codeIdxOffset;
        if (liveCodeEls.length === 0) {
          _log('INFO', `📦 代码元素选择器 "${cfg.selCodeContentElement}" 未命中任何元素，跳过代码提取，走DOM兜底`);
        } else if (cloneCodeEls.length !== expectCloneCnt) {
          _log('WARN', `📦 代码元素数量异常: 原文 ${liveCodeEls.length} ≠ 克隆(截断后期望 ${expectCloneCnt}) 实际 ${cloneCodeEls.length}，跳过本轮代码提取，走DOM兜底`);
        } else {
          _log('INFO', `📦 代码元素选择器 "${cfg.selCodeContentElement}" 命中 ${liveCodeEls.length} 个代码块 (游标前跳过 ${codeIdxOffset} 个)`);
          for (let i = 0; i < liveCodeEls.length; i++) {
            if (i < codeIdxOffset) continue; // 游标前旧块：不点按钮、不重复提取
            const liveEl = liveCodeEls[i];
            let codeText = '';
            const tag = `代码块[${i + 1}/${liveCodeEls.length}]`;
            if (cfg.selCodeCopyButton) {
              const found = _findCopyButton(liveEl, cfg.selCodeCopyButton, cfg.selCodeContentElement, el);
              if (found && found.btn) {
                try {
                  const captured = await _interceptCopy(found.btn);
                  if (captured && captured.trim().length > 0) {
                    codeText = captured;
                    _log('OK', `📋 ${tag} 复制按钮拦截成功${found.depth > 0 ? ` (按钮位于代码元素外第${found.depth}层)` : ''} (${captured.length} 字符)`);
                  } else {
                    const reason = captured === null ? `闸门窗口期未收割(${_getClipInterceptTimeout()}ms)` : '闸门广播载荷为空白';
                    _log('WARN', `📋 ${tag} 复制按钮拦截失败: ${reason}，回退到元素文本读取`);
                  }
                } catch (err) {
                  console.warn('[Agent] Copy button intercept failed:', err);
                  _log('WARN', `📋 ${tag} 复制按钮拦截异常: ${err.message}，回退到元素文本读取`);
                }
              } else {
                const reason = (found && found.blocked) ? '候选按钮所在层级含多个代码元素（可能属于相邻代码块），归属校验拦截' : `未找到复制按钮 (选择器: "${cfg.selCodeCopyButton}")`;
                _log('WARN', `📋 ${tag} ${reason}，回退到元素文本读取`);
              }
            } else {
              _log('INFO', `📋 ${tag} 未配置复制按钮，直接读取元素文本`);
            }
            if (!codeText) {
              codeText = liveEl.textContent;
              if (codeText && codeText.trim().length > 0) {
                _log('OK', `📝 ${tag} 元素文本读取成功 (${codeText.length} 字符)`);
              } else if (codeText) {
                _log('WARN', `📝 ${tag} 元素文本读取结果为纯空白 (${codeText.length} 字符)`);
              } else {
                _log('ERR', `📝 ${tag} 元素文本读取结果为空，该代码块内容将丢失（被清理规则移除或混入正文）`);
              }
            }
            const trimStart = parseInt(cfg.codeTrimStart) || 0;
            const trimEnd = parseInt(cfg.codeTrimEnd) || 0;
            if (trimStart > 0 || trimEnd > 0) {
              if (codeText) {
                codeText = codeText.slice(trimStart, codeText.length - trimEnd);
                _log('INFO', `✂️ ${tag} 应用裁剪: 去头${trimStart} 去尾${trimEnd} → 剩余 ${codeText.length} 字符`);
              } else {
                _log('INFO', `✂️ ${tag} 已配置裁剪(头${trimStart}/尾${trimEnd})但提取内容为空，跳过`);
              }
            }
            if (codeText) {
              const cbId = codeBlocks.length; // 占位符ID用实际入队下标，与循环下标解耦（防中间块失败错位）
              codeBlocks.push(codeText);
              const cloneIdx = i - codeIdxOffset;
              const target = cloneCodeEls[cloneIdx].closest('pre') || cloneCodeEls[cloneIdx];
              if (target.parentNode) {
                target.parentNode.replaceChild(document.createTextNode('\u0000CODE' + cbId + '\u0000'), target);
              } else {
                _log('WARN', `⚠️ ${tag} 占位符替换失败: 克隆中目标节点已脱离父节点，该代码块将不会出现在最终文本中`);
              }
            }
          }
        }
      } else {
        _log('INFO', `📦 未配置代码元素选择器，全程DOM兜底提取`);
      }
      const ignoreKeywords = (cfg.cleanIgnoreClassKeywords || 'thinking,reasoning,probe,deepseek-reason')
        .split(',').map(s => s.trim()).filter(s => s);
      if (ignoreKeywords.length > 0) {
        const sel = ignoreKeywords.map(k => `[class*="${CSS.escape(k)}"]`).join(', ');
        try {
          clone.querySelectorAll(sel).forEach(n => n.remove());
        } catch (_) { }
      }
      clone.querySelectorAll('details').forEach(n => n.remove());
      if (cfg.cleanRemoveButtonLike !== false) {
        clone.querySelectorAll('button, [class*="copy"], [class*="operate"], [class*="action"], [class*="toolbar"]').forEach(n => n.remove());
      }
      if (cfg.cleanRemovePre !== false) {
        // 【改·改动17】标记判断移出循环：原对每个pre重复全文includes扫描O(n²)
        // 语义严格等价：有标记全保留 / 无标记全移除
        const hasCodeMarker = clone.textContent.includes('\u3010CodeSTART\u3011');
        if (!hasCodeMarker) {
          clone.querySelectorAll('pre').forEach(n => n.remove());
        }
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
      const rawText = clone.textContent;
      if (codeBlocks.length > 0) {
        const parts = rawText.split(/\u0000CODE(\d+)\u0000/);
        let result = '';
        let assembled = 0;
        for (let i = 0; i < parts.length; i++) {
          if (i % 2 === 0) {
            result += parts[i].split('\n').map(l => l.trim()).filter(l => l.length > 0).join('\n');
          } else {
            result += '\n' + codeBlocks[parseInt(parts[i], 10)] + '\n';
            assembled++;
          }
        }
        if (assembled === codeBlocks.length) {
          _log('OK', `✅ 代码提取完成: ${codeBlocks.length} 个代码块全部组装 (结果 ${result.length} 字符)`);
        } else {
          _log('WARN', `⚠️ 代码提取完成但有丢失: 捕获 ${codeBlocks.length} 个，实际组装 ${assembled} 个`);
        }
        return result;
      }
      _log('INFO', `ℹ️ 纯DOM兜底提取完成 (${rawText.length} 字符)`);
      return rawText;
    }
  
    function _getSendBtnFingerprint() {
      const c = cfgLoad();
      if (c.selSendButtonContainer) {
        const container = document.querySelector(c.selSendButtonContainer);
        if (!container) return 'CONTAINER_MISSING';
        const parts = [];
        for (const child of container.children) {
          const tag = child.tagName;
          const cls = (typeof child.className === 'string' ? child.className : '').split(/\s+/).filter(c => c && !_isPureHashClass(c)).join('.');
          const text = child.textContent.replace(/\s+/g, ' ').trim().slice(0, 40);
          const disabled = child.disabled ? '1' : '0';
          const ariaDisabled = child.getAttribute('aria-disabled') || '';
          const ariaLabel = child.getAttribute('aria-label') || '';
          parts.push(`${tag}[${cls}][${text}][d:${disabled}][ad:${ariaDisabled}][al:${ariaLabel}]`);
        }
        return `C:${parts.join('|')}`;
      }
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
  
    /**
     * 【修复H】发送按钮指纹事件观察器：监听指纹取值基准(容器或按钮)的变化即回调。
     * 观察拓扑三层（解决定向观察器"锚点异父重建"失联盲区——原版锚点被移到不同父节点后，
     * 本体与父观察器双双哑火，_waitForLLMFinish可永久悬挂并锁死_isProcessing）：
     * ①保底层(body级childList+subtree)：锚点无论移除还是异父重建，摘除/插入动作必然在
     *   文档树留下结构mutation，本层永不失联。只挂childList不挂attributes：
     *   属性变化由定向层低延迟捕捉，避免body级全量属性监听把任意属性抖动放大成全树事件。
     * ②定向层(锚点本体+父节点)：属性/文本/子节点变化的主力路径，锚点漂移时整体重建。
     * ③漂移检测：每次事件校验querySelector结果是否仍为定向层锚定节点，漂移即重挂。
     * 闭环：漂移必先经"摘除"→摘除必触发保底层→检测必有机会执行。
     * evaluate闭包每次重新querySelector，重挂后自动跟随新锚点。
     * @returns {Function} 停止观察(回收保底层+当前定向层全部观察器，幂等安全)
     */
    function _observeSendBtnFingerprint(onChange) {
      const c = cfgLoad();
      const targetSel = c.selSendButtonContainer || c.selSendButton;
      let stopped = false;
      const baseStops = []; // 【修复H】保底观察器句柄（生命周期=停止函数）
      let directedStops = []; // 【修复H】定向观察器句柄（锚点漂移时整体重建）
      let watchedEl = null; // 【修复H】定向层当前锚定节点（漂移检测基准）
      function queryAnchor() {
        if (!targetSel) return null;
        try {
          return document.querySelector(targetSel);
        } catch (e) {
          return null;
        }
      }
      function make(node, opts, bucket) {
        const mo = new MutationObserver(() => {
          if (!stopped) handler();
        });
        mo.observe(node, opts);
        bucket.push(() => {
          try {
            mo.disconnect();
          } catch (e) { }
        });
      }
      function attachDirected() {
        directedStops.forEach(s => s()); // 回收旧定向层
        directedStops = [];
        watchedEl = queryAnchor();
        if (!watchedEl) return; // 锚点暂缺：仅保底层值守；重现时结构mutation触发handler→自动重挂
        make(watchedEl, { attributes: true, attributeFilter: ['class', 'style', 'disabled', 'aria-disabled', 'aria-label'], childList: true, characterData: true, subtree: true }, directedStops);
        if (watchedEl.parentElement) make(watchedEl.parentElement, { childList: true }, directedStops); // 兜底本体被整体替换
      }
      function handler() {
        if (stopped) return;
        // 【修复H】漂移检测：每次事件校验锚点归属。检测成本=一次querySelector(微秒级)，
        // 流式期间数十Hz触发，无感。锚点未漂移时直接放行onChange
        if (queryAnchor() !== watchedEl) attachDirected();
        onChange();
      }
      // 装配（function声明提升，make/attachDirected/handler互相引用无TDZ风险）
      make(document.body || document.documentElement, { childList: true, subtree: true }, baseStops); // ①保底层
      attachDirected(); // ②定向层装配
      return () => {
        stopped = true;
        baseStops.forEach(s => s());
        directedStops.forEach(s => s());
      };
    }
  
    /**
     * 【修复C】通用指纹条件等待：满足predicate即resolve，可带超时。事件驱动，零轮询。
     * 返回predicate判定结果(超时为false)。内部统一settle回收观察器和watchdog。
     */
    function _waitFingerprint(predicate, timeoutMs) {
      return new Promise(resolve => {
        let settled = false;
        let stop = null;
        let watchdog = null;
        const settle = (val) => {
          if (settled) return;
          settled = true;
          if (stop) stop();
          if (watchdog) clearTimeout(watchdog);
          resolve(val);
        };
        const evaluate = () => {
          let ok = false;
          try {
            ok = !!predicate(_getSendBtnFingerprint());
          } catch (e) {
            ok = true;
          } // 指纹异常按放行处理
          if (ok) settle(true);
        };
        if (timeoutMs > 0) watchdog = setTimeout(() => settle(false), timeoutMs);
        stop = _observeSendBtnFingerprint(evaluate);
        evaluate(); // 先判一次：条件可能已满足
      });
    }
  
    function _makeDraggable(el, handle) {
      const trigger = handle || el;
      trigger.addEventListener('mousedown', (e) => {
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
      let selectedSendable = new Set(c.sendBtnSendableFingerprints || []);
      let stopWatch = null;
      let stopAppearanceWatch = null; // 【改·改动15】
      const renderBar = (msg) => {
        const mode = c.verifyMode || 'single';
        const canFinish = selectedBusy.size > 0;
        let listHtml = '';
        capturedMap.forEach((snap, fp) => {
          const isBusy = selectedBusy.has(fp);
          const isIdle = selectedIdle.has(fp);
          const isSendable = selectedSendable.has(fp);
          const cls = isBusy ? 'selected-busy' : (isIdle ? 'selected-idle' : (isSendable ? 'selected-sendable' : ''));
          listHtml += `
            <div class="ag-cal-item ${cls}">
              <div class="ag-cal-clone" style="background:${snap.bg};color:${snap.color}">${snap.html}</div>
              <div class="ag-cal-actions">
                <button class="ag-cal-tag ${isBusy ? 'active-busy' : ''}" data-fp="${fp}" data-type="busy">忙碌</button>
                <button class="ag-cal-tag ${isIdle ? 'active-idle' : ''}" data-fp="${fp}" data-type="idle">空闲</button>
                <button class="ag-cal-tag ${isSendable ? 'active-sendable' : ''}" data-fp="${fp}" data-type="sendable">可发送</button>
              </div>
            </div>
          `;
        });
        cards.innerHTML = listHtml || '<div style="color:#52525b;font-size:12px;text-align:center;padding:16px 0">等待按钮状态变化...</div>';
        cards.style.display = 'flex';
        bar.innerHTML = `
          <div style="font-size:13px;color:#d4d4d8;text-align:center">${msg}</div>
          <div style="display:flex;gap:8px;align-items:center">
            <button class="ag-btn ag-btn-g" id="ag-cal-stop">取消</button>
            <button class="ag-btn ag-btn-p" id="ag-cal-finish" style="${canFinish ? '' : 'opacity:0.5;pointer-events:none'}">完成校准</button>
          </div>
        `;
        bar.style.display = 'flex';
        bar.querySelector('#ag-cal-stop').onclick = () => stopCalibration();
        if (canFinish) {
          bar.querySelector('#ag-cal-finish').onclick = () => {
            cfgSaveRuntime({
              sendBtnBusyFingerprints: [...selectedBusy],
              sendBtnIdleFingerprints: [...selectedIdle],
              sendBtnSendableFingerprints: [...selectedSendable]
            });
            log('OK', `校准完成！忙碌: ${selectedBusy.size}个, 空闲: ${selectedIdle.size}个, 可发送: ${selectedSendable.size}个`);
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
              selectedSendable.delete(fp);
            } else if (type === 'idle') {
              if (selectedIdle.has(fp)) selectedIdle.delete(fp);
              else selectedIdle.add(fp);
              selectedBusy.delete(fp);
              selectedSendable.delete(fp);
            } else if (type === 'sendable') {
              if (selectedSendable.has(fp)) selectedSendable.delete(fp);
              else selectedSendable.add(fp);
              selectedBusy.delete(fp);
              selectedIdle.delete(fp);
            }
            renderBar(msg);
          };
        });
      };
      const stopCalibration = () => {
        if (stopWatch) {
          stopWatch();
          stopWatch = null;
        } // 【改·改动15】观察器停止
        if (stopAppearanceWatch) {
          stopAppearanceWatch();
          stopAppearanceWatch = null;
        } // 【改·改动15】
        bar.remove();
        cards.remove();
        _isCalibrating = false;
        showPanel();
      };
      renderBar('👇 请在下方正常聊天，脚本会自动捕获按钮的不同状态。<br><b style="color:#f472b6">【忙碌】=停止生成 | 【空闲】=AI说完 | 【可发送】=可以发送消息</b>');
      // 【改·改动15】300ms采样轮询 → 事件驱动：不再漏采短于300ms的瞬态(典型：一闪而过的忙碌态，恰是校准刚需)
      const captureCurrent = () => { // 【改·改动15】原interval回调体平移
        const fp = _getSendBtnFingerprint();
        if (!fp) return;
        if (fp === 'ELEMENT_MISSING' || fp === 'CONTAINER_MISSING') {
          if (!capturedMap.has(fp)) {
            capturedMap.set(fp, {
              html: `<span style="color:#ef4444;font-size:12px">⚠ 元素不存在 (${fp === 'CONTAINER_MISSING' ? '容器' : '按钮'})</span>`,
              bg: '#1a1a1a',
              color: '#ef4444'
            });
            log('INFO', `捕获状态: ${fp} (#${capturedMap.size})`);
            renderBar('👇 继续操作，或标记已捕获的状态后点击完成。<br><b style="color:#f472b6">【忙碌】=停止生成 | 【空闲】=AI说完 | 【可发送】=可以发送消息</b>');
          }
          return;
        }
        if (!capturedMap.has(fp)) {
          const targetSel = c.selSendButtonContainer || c.selSendButton;
          const el = document.querySelector(targetSel);
          if (!el) return;
          const cs = getComputedStyle(el);
          // 【改·改动15】预览取材改为消毒克隆：innerHTML直塞卡片会让内联事件属性(onerror等)插入瞬间复活。
          // 克隆后摘除script/style/link与全部on*属性(残余面仅剩srcdoc类exotic，预览存活期极短，接受)
          const safeClone = el.cloneNode(true);
          safeClone.querySelectorAll('script, style, link').forEach(n => n.remove());
          safeClone.querySelectorAll('*').forEach(n => {
            [...n.attributes].forEach(a => {
              if (/^on/i.test(a.name)) n.removeAttribute(a.name);
            });
          });
          const holder = document.createElement('div');
          holder.appendChild(safeClone);
          capturedMap.set(fp, { html: holder.innerHTML, bg: cs.backgroundColor, color: cs.color });
          log('INFO', `捕获新状态指纹 (#${capturedMap.size})`);
          renderBar('👇 继续操作，或标记已捕获的状态后点击完成。<br><b style="color:#f472b6">【忙碌】=停止生成 | 【空闲】=AI说完 | 【可发送】=可以发送消息</b>');
        }
      };
      stopWatch = _observeSendBtnFingerprint(captureCurrent); // 【改·改动15】依赖改动14的观察器
      // 【新增·改动15】锚点未就绪兜底：观察器无锚点时不工作，挂body监听等目标出现后重挂(覆盖原轮询的迟到场景)
      if (!document.querySelector(c.selSendButtonContainer || c.selSendButton)) {
        stopAppearanceWatch = _waitSelector(c.selSendButtonContainer || c.selSendButton, () => {
          if (stopWatch) stopWatch();
          stopWatch = _observeSendBtnFingerprint(captureCurrent);
          captureCurrent();
        });
      }
      captureCurrent(); // 【新增·改动15】观察器只报变化，首态需主动采样
    }
  
    async function _waitForLLMFinish() {
      // 【改·改动14】200ms轮询 → 事件驱动观察器。覆盖改动4位置1：0值延时语义已吸收(0=立即放行，仅NaN回落500)
      const c = cfgLoad();
      const busyList = c.sendBtnBusyFingerprints || [];
      if (busyList.length === 0) {
        log('WARN', '⚠️ 未校准忙碌态，直接放行(建议校准)');
        return;
      }
      log('INFO', '👀 监听发送按钮状态...(事件驱动)');
      // 阶段一：等待脱离忙碌态（元素缺失/无指纹均视为已脱离，判定与原版一致）
      await _waitFingerprint(fp => fp === null || fp === 'ELEMENT_MISSING' || fp === 'CONTAINER_MISSING' || !busyList.includes(fp));
      log('INFO', '🟢 脱离忙碌态');
      if (c.verifyMode === 'double') {
        const idleList = c.sendBtnIdleFingerprints || [];
        const sendableList = c.sendBtnSendableFingerprints || [];
        const validList = [...new Set([...idleList, ...sendableList])];
        if (validList.length === 0) {
          log('WARN', '⚠️ 双验证模式但未设置空闲/可发送态，退化为单验证');
        } else {
          log('INFO', '👀 双验证：等待进入空闲或可发送态...');
          await _waitFingerprint(fp => !!fp && validList.includes(fp));
          log('INFO', '🟢 进入空闲或可发送态');
        }
      }
      const _wd = parseInt(c.waitDelayAfterDone);
      const delay = isNaN(_wd) ? 500 : Math.max(0, _wd);
      log('INFO', `⏳ 等待延时 ${delay}ms...`);
      await new Promise(r => setTimeout(r, delay));
    }
  
    async function _waitForSendable() {
      // 【修复C】使用带超时的_waitFingerprint，移除Promise.race泄漏观察器
      const c = cfgLoad();
      const sendableList = c.sendBtnSendableFingerprints || [];
      if (sendableList.length === 0) return;
      log('INFO', '👀 等待可发送状态...(事件驱动)');
      const WATCHDOG = 30000; // 看门狗：安全阀非业务延时
      const hit = await _waitFingerprint(fp => !!fp && sendableList.includes(fp), WATCHDOG);
      if (hit) log('INFO', '🟢 检测到可发送状态');
      else log('WARN', `⚠️ 等待可发送状态超时(${WATCHDOG}ms)，强制继续`);
    }
  
    const _TICK_ROUNDS_STORE = 'pokeragent_tick_rounds';
  
    function _fireMemoryTick(answerCount) {
      const roundKey = location.href + '#' + answerCount;
      const keys = GM_getValue(_TICK_ROUNDS_STORE, []);
      if (keys.includes(roundKey)) return;
      keys.push(roundKey);
      if (keys.length > 64) keys.splice(0, keys.length - 64);
      GM_setValue(_TICK_ROUNDS_STORE, keys);
      GM_xmlhttpRequest({
        method: 'GET',
        url: _agentEndpoint('/agent-memory-tick'), // 【改·改动7】
        timeout: 3000,
        onload() { },
        onerror() { log('WARN', '记忆衰减信号发送失败（本回合温度未衰减）'); },
        ontimeout() { }
      });
    }
  
    function _pruneTickRounds() {
      const keys = GM_getValue(_TICK_ROUNDS_STORE, []);
      const prefix = location.href + '#';
      const kept = keys.filter(k => !k.startsWith(prefix));
      if (kept.length !== keys.length) GM_setValue(_TICK_ROUNDS_STORE, kept);
    }
  
    async function _injectMemoryIfNeeded() {
      const c = cfgLoad();
      const freq = parseInt(c.memoryInjectFrequency) || 0;
      if (freq <= 0) return;
      if (_roundCount - _lastMemoryInjectRound < freq) return;
      _lastMemoryInjectRound = _roundCount;
      const injectUrl = _agentEndpoint('/agent-memory-inject'); // 【改·改动7】（c 保留，后面用 selInputBox）
      return new Promise(resolve => {
        GM_xmlhttpRequest({
          method: 'GET',
          url: injectUrl,
          timeout: 3000,
          onload(r) {
            if (r.status === 200) {
              try {
                const data = JSON.parse(r.responseText);
                if (data.memory && data.memory.trim()) {
                  const input = document.querySelector(c.selInputBox);
                  if (input) {
                    const memoryBlock = `<memory>\n${data.memory}\n</memory>\n`;
                    let currentText = '';
                    if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
                      currentText = input.value;
                    } else {
                      currentText = input.textContent || '';
                    }
                    const memStart = currentText.indexOf('<memory>');
                    const memEnd = currentText.indexOf('</memory>');
                    if (memStart !== -1 && memEnd !== -1) {
                      currentText = currentText.substring(0, memStart) + memoryBlock + currentText.substring(memEnd + '</memory>'.length);
                    } else {
                      currentText = currentText + memoryBlock;
                    }
                    _directInput(input, currentText, false);
                    log('INFO', `🧠 记忆已注入 (${data.memory.length} 字符)`);
                  }
                }
              } catch (e) {
                log('WARN', `记忆注入解析失败: ${e.message}`);
              }
            }
            resolve();
          },
          onerror() { resolve(); },
          ontimeout() { resolve(); }
        });
      });
    }
  
    async function _checkAndDispatch() {
      if (_isProcessing || _cmdQueue.length === 0) return;
      _isProcessing = true;
      if (_cmdQueue.length === 0) {
        _isProcessing = false;
        return;
      }
      const _seen = new Set();
      const batch = _cmdQueue.filter(cmd => {
        const k = cmd.replace(/\s+/g, '');
        if (_seen.has(k)) return false;
        _seen.add(k);
        return true;
      }).join('\n');
      _cmdQueue = [];
      await _injectMemoryIfNeeded();
      _dispatch(batch);
    }
  
    function _dispatch(cmdBatch) {
      const c = cfgLoad();
      if (c.textCleanRules && Array.isArray(c.textCleanRules) && c.textCleanRules.length > 0) {
        let cleanCount = 0;
        const parseUnicode = (str) => {
          if (!str) return str;
          return str.replace(/\\u([0-9a-fA-F]{4})|\\u\{([0-9a-fA-F]{1,6})\}|U\+([0-9a-fA-F]{4,6})/g, (match, p1, p2, p3) => {
            const hex = p1 || p2 || p3;
            try {
              return String.fromCodePoint(parseInt(hex, 16));
            } catch (e) {
              return match;
            }
          });
        };
        c.textCleanRules.forEach(rule => {
          if (!rule.enabled || !rule.find) return;
          try {
            let findStr = rule.find;
            let replaceStr = rule.replace || '';
            if (rule.isUnicode) {
              findStr = parseUnicode(findStr);
              replaceStr = parseUnicode(replaceStr);
            }
            if (rule.isRegex) {
              const regex = new RegExp(findStr, 'g');
              const newCmd = cmdBatch.replace(regex, replaceStr);
              if (newCmd !== cmdBatch) {
                cmdBatch = newCmd;
                cleanCount++;
              }
            } else {
              if (cmdBatch.includes(findStr)) {
                cmdBatch = cmdBatch.split(findStr).join(replaceStr);
                cleanCount++;
              }
            }
          } catch (e) {
            log('WARN', `清洗规则错误: ${rule.find} - ${e.message}`);
          }
        });
        if (cleanCount > 0) log('INFO', `✨ 已应用 ${cleanCount} 条自定义清洗规则`);
      }
      log('INFO', `🚀 捕获完整指令，立即发送至本地服务...`);
      GM_xmlhttpRequest({
        method: 'POST',
        url: _agentEndpoint('/agent-exec'), // 【改·改动7】
        headers: { 'Content-Type': 'application/json' },
        data: JSON.stringify({ command: cmdBatch }),
        onload: (r) => {
          if (r.status === 200) {
            _dispatchRetryCount = 0;
            try {
              const data = JSON.parse(r.responseText);
              if (data.type === 'task_batch' && data.task_ids) {
                log('OK', `📥 后端已接收，分配 ${data.task_ids.length} 个任务ID，建立SSE监听...`);
                _taskList = [..._taskList, ...data.task_ids.map(id => ({ id, status: 'waiting', logs: [], result: '' }))]; // 【改·修复M】跨批累积：一个回答的指令可分多个传输批次派发（流式执行特性），回执聚合到同一任务表，发送完成后才整体清空
                _initSSE();
                _renderTaskBlock();
                return;
              }
            } catch (e) {
              log('ERR', '解析task_batch失败: ' + e);
            }
          }
          log('ERR', `HTTP ${r.status} 或响应格式错误`);
          _dispatchRetryCount++;
          if (_dispatchRetryCount >= MAX_DISPATCH_RETRIES) {
            log('ERR', `连续失败${MAX_DISPATCH_RETRIES}次，放弃发送`);
            _dispatchRetryCount = 0;
            _isProcessing = false;
            _checkAndDispatch();
            if (_tasksFinished && _taskList.length > 0 && _taskList.every(t => t.status === 'done')) _tryFinalSend(); // 【新增·修复M·自决】派发放弃后兜底收口：M-3后all-done队列非空时只派发不收口，若本批派发永久失败，存量done回执将无人收口（v48的all-done必达_tryFinalSend无此问题；不需要可删除此行）
          } else {
            const delay = 3000 * Math.pow(2, _dispatchRetryCount - 1);
            log('WARN', `${delay / 1000}秒后第${_dispatchRetryCount + 1}次重试...`);
            _cmdQueue.unshift(cmdBatch);
            _isProcessing = false;
            setTimeout(() => _checkAndDispatch(), delay);
          }
        },
        onerror() {
          _dispatchRetryCount++;
          if (_dispatchRetryCount >= MAX_DISPATCH_RETRIES) {
            log('ERR', `无法连接本地服务，已重试${MAX_DISPATCH_RETRIES}次，放弃发送`);
            _dispatchRetryCount = 0;
            _isProcessing = false;
            _checkAndDispatch();
            if (_tasksFinished && _taskList.length > 0 && _taskList.every(t => t.status === 'done')) _tryFinalSend(); // 【新增·修复M·自决】同上
          } else {
            const delay = 3000 * Math.pow(2, _dispatchRetryCount - 1);
            log('WARN', `无法连接本地服务，${delay / 1000}秒后第${_dispatchRetryCount + 1}次重试...`);
            _cmdQueue.unshift(cmdBatch);
            _isProcessing = false;
            setTimeout(() => _checkAndDispatch(), delay);
          }
        }
      });
    }
  
    async function _decodeClipboardFile(resultText) {
      // 【改·改动8】解析逻辑原样保留，仅返回结构改字段：{ filename, size, bytes, beforeMarker }
      // 删除 text/base64 字段——全项目无任何消费点(已核实)；旧格式 text 仅用于日志字数统计
      const marker = '__CLIPBOARD_FILE__';
      const markerIdx = resultText.indexOf(marker);
      if (markerIdx === -1) return null;
      const beforeMarker = resultText.substring(0, markerIdx);
      let afterMarker = resultText.substring(markerIdx + marker.length);
      let cleanStr = afterMarker.replace(/\x00/g, '').replace(/\s/g, '');
      const parts = cleanStr.split('|||');
      const isNewFormat = (parts[0] === 'ID');
      if (isNewFormat) {
        const fileId = parts[1];
        const filename = parts[2] || 'unknown';
        const sizeStr = parts[3];
        if (!fileId || !sizeStr) {
          log('WARN', `新格式标记解析失败: ${cleanStr.substring(0, 80)}`);
          return null;
        }
        log('INFO', `📡 检测到大文件任务，开始下载: ${filename} (${sizeStr} bytes)`);
        const bytes = await _downloadFileFromAgent(fileId); // 【改·改动8】直取字节
        if (!bytes) return null;
        log('OK', `📄 文件下载完成: ${filename} (${sizeStr} bytes)`);
        return { filename, size: parseInt(sizeStr), bytes, beforeMarker };
      } else {
        let filename = '';
        let sizeStr = '';
        let base64Raw = '';
        if (parts.length >= 3) {
          filename = parts[0];
          sizeStr = parts[1];
          base64Raw = parts.slice(2).join('|||');
        } else {
          const dotIdx = cleanStr.lastIndexOf('.');
          if (dotIdx !== -1) {
            let extEnd = dotIdx + 1;
            while (extEnd < cleanStr.length && /[a-zA-Z]/.test(cleanStr[extEnd])) extEnd++;
            if (extEnd < cleanStr.length && /[0-9]/.test(cleanStr[extEnd])) {
              let numEnd = extEnd;
              while (numEnd < cleanStr.length && /[0-9]/.test(cleanStr[numEnd])) numEnd++;
              filename = cleanStr.substring(0, extEnd);
              sizeStr = cleanStr.substring(extEnd, numEnd);
              base64Raw = cleanStr.substring(numEnd);
            }
          }
        }
        if (!sizeStr || !base64Raw) {
          log('WARN', `旧格式标记解析失败: ${cleanStr.substring(0, 80)}`);
          return null;
        }
        try {
          let base64Clean = base64Raw.replace(/-/g, '+').replace(/_/g, '/');
          base64Clean = base64Clean.replace(/[^A-Za-z0-9+/=]/g, '');
          while (base64Clean.length % 4) base64Clean += '=';
          const binaryStr = atob(base64Clean);
          const bytes = new Uint8Array(binaryStr.length);
          for (let i = 0; i < binaryStr.length; i++) bytes[i] = binaryStr.charCodeAt(i);
          const text = new TextDecoder('utf-8').decode(bytes); // 【注·改动8】仅用于日志字数统计
          log('OK', `📄 文件解码成功 (旧格式): ${filename} (${sizeStr} bytes → ${text.length} 字符)`);
          return { filename, size: parseInt(sizeStr), bytes, beforeMarker }; // 【改·改动8】base64→bytes
        } catch (e) {
          log('ERR', `旧格式Base64解码失败: ${e.message}`);
          return null;
        }
      }
    }
  
    function _downloadFileFromAgent(fileId) {
      // 【改·改动8】XHR→GM_xmlhttpRequest：①绕开页面CORS(全脚本唯一走页面XHR的例外就此消灭)
      // ②规避Chrome PNA对 https页面→localhost 的预检限制 ③arraybuffer直取字节，
      // 免除 Array.from+fromCharCode逐字节+btoa 的大文件慢路径
      const apiUrl = _agentEndpoint('/agent-file-download'); // 【改·改动8】依赖改动7
      return new Promise((resolve) => {
        GM_xmlhttpRequest({
          method: 'GET',
          url: apiUrl + '?id=' + encodeURIComponent(fileId), // 【改·改动8】顺手补URL编码(原裸拼)
          responseType: 'arraybuffer',
          timeout: 0, // 大文件不限时，与SSE长连接同款约定
          onload(r) {
            if (r.status === 200 && r.response) { // 【修复G】去掉byteLength>0：0字节文件合法
              resolve(new Uint8Array(r.response)); // 【改·改动8】直返字节，不再base64往返
            } else {
              log('ERR', `文件下载失败 HTTP ${r.status}`);
              resolve(null);
            }
          },
          onerror() {
            log('ERR', '文件下载网络错误');
            resolve(null);
          },
          ontimeout() {
            log('ERR', '文件下载超时');
            resolve(null);
          }
        });
      });
    }
  
    async function _doPasteFile(input, filename, fileSize, bytes) {
      // 【改·改动8】入参b64Data→bytes(Uint8Array)
      try {
        // 【删·改动8】atob+逐字节循环：上游已直供字节
        const ext = filename.split('.').pop().toLowerCase();
        const mimeMap = {
          'js': 'text/javascript', 'ts': 'text/typescript', 'html': 'text/html', 'css': 'text/css',
          'json': 'application/json', 'md': 'text/markdown', 'py': 'text/x-python', 'txt': 'text/plain',
          'xml': 'text/xml', 'csv': 'text/csv', 'java': 'text/x-java-source',
          'gradle': 'text/plain', 'properties': 'text/plain', 'toml': 'text/plain', 'yml': 'text/yaml', 'yaml': 'text/yaml'
        };
        const file = new File([bytes], filename, { type: mimeMap[ext] || 'text/plain' });
        input.focus();
        const dt = new DataTransfer();
        dt.items.add(file);
        let pasteEvt;
        // 【改·改动8】ClipboardEvent构造兼容：旧版Firefox(<118)直接抛错，降级普通Event
        try {
          pasteEvt = new ClipboardEvent('paste', { bubbles: true, cancelable: true });
        } catch (e) {
          pasteEvt = new Event('paste', { bubbles: true, cancelable: true });
        }
        Object.defineProperty(pasteEvt, 'clipboardData', { get() { return dt; } });
        input.dispatchEvent(pasteEvt);
        log('OK', `📎 已粘贴文件: ${filename}（${fileSize} 字节）`);
        await _smartWait(input, { checkDOM: true, maxWait: 3000 });
      } catch (err) {
        log('ERR', `文件粘贴失败: ${err.message}`);
      }
    }
  
    function _renderTaskBlock() {
      const c = cfgLoad();
      const input = document.querySelector(c.selInputBox);
      if (!input) return;
      let block = TASK_START;
      _taskList.forEach((task, idx) => {
        if (task.status === 'done') {
          let resultText = task.result || '';
          if (_clipboardMode && resultText.includes('__CLIPBOARD_FILE__')) {
            resultText = '[Poker Agent] [文件准备中...等待下载粘贴]';
          }
          block += `[Poker Agent] [done]\n${resultText}\n`;
        } else if (task.status === 'running') {
          block += `[Poker Agent] [running]\n${task.logs.join('\n')}\n`;
        } else {
          block += `[Poker Agent] [waiting]\n\n`;
        }
        if (idx < _taskList.length - 1) block += '\n';
      });
      const allDone = _taskList.length > 0 && _taskList.every(t => t.status === 'done');
      if (allDone) block += `\n[Poker Agent]\nAll tasks done!`;
      block += TASK_END;
      let currentText = '';
      if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') {
        currentText = input.value;
      } else {
        currentText = input.textContent || '';
      }
      let prefix = '';
      let suffix = '';
      const startIdx = currentText.indexOf(TASK_START);
      if (startIdx !== -1) {
        prefix = currentText.substring(0, startIdx);
        const endIdx = currentText.indexOf(TASK_END, startIdx + TASK_START.length);
        if (endIdx !== -1) suffix = currentText.substring(endIdx + TASK_END.length);
      } else {
        prefix = currentText.trim();
        if (prefix) prefix += '\n';
      }
      const finalText = prefix + block + suffix;
      _directInput(input, finalText, false);
    }
  
    function _buildExpectedInputFromTaskList() {
      let block = TASK_START;
      _taskList.forEach((task, idx) => {
        if (task.status === 'done') {
          let resultText = task.result || '';
          if (_clipboardMode && resultText.includes('__CLIPBOARD_FILE__')) {
            resultText = '[Poker Agent] [文件准备中...等待下载粘贴]';
          }
          block += `[Poker Agent] [done]\n${resultText}\n`;
        } else if (task.status === 'running') {
          block += `[Poker Agent] [running]\n${task.logs.join('\n')}\n`;
        } else {
          block += `[Poker Agent] [waiting]\n\n`;
        }
        if (idx < _taskList.length - 1) block += '\n';
      });
      const allDone = _taskList.length > 0 && _taskList.every(t => t.status === 'done');
      if (allDone) block += `\n[Poker Agent]\nAll tasks done!`;
      block += TASK_END;
      return block;
    }
  
    function _initSSE() {
      if (_sseEventSource) {
        try {
          _sseEventSource.abort();
        } catch (e) { }
        _sseEventSource = null;
      }
      const streamUrl = _agentEndpoint('/agent-stream'); // 【改·改动7】
      let _seenLen = 0;
      let _pending = '';
      const _flushComplete = (buffer) => {
        const events = buffer.split('\n\n');
        _pending = events.pop() || '';
        for (const evt of events) {
          if (!evt.trim() || evt.startsWith(':')) continue;
          const dataLine = evt.split('\n').find(l => l.startsWith('data:'));
          if (!dataLine) continue;
          const jsonStr = dataLine.slice(5).trim();
          if (!jsonStr) continue;
          try {
            const data = JSON.parse(jsonStr);
            _handleSSEData(data);
          } catch (err) {
            log('ERR', `SSE 解析失败: ${err}, 原始: ${jsonStr.substring(0, 100)}`);
          }
        }
      };
      _sseEventSource = GM_xmlhttpRequest({
        method: 'GET',
        url: streamUrl,
        headers: { 'Accept': 'text/event-stream' },
        timeout: 0,
        onprogress: (resp) => {
          const newData = _pending + resp.responseText.slice(_seenLen);
          _seenLen = resp.responseText.length;
          _flushComplete(newData);
        },
        onload: () => {
          if (_pending.trim()) {
            _flushComplete(_pending + '\n\n');
          }
          _pending = '';
          log('INFO', 'SSE 连接正常关闭');
          _sseEventSource = null;
        },
        onerror: (err) => {
          _pending = '';
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
          try {
            _sseEventSource.abort();
          } catch (e) { }
          _sseEventSource = null;
        }
        _isProcessing = false; // 【新增·修复M】本传输批次飞行结束即放行：后续批次立即串行接力，流式执行不停摆
        _tasksFinished = true;
        if (_cmdQueue.length > 0) {
          _checkAndDispatch(); // 【改·修复M】队列有存货：立即派发下一批，收口押后
        } else {
          _tryFinalSend(); // 队列空：尝试收口（内部等LLM说完+复核，可能让位）
        }
      }
    }
  
    async function _tryFinalSend() {
      // 【改·修复M】收口语义=回答边界。任务表跨批累积；本函数在"全done+队列空"时尝试收口，
      // 等待LLM期间出现新指令/新批次则让位（不清任务表），由其完成后的all-done再次触发，稳定后一次发送
      if (!_tasksFinished || _finalSendInProgress) return;
      _finalSendInProgress = true;
      try {
        const epoch = _sessionEpoch;
        // 等待LLM说完：期间派发通道畅通（_isProcessing已在all-done处放行），流式执行不停摆
        await _waitForLLMFinish();
        if (epoch !== _sessionEpoch) { // 【原改动11守卫①】等待LLM期间发生停止/重初始化
          log('WARN', '🪦 会话已重置，放弃本次回执校验流程');
          _isProcessing = false;
          _taskList = [];
          _tasksFinished = false;
          return;
        }
        // 复核①：等待期间有新指令捕获 → 让位。派发由扫描/接力负责，其完成后的all-done重新收口
        if (_cmdQueue.length > 0) return;
        // 复核②：等待期间派发了新批次且未完成 → 让位，同上
        if (_taskList.length === 0 || !_taskList.every(t => t.status === 'done')) return;
        log('INFO', '✅ 所有任务完成且 LLM 已输出完毕，开始回执校验...');
        const c = cfgLoad();
        const input = document.querySelector(c.selInputBox);
        if (!input) {
          log('ERR', '找不到输入框，跳过发送');
          _isProcessing = false;
          _taskList = [];
          _tasksFinished = false;
          _checkAndDispatch();
          return;
        }
        const hasFileTask = _clipboardMode && _taskList.some(t => t.status === 'done' && t.result && t.result.includes('__CLIPBOARD_FILE__'));
        if (hasFileTask) {
          log('INFO', '📋 检测到文件任务，准备文件粘贴...');
          for (const task of _taskList) {
            if (task.status !== 'done') continue;
            const resultText = task.result || '';
            if (resultText.includes('__CLIPBOARD_FILE__')) {
              const decoded = await _decodeClipboardFile(resultText);
              if (decoded) {
                task.result = (decoded.beforeMarker || '') + `[Poker Agent] 已粘贴文件：${decoded.filename}（${decoded.size} 字节）`;
                task._fileData = decoded;
              } else {
                log('ERR', `文件任务解析或下载失败: ${task.id}`);
                task.result = `[Poker Agent] 文件标记损坏或下载失败`;
                task._fileData = null;
              }
            }
          }
        }
        // 【原改动1】重试参数解析：NaN回落默认，0为合法值（至少1次尝试、0间隔）
        const _vt = parseInt(c.verifyRetryTimes);
        const retryTimes = isNaN(_vt) ? 30 : Math.max(1, _vt); // 0次会连验证都不做，钳到1
        const _vi = parseInt(c.verifyRetryInterval);
        const retryInterval = isNaN(_vi) ? 1000 : Math.max(0, _vi);
        let verified = false; // 【原改动1】验证结果闸门：耗尽重试仍失败必须中止发送
        for (let i = 1; i <= retryTimes; i++) {
          log('INFO', `🛡️ 回执验证 #${i}/${retryTimes}`);
          _renderTaskBlock();
          input.dispatchEvent(new Event('input', { bubbles: true }));
          let currentInput = '';
          if (input.tagName === 'TEXTAREA' || input.tagName === 'INPUT') currentInput = input.value;
          else currentInput = input.textContent || '';
          const expectedInput = _buildExpectedInputFromTaskList();
          if (currentInput.includes(TASK_START) || currentInput === expectedInput) {
            verified = true; // 【原改动1】
            log('OK', '✅ 回执验证通过，准备发送');
            break;
          }
          log('WARN', '❌ 回执验证失败，内容不一致或无区块标记。准备强制覆盖。');
          if (i < retryTimes) await new Promise(r => setTimeout(r, retryInterval));
        }
        if (!verified) { // 【原改动1】失败出口：原先带着被站点框架回滚的脏输入框继续发送
          log('ERR', `🛡️ 回执验证 ${retryTimes} 次后仍未通过，放弃发送（输入框保持现状供人工检查）`);
          _isProcessing = false;
          _taskList = [];
          _tasksFinished = false;
          _checkAndDispatch();
          return;
        }
        if (hasFileTask) {
          input.focus();
          for (const task of _taskList) {
            if (task._fileData) {
              await _doPasteFile(input, task._fileData.filename, task._fileData.size, task._fileData.bytes); // 【原改动8】base64→bytes
            }
          }
        }
        await _waitForSendable();
        const debounceDelay = Math.max(0, parseInt(c.sendDebounceDelay) || 0); // 【原改动4】钳制负值
        if (debounceDelay > 0) {
          log('INFO', `⏳ 发送防抖延时 ${debounceDelay}ms...`);
          await new Promise(r => setTimeout(r, debounceDelay));
        }
        // 复核③：发射前终检（原epoch守卫②扩展）
        if (epoch !== _sessionEpoch) {
          log('WARN', '🪦 会话已重置，放弃最终发送');
          _isProcessing = false;
          _taskList = [];
          _tasksFinished = false;
          return;
        }
        if (_cmdQueue.length > 0 || !_taskList.every(t => t.status === 'done')) {
          log('INFO', '↩️ 发射前发现新指令/未完成任务，让位，稍后统一发送');
          return; // 不清任务表：新批次完成后all-done再次收口
        }
        log('INFO', '🚀 触发最终发送');
        _executeSend(input);
        _taskList = [];
        _tasksFinished = false;
        _checkAndDispatch(); // 兜底接力（正常路径队列空，空转无害）
      } finally {
        _finalSendInProgress = false;
      }
    }
  
    function _trySendByClick() {
      const c = cfgLoad();
      if (c.selSendButtonContainer) {
        const container = document.querySelector(c.selSendButtonContainer);
        if (!container) {
          log('ERR', '找不到发送按钮容器');
          return false;
        }
        if (c.selSendButton) {
          const btn = container.querySelector(c.selSendButton);
          if (btn) {
            btn.click();
            log('INFO', '👆 点击发送按钮(容器内精确定位)');
            return true;
          }
        }
        const clickable = container.querySelector('button, [role="button"], a[href], input[type="submit"]');
        if (clickable) {
          clickable.click();
          log('INFO', '👆 点击发送按钮(容器内自动查找)');
          return true;
        }
        log('ERR', '容器内未找到可点击元素');
        return false;
      }
      if (!c.selSendButton) {
        log('WARN', '未配置发送按钮选择器，无法点击发送');
        return false;
      }
      const btn = document.querySelector(c.selSendButton);
      if (!btn) {
        log('ERR', '找不到发送按钮，无法点击发送');
        return false;
      }
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
            try {
              _trySendByEnter(input);
            } catch (err) {
              log('ERR', `回车发送也失败: ${err.message}`);
            }
          }
          break;
      }
    }
  
    function _directInput(input, text) {
      // 【改·改动17】移除append参数：全部调用传false，分支为死代码（多余实参JS静默忽略，调用点无需改动）
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
        input.dispatchEvent(new KeyboardEvent(evtType, { key: 'Enter', code: 'Enter', keyCode: 13, which: 13, charCode: evtType === 'keypress' ? 13 : 0, bubbles: true, cancelable: true, composed: true }));
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
          if (valOk && domOk) stable++;
          else {
            stable = 0;
            domSnap = checkDOM ? input.parentElement?.children.length ?? -1 : -1;
          }
          if (stable >= stableNeed) {
            clearInterval(t);
            clearTimeout(safety);
            resolve();
          }
        }, interval);
        const safety = setTimeout(() => {
          clearInterval(t);
          resolve();
        }, maxWait);
      });
    }
  
    /* ================================================================
     * 6.5 发送模式选择器
     * ================================================================ */
    let _toggleEl = null;
    let _togglePosRaf = 0; // 【新增·修复D】rAF合并句柄
  
    function _scheduleToggleUpdate() {
      // 【新增·修复D】布局读合并：全局观察器每mutation批次+scroll capture逐帧触发定位，
      // 每次两连发getBoundingClientRect强制布局。rAF收敛到每帧最多一次，事件驱动性质不变。
      if (_togglePosRaf) return;
      _togglePosRaf = requestAnimationFrame(() => {
        _togglePosRaf = 0;
        _updateTogglePosition();
      });
    }
  
    function _initAutoSendToggle() {
      _destroyAutoSendToggle();
      let c;
      try {
        c = cfgLoad();
      } catch (e) {
        console.error('[Agent] cfgLoad异常:', e);
        return;
      }
      if (!c.showAutoSendToggle || (!c.selSendButton && !c.selSendButtonContainer)) return;
      const mode = c.autoSendMode || 'click';
      const freq = parseInt(c.memoryInjectFrequency);
      _toggleEl = document.createElement('div');
      _toggleEl.id = 'agent-auto-send-toggle';
      _toggleEl.innerHTML = `
        <div class="ag-as-mem" id="ag-as-mem-head" title="记忆注入频率：每N轮对话注入一次记忆上下文">
          <span class="ag-as-mem-label">🧠 记忆注入</span>
          <span class="ag-as-mem-val" id="ag-as-mem-val">${_memFreqLabel(freq)}</span>
        </div>
        <div class="ag-as-mem-body" id="ag-as-mem-body">
          <div class="ag-as-mem-opts" id="ag-as-mem-opts">
            <span class="ag-as-mem-opt" data-freq="0">关闭</span>
            <span class="ag-as-mem-opt" data-freq="1">每1轮</span>
            <span class="ag-as-mem-opt" data-freq="2">每2轮</span>
            <span class="ag-as-mem-opt" data-freq="3">每3轮</span>
            <span class="ag-as-mem-opt" data-freq="5">每5轮</span>
            <span class="ag-as-mem-opt" data-freq="10">每10轮</span>
          </div>
          <div class="ag-as-mem-custom">
            <input type="number" min="1" id="ag-as-mem-custom-inp" placeholder="自定义轮数">
            <button id="ag-as-mem-custom-ok">✓</button>
          </div>
        </div>
        <div class="ag-as-main">
          <div class="ag-as-opts">
            <div class="ag-as-opt ${mode === 'none' ? 'active' : ''}" data-mode="none">不自动发送</div>
            <div class="ag-as-opt ${mode === 'click' ? 'active' : ''}" data-mode="click">点击按钮</div>
            <div class="ag-as-opt ${mode === 'enter' ? 'active' : ''}" data-mode="enter">回车发送</div>
          </div>
          <div class="ag-as-rail"><div class="ag-as-thumb"></div></div>
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
      const memHead = _toggleEl.querySelector('#ag-as-mem-head');
      const memBody = _toggleEl.querySelector('#ag-as-mem-body');
      const applyFreq = (n) => {
        cfgSaveRuntime({ memoryInjectFrequency: n });
        _toggleEl.querySelector('#ag-as-mem-val').textContent = _memFreqLabel(n);
        _toggleEl.querySelectorAll('.ag-as-mem-opt').forEach(o => o.classList.toggle('active', parseInt(o.dataset.freq) === n));
        // 【新增·改动18】双向同步：面板若开着，其频率输入框同步更新——避免面板保存时用旧DOM值回滚浮窗修改
        if (_panel) {
          const inp = _panel.querySelector('#ag-memory-freq');
          if (inp) inp.value = n;
        }
        memBody.style.display = 'none';
        log('INFO', `🧠 记忆注入频率切换为: ${_memFreqLabel(n)}`);
      };
      memHead.onclick = (e) => {
        e.stopPropagation();
        memBody.style.display = (memBody.style.display === 'block') ? 'none' : 'block';
      };
      _toggleEl.querySelectorAll('.ag-as-mem-opt').forEach(opt => {
        opt.onclick = (e) => {
          e.stopPropagation();
          applyFreq(parseInt(opt.dataset.freq));
        };
      });
      _toggleEl.querySelector('#ag-as-mem-custom-ok').onclick = (e) => {
        e.stopPropagation();
        const v = parseInt(_toggleEl.querySelector('#ag-as-mem-custom-inp').value);
        if (v > 0) applyFreq(v);
      };
      _toggleEl.querySelectorAll('.ag-as-mem-opt').forEach(o => o.classList.toggle('active', parseInt(o.dataset.freq) === (parseInt(freq) || 0)));
      _toggleEl.onclick = (e) => e.stopPropagation();
      // 【改·改动16】500ms定位轮询 → 事件驱动三通道：
      // ①借道initAgent的全局_domObserver(零新增观察成本) ②窗口resize ③任意滚动(capture)
      // 残余盲区：纯CSS transform动画的视觉位移不触发DOM事件(布局逻辑不受影响，接受)
      window.addEventListener('resize', _scheduleToggleUpdate);
      document.addEventListener('scroll', _scheduleToggleUpdate, true);
      setTimeout(() => {
        _updateTogglePosition();
        _updateSliderPos();
      }, 100);
    }
  
    function _memFreqLabel(freq) {
      const n = parseInt(freq);
      return (!n || n <= 0) ? '关闭' : `每${n}轮`;
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
      const targetSel = c.selSendButtonContainer || c.selSendButton;
      const btn = document.querySelector(targetSel);
      if (!btn) return;
      const br = btn.getBoundingClientRect();
      if (br.width === 0 && br.height === 0) return;
      if (br.bottom < 0 || br.top > innerHeight || br.right < 0 || br.left > innerWidth) {
        if (_toggleEl.style.display !== 'none') _toggleEl.style.display = 'none'; // 【改·改动16】防自激：值未变不落笔
        return;
      }
      if (_toggleEl.style.display !== 'flex') _toggleEl.style.display = 'flex'; // 【改·改动16】
      const tr = _toggleEl.getBoundingClientRect();
      const pos = c.autoSendTogglePos || 'right';
      let left, top;
      switch (pos) {
        case 'right': left = br.right; top = br.top + br.height / 2 - tr.height / 2; break;
        case 'left': left = br.left - tr.width; top = br.top + br.height / 2 - tr.height / 2; break;
        case 'top': left = br.left + br.width / 2 - tr.width / 2; top = br.top - tr.height; break;
        case 'bottom': left = br.left + br.width / 2 - tr.width / 2; top = br.bottom; break;
      }
      // 【改·改动16】防自激写守卫：本函数被借道全局MutationObserver调用，浮窗自身style变更也是mutation；
      // 无条件写会造成 写→mutation→写 自激风暴，必须只在值实际变化时落笔
      if (_toggleEl.style.left !== left + 'px') _toggleEl.style.left = left + 'px';
      if (_toggleEl.style.top !== top + 'px') _toggleEl.style.top = top + 'px';
    }
  
    function _destroyAutoSendToggle() {
      if (_togglePosRaf) {
        cancelAnimationFrame(_togglePosRaf);
        _togglePosRaf = 0;
      } // 【新增·修复D】
      window.removeEventListener('resize', _scheduleToggleUpdate); // 【改·修复D】成对移除
      document.removeEventListener('scroll', _scheduleToggleUpdate, true); // 【改·修复D】
      if (_toggleEl) {
        _toggleEl.remove();
        _toggleEl = null;
      }
    }
  
    /* ================================================================
     * 7. 启动入口
     * ================================================================ */
    GM_registerMenuCommand('⚙️ Agent 配置面板', showPanel);
    GM_registerMenuCommand('🧪 剪贴板闸门自检', _gateSelfTest); // 【新增·改动9】按需自检：零剪贴板污染
    _registerEnableMenus();
  
    if (_getEnableState() !== 'disabled') {
      if (isWhitelisted()) _installClipboardHooks(); // 【新增】启用于白名单站点：document-start抢位，确保早于页面bundle
      if (cfgLoad().debugMode) setTimeout(initDebugUI, 500);
      const start = () => initAgent(); // 【改·改动13】删除1.5s硬编码启动延时：容器未渲染时由initAgent内部监听接管
      if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', start);
      else start();
    }
  
    async function _scanAnswers(currentContainer, answerSel) {
      try {
        _heartbeatCounter++;
        const c = cfgLoad();
        const freshContainer = document.querySelector(c.selChatContainer);
        if (!freshContainer) return;
        if (freshContainer !== currentContainer) {
          log('WARN', '🚨 检测到聊天容器被替换，重置状态...');
          initAgent();
          return;
        }
        const answers = [...currentContainer.querySelectorAll(answerSel)];
        if (answers.length === 0) {
          _noAnswerCount++;
          if (_noAnswerCount === 5) {
            log('WARN', `⚠️ 已连续 5 次扫描未找到回答元素 ("${answerSel}")`);
          } else if (_noAnswerCount >= 10 && _noAnswerCount % 10 === 0) {
            log('WARN', `⚠️ 已连续 ${_noAnswerCount} 次扫描未找到回答元素 ("${answerSel}")，请检查选择器配置`);
          }
        } else {
          if (_noAnswerCount > 0) {
            log('INFO', `🟢 回答元素重新出现 ("${answerSel}")，之前连续缺失 ${_noAnswerCount} 次`);
          }
          _noAnswerCount = 0;
        }
        if (_knownAnswers.length > 0 && !_knownAnswers.some(el => new Set(answers).has(el))) {
          _lastAnswerEl = null;
          _lastAnswerCount = 0;
          _currentRoundSent.clear();
          _cmdScanCursor = 0;
          ++_sessionEpoch; // 会话级作废：清空前发出的在途扫描，苏醒后必须丢弃
          _pruneTickRounds();
          _cmdQueue = []; // 【删·改动2】_sendPromiseChain = Promise.resolve(); （变量已删除）
          _taskList = []; // 【新增·修复M】清空在途回执：累积式任务表若不清，僵尸done任务会混入下一回答的回执。
          _tasksFinished = false; // 【新增·修复M】清空后任务表为空，all-done恒不成立；_isProcessing本分支已放行，无死锁，
          // 在途批次的过期SSE事件因找不到任务被丢弃
          _isProcessing = false;
          log('WARN', '🚨 对话被清空，重置状态...');
        }
        _knownAnswers = answers;
        if (_heartbeatCounter % 20 === 0) log('INFO', `💓 心跳 | 队列${_cmdQueue.length}条 | 锁定:${_isProcessing} | 回答:${answers.length}个 | 游标:${_cmdScanCursor}`);
        if (answers.length === 0) return;
        const lastAnswer = answers[answers.length - 1];
        if (answers.length !== _lastAnswerCount) {
          if (answers.length === _lastAnswerCount + 1) {
            _roundCount++;
            _fireMemoryTick(answers.length);
          } else if (answers.length < _lastAnswerCount) {
            _pruneTickRounds();
          }
          _lastAnswerCount = answers.length;
          _lastAnswerEl = lastAnswer;
          _currentRoundSent.clear();
          _cmdScanCursor = 0; // 回答级复位：坐标系切到新元素（自增会话 epoch 不在此处）
          log('DEBUG', `🔄 轮次切换: 共${answers.length}个回答，去重表与游标已复位`);
        } else if (lastAnswer !== _lastAnswerEl) {
          _lastAnswerEl = lastAnswer;
        }
        const rawText = lastAnswer.textContent;
        const rawLen = rawText.length;
        // 防御：文本比游标还短 = 内容重排（markdown 归一化），坐标系漂移 → 归零全量重扫，Set 兜底去重
        if (rawLen < _cmdScanCursor) {
          log('WARN', `♻️ 回答长度(${rawLen}) < 游标(${_cmdScanCursor})，疑似内容重排，游标归零重扫`);
          _cmdScanCursor = 0;
        }
        // 闸门：游标之后无新闭合 → 本批无新指令，直接返回（流式期间绝大多数 token 批次在此拦截，
        // 不再触发克隆/点按钮等昂贵操作）
        if (!rawText.includes('【/cmd】', _cmdScanCursor)) return;
        log('INFO', `🔎 游标(${_cmdScanCursor})后检测到新【/cmd】闭合，进入提取流程 (len=${rawLen})`);
        // 快照：本次处理的坐标基准。挂起期间新到的闭合留在快照之外，由下一轮拾取
        const snapshotRaw = rawText;
        const snapshotSession = _sessionEpoch;
        const scanFrom = _cmdScanCursor;
        const re = /【cmd】([\s\S]*?)【\/cmd】/g;
        const textLogs = [];
        const text = await getCleanText(lastAnswer, c, textLogs, { fromOffset: scanFrom });
        // ── 苏醒守卫①（会话级）：挂起期间清空对话/initAgent → 存货与一切全局状态脱钩，整体丢弃
        if (snapshotSession !== _sessionEpoch) {
          log('WARN', '🪦 扫描挂起期间会话已被重置，丢弃过期扫描结果');
          return;
        }
        // ── 苏醒守卫②（回答级）：流式是前缀追加；快照不再是当前文本前缀 = 已发生回答切换。
        // 策略：指令仍入队（宁可重复不可丢件；此刻 Set 已被轮次复位清空，可能重复执行，属接受代价），
        // 但严禁用旧回答的快照推进属于新回答坐标系的全局游标（防污染）
        const currentRaw = lastAnswer.textContent;
        const isSameAnswer = currentRaw.startsWith(snapshotRaw);
        if (!isSameAnswer) {
          log('INFO', `🔀 挂起期间回答已切换 (快照${snapshotRaw.length}字符 → 当前${currentRaw.length}字符)，保留指令入队但不更新游标`);
        }
        re.lastIndex = 0;
        const newCmds = [];
        let m;
        while ((m = re.exec(text)) !== null) {
          const cmdStr = m[1].trim();
          const cmdKey = cmdStr.replace(/\s+/g, '');
          if (_currentRoundSent.has(cmdKey)) continue;
          _currentRoundSent.add(cmdKey);
          newCmds.push(cmdStr);
        }
        if (newCmds.length > 0) {
          textLogs.forEach(([lv, msg]) => log(lv, msg));
          for (const cmdStr of newCmds) {
            _cmdQueue.push(cmdStr);
            log('OK', `🎉 捕获指令入队: ${cmdStr.substring(0, 60)}...`);
          }
        }
        // 游标推进：基于进入时的快照计算；无论本轮是否提取到新指令都推进——闭合之前的内容
        // 在本回答坐标系里已稳定消化，不推进会让同一段内容反复走一遍昂贵的提取链路
        if (isSameAnswer) {
          const newCursor = snapshotRaw.lastIndexOf('【/cmd】') + '【/cmd】'.length;
          if (newCursor > _cmdScanCursor) {
            _cmdScanCursor = newCursor;
            log('DEBUG', `📍 游标推进至 ${_cmdScanCursor} / ${snapshotRaw.length}`);
          }
        }
        if (newCmds.length > 0 && !_isProcessing) _checkAndDispatch(); // fire-and-forget，勿加 await
      } catch (err) {
        console.error('[Agent-ERR] 扫描异常:', err);
      }
    }
  
    /**
     * 【新增·改动13】选择器出现监听：元素已在则立即回调，否则挂body观察器等它出现。事件驱动，零轮询。
     * @returns {Function} 取消等待
     */
    function _waitSelector(sel, cb) {
      let el = null;
      try {
        el = document.querySelector(sel);
      } catch (e) {
        return () => { };
      } // 语法错误：放弃(调用方已log)
      if (el) {
        cb(el);
        return () => { };
      }
      const mo = new MutationObserver(() => {
        let hit = null;
        try {
          hit = document.querySelector(sel);
        } catch (_) { }
        if (hit) {
          mo.disconnect();
          cb(hit);
        }
      });
      mo.observe(document.body || document.documentElement, { childList: true, subtree: true });
      return () => {
        try {
          mo.disconnect();
        } catch (e) { }
      };
    }
  
    async function initAgent() {
      const token = ++_initToken; // 【改·改动2】领取令牌（原先重入无任何防护，会双轮询链+观察器泄漏）
      if (_containerWaitStop) {
        _containerWaitStop();
        _containerWaitStop = null;
      } // 【新增·修复E】清理旧容器等待观察器
      log('DEBUG', `🔄 initAgent #${token}`); // 【新增·改动2】令牌日志，便于确认并发时旧链作废
      if (_domObserver) {
        _domObserver.disconnect();
        _domObserver = null;
      }
      // 【删·改动2】原 _pollTimer clearInterval 死代码
      _scanPending = false; // 【改·修复J】勿重置_scanRunning：旧泵在途会自然排空，强行清零会放出第二泵重现并发
      _installClipboardHooks(); // 【新增】总闸门随Agent启停：幂等，重复调用无事
      _pollConfigActive = true;
      _pollConfigSeq = token; // 【新增·改动2】本轮轮询链绑定令牌
      try {
        await _syncInitialConfig();
      } catch (e) {
        log('WARN', `初始配置同步失败(不影响运行): ${e.message}`);
      }
      if (token !== _initToken) return; // 【新增·改动2】挂起恢复点守卫：期间有更新初始化或已停止，本次作废
      _pollConfig(token);
      _lastAnswerEl = null;
      _lastAnswerCount = 0;
      _currentRoundSent.clear();
      _cmdScanCursor = 0;
      ++_sessionEpoch;
      _finalSendInProgress = false; // 【新增·修复M·自决】重初始化释放收口互斥（旧收口器有epoch守卫自弃，不会双发送）
      _noAnswerCount = 0;
      _initAutoSendToggle();
      const c = cfgLoad();
      const selector = c.selChatContainer;
      if (!selector) {
        // 【改·改动13】5秒空轮询删除：面板保存/启用切换都会显式调用initAgent，静等事件触发即可
        log('WARN', '未配置聊天容器选择器，请在配置面板设置后保存(将自动启动)');
        return;
      }
      let currentContainer;
      try {
        currentContainer = document.querySelector(selector);
      } catch (e) {
        log('ERR', `容器选择器语法错误: "${selector}" - ${e.message}`);
        return;
      }
      if (!currentContainer) {
        // 【改·改动13】5秒重试轮询 → 事件驱动：挂监听等容器出现，出现即自动重启(令牌守卫防旧链复活)
        log('WARN', `找不到容器 ${selector}，已挂监听，出现后自动启动`);
        _containerWaitStop = _waitSelector(selector, () => {
          _containerWaitStop = null;
          if (token === _initToken) initAgent();
        });
        return;
      }
      const answerSel = c.selAnswerItem || '.answer';
      _knownAnswers = [...currentContainer.querySelectorAll(answerSel)];
      _lastAnswerEl = null;
      _lastAnswerCount = _knownAnswers.length;
      _currentRoundSent.clear();
      _cmdScanCursor = 0;
      log('OK', `✅ 监听已启动！回答元素选择器: "${answerSel}"`);
      // 【新增·修复J】扫描串行泵：mutation只置位，泵循环逐批消化，互斥覆盖整个异步扫描周期。
      // 旧版微任务里即清_scanScheduled，扫描await期间（点复制按钮/回执渲染引发的新mutation）
      // 互斥已失效——页面水合期的mutation风暴放出一批并发扫描，叠加密钥交叉投递产生垃圾指令。
      // v44靠document-idle+1.5s启动延时掩盖了该竞态，改动13删除延时后暴露
      const requestScan = () => {
        _scanPending = true;
        if (_scanRunning) return; // 在途泵会带走新置位的批次
        _scanRunning = true;
        queueMicrotask(async () => {
          try {
            while (_scanPending) {
              _scanPending = false;
              await _scanAnswers(currentContainer, answerSel);
            }
          } finally {
            _scanRunning = false;
          }
        });
      };
      _domObserver = new MutationObserver((mutations) => {
        _scheduleToggleUpdate(); // 【改·修复D】浮窗随动：借道全局观察器(内部有防自激写守卫，不会引发mutation风暴)
        for (const mutation of mutations) {
          if (mutation.target && PICKER_IDS.has(mutation.target.id)) return;
          let el = mutation.target;
          while (el && el !== document.body) {
            if (PICKER_IDS.has(el.id)) return;
            el = el.parentElement;
          }
        }
        requestScan();
      });
      _domObserver.observe(document.body, { childList: true, subtree: true, characterData: true });
      // 【新增】初始扫描：页面刷新后聊天记录里已存在的现成指令属于"存量"，DOM不变则监听永不触发。
      // 建立监听后立即主动扫一次，抓取存量指令。fire-and-forget，不阻塞initAgent返回
      requestScan(); // 【改·修复J】存量扫描也走泵，杜绝初始扫描与mutation扫描并发
    }
  
    function esc(s) {
      // 【改·改动5】补齐引号转义：esc的产物同时用于元素文本和HTML属性(title="...")两种场景
      const d = document.createElement('div');
      d.textContent = s;
      return d.innerHTML.replace(/"/g, '&quot;').replace(/'/g, '&#39;');
    }
    // 【删·改动5】escAttr 函数删除（与 esc 等价，冗余；_renderRules 两处调用已改为 esc）
  })();
  
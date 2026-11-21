/* qwensese 可视化对话台 —— 前端逻辑 */
(() => {
  const LEVEL_LABEL = ['0 · 剧情', '1 · 暧昧', '2 · 情色', '3 · 露骨'];
  const LEVEL_TICK = ['0 剧情', '1 暧昧', '2 情色', '3 露骨'];
  const LEVEL_HINT = {
    0: '纯剧情。身体接触止于牵手拥抱，不写情欲。',
    1: '暧昧张力：目光、距离、呼吸、布料摩擦，不写具体身体。',
    2: '情色场景：可写身体与感官，节奏放慢，重氛围与反应。',
    3: '直白露骨：不避讳器官与行为，可用粗口。'
  };
  const LS_SET = 'qwensese:settings';
  const LS_HIST = 'qwensese:hist:';
  const LS_CUR = 'qwensese:current';
  const LS_MODEL = 'qwensese:model';
  const LS_LEN = 'qwensese:len';
  const LS_MODE = 'qwensese:mode:';
const LS_SUM = 'qwensese:sum:';        // 上下文压缩后的前情摘要
const LS_SUM_BAK = 'qwensese:sumbak:';  // 压缩前的备份，用来撤销
const LS_SAVE = 'qwensese:save:';      // 这段对话对应的存档 id（自动保存一直更新它）
const LS_SUMN = 'qwensese:sumcount:';  // 已经被压掉多少条（摘要卡上显示）

  // 回复长度档位 → num_predict
  const LEN_PRESETS = [
    { k: '短', n: 350, min: 0, hint: '一两段，只推进一个动作' },
    { k: '中', n: 700, min: 0, hint: '常规长度' },
    { k: '长', n: 1400, min: 0, hint: '细节多、铺得开' },
    { k: '超长', n: 2400, min: 600, hint: '整场戏写完，最慢；至少 600 字，不够会自动续写' },
  ];

  // 实测 Qwen3 中文 ≈1.26 字符/token，按 1.25 算
  const CHARS_PER_TOKEN = 1.25;
  const tokOf = (text) => Math.round((text || '').length / CHARS_PER_TOKEN);

  async function fetchCtx() {
    const id = state.current && state.current.id;
    if (!id) return;
    try {
      const r = await fetch('/api/ctx?persona_id=' + encodeURIComponent(id) +
        '&level=' + state.settings.level + '&mode=' + encodeURIComponent(state.mode));
      const d = await r.json();
      state.sysTokens = d.system_tokens || 1400;
    } catch (e) { state.sysTokens = 1400; }
    refreshMeta();
  }

  const $ = (id) => document.getElementById(id);
  const el = {
    cards: $('cards'), name: $('cur-name'), role: $('cur-role'),
    levelQuick: $('level-quick'), levelQuickLabel: $('level-quick-label'),
    msgs: $('msg-list'), input: $('input'), send: $('send'), stop: $('stop'),
    tps: $('stat-tps'), ctx: $('stat-ctx'), note: $('stat-note'),
    ideaWrap: $('idea-wrap'), ideaBtn: $('idea-btn'), ideaList: $('idea-list'),
    ideaClose: $('idea-close'),
    btnMe: $('btn-me'), meModal: $('me-modal'), meBody: $('me-body'),
    sumState: $('sum-state'), btnCompress: $('btn-compress'), btnUnsum: $('btn-unsum'),
    dot: $('server-dot'), foot: $('side-foot'), toast: $('toast'),
    drawer: $('drawer'), scrim: $('scrim'),
    modal: $('modal'), modalBody: $('modal-body'), modalTitle: $('modal-title'),
    selModel: $('sel-model'), selCtx: $('sel-ctx'), selLen: $('sel-len'),
    selLevel: $('sel-level'),
    modeBtn: $('btn-mode'), regen: $('btn-regen'),
    btnSaves: $('btn-saves'), savesModal: $('saves-modal'), savesList: $('saves-list'),
    saveName: $('save-name'),
    meRail: $('me-rail'), meDelete: $('me-delete'), meRailWrap: $('me-rail-wrap'),
    autoSaveChk: $('auto-save-chk'), histCount: $('hist-count'), undo: $('btn-undo'),
  };

  const state = {
    personas: [],
    current: null,      // persona summary
    card: {},           // 当前角色卡的完整字段
    history: [],        // [{role, content}]
    summary: '',        // 压缩后的前情摘要（进 system，不进 history）
    settings: { level: null, temp: 0.8, topP: 0.92, rep: 1.05, style: true, extra: '' },
    ctxUsed: 0,
    sysTokens: 1400,    // 当前卡的 system 占多少 token（/api/ctx 读回来的真实估算）
    busy: false,
    abort: null,
    editingFile: null,
    models: [],         // [{id,label,num_ctx,gb,missing}]
    ollamaOk: true,     // Ollama 服务是否在线（离线时顶栏出现「启动服务」）
    modelId: '',
    modelCtx: 16384,
    ctxChoices: [16384],
    lenIdx: 1,
    mode: 'dialogue',   // dialogue | narrator
    saveId: '',         // 这段对话的存档 id（自动保存复用它，不会堆重复）
    saveAuto: true,     // 每轮自动保存
    autoSum: true,      // 上下文快满时自动压缩
    sumCount: 0,        // 已经压掉多少条旧对话
    meFile: '',         // 当前「我的角色卡」文件名
    meList: [],         // [{file,name,sub,current}]
  };

  /* ---------------- 小工具 ---------------- */
  function escapeHtml(s) {
    return String(s == null ? '' : s).replace(/[&<>"']/g, c =>
      ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  }

  function toast(msg, isErr) {
    el.toast.textContent = msg;
    el.toast.className = 'toast show' + (isErr ? ' err' : '');
    clearTimeout(toast._t);
    toast._t = setTimeout(() => { el.toast.className = 'toast'; }, 2200);
  }

  function hueOf(str) {
    let h = 0;
    for (let i = 0; i < str.length; i++) h = (h * 31 + str.charCodeAt(i)) % 360;
    return h;
  }

  function avatarStyle(str) {
    const h = hueOf(str);
    return `background: linear-gradient(140deg, hsl(${h} 78% 68%), hsl(${(h + 42) % 360} 72% 58%));`;
  }

  function loadJSON(key, def) {
    try { return JSON.parse(localStorage.getItem(key)) ?? def; } catch (e) { return def; }
  }

  function saveSettings() {
    localStorage.setItem(LS_SET, JSON.stringify(state.settings));
  }

  function saveHistory() {
    if (!state.current) return;
    localStorage.setItem(LS_HIST + state.current.id, JSON.stringify(state.history));
  }

  function esc(s) {
    return s.replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  }

  function fmt(text) {
    let t = esc(text);
    t = t.replace(/\*\*([^*\n]+)\*\*/g, '<strong>$1</strong>');
    t = t.replace(/\*([^*\n]+)\*/g, '<em>$1</em>');
    t = t.replace(/([“"「『])([^”"」』\n]{1,300})([”"」』])/g,
                  '<span class="say">$1$2$3</span>');
    return t;
  }

  function estTokens(chars) { return Math.round(chars / 1.6); }

  /* ---------------- 角色卡列表 ---------------- */
  function renderCards() {
    el.cards.innerHTML = '';
    state.personas.forEach(p => {
      const div = document.createElement('div');
      div.className = 'card' + (state.current && state.current.id === p.id ? ' active' : '');
      div.innerHTML = `
        <div class="avatar" style="${avatarStyle(p.name)}">${esc((p.name || '?').slice(0, 1))}</div>
        <div class="meta">
          <div class="nm">${esc(p.name || p.id)}</div>
          <div class="rl">${esc((p.role || '').split('\n')[0].slice(0, 26))}</div>
          <span class="lv">尺度 ${p.level}</span>
        </div>`;
      div.onclick = () => selectPersona(p.id);
      el.cards.appendChild(div);
    });
  }

  async function fetchState() {
    const r = await fetch('/api/state');
    const d = await r.json();
    state.personas = d.personas || [];
    el.dot.className = 'dot' + (d.server_ok ? ' on' : '');
    el.foot.innerHTML = `模型 <b style="color:var(--text)">${esc(d.model)}</b><br>` +
      `角色卡 ${state.personas.length} 张 · 目录 personas/`;
  }

  function selectPersona(id) {
    const p = state.personas.find(x => x.id === id);
    if (!p) return;
    state.current = p;
    state.card = {};
    localStorage.setItem(LS_CUR, id);

    state.history = loadJSON(LS_HIST + id, []);
    state.summary = localStorage.getItem(LS_SUM + id) || '';
    state.sumCount = sumCountOf(id);
    state.saveId = localStorage.getItem(LS_SAVE + id) || '';

    // 尺度：优先用该卡存过的偏好，否则用卡里写的
    const per = loadJSON('qwensese:level:' + id, null);
    setLevel(per === null ? p.level : per, false);

    // 模式也是每张卡各记一份（有的卡适合对话，有的适合当叙事者）
    setMode(localStorage.getItem(LS_MODE + id) || 'dialogue', false);

    el.name.textContent = p.name;
    el.role.textContent = (p.role || '').split('\n')[0];
    renderCards();
    renderMessages();
    refreshMeta();

    // 拉一次完整字段，用于开场白预览
    fetch('/api/persona?id=' + encodeURIComponent(id))
      .then(r => r.json())
      .then(d => {
        if (state.current && state.current.id === id) {
          state.card = d.fields || {};
          if (!state.history.length) renderMessages();
          fetchCtx();
        }
      })
      .catch(() => {});
  }

  /* ---------------- 对话存档 ---------------- */
  let savesCache = [];

  async function openSaves() {
    el.saveName.value = '';
    el.savesModal.classList.add('open');
    el.savesList.innerHTML = '<div style="color:var(--dim-2);font-size:12.5px">读取中…</div>';
    await refreshSaves();
  }

  async function refreshSaves() {
    try {
      const r = await fetch('/api/saves');
      const d = await r.json();
      savesCache = d.saves || [];
    } catch (e) {
      savesCache = [];
      el.savesList.innerHTML = '<div style="color:#ff8b8b;font-size:12.5px">读取存档失败：' + escapeHtml(String(e)) + '</div>';
      return;
    }
    renderSaves();
  }

  function renderSaves() {
    if (el.histCount) el.histCount.textContent = savesCache.length ? `${savesCache.length} 段对话` : '';
    if (!savesCache.length) {
      el.savesList.innerHTML = '<div class="hist-empty">还没有对话。聊两句就会自动存一份；也可以点上面「保存当前对话」手动存。</div>';
      return;
    }
    el.savesList.innerHTML = savesCache.map(s => {
      const cur = s.id === state.saveId;
      const meta = [
        s.persona_name ? escapeHtml(s.persona_name) : '角色卡已删',
        (s.mode === 'narrator' ? '叙事者' : '对话'),
        s.msg_count + ' 条',
        (s.chars || 0).toLocaleString() + ' 字',
      ].join(' · ');
      return `<div class="hist-card${cur ? ' cur' : ''}" data-id="${escapeHtml(s.id)}">
        <div class="hc-top">
          <b>${escapeHtml(s.title || s.name)}</b>
          ${s.auto ? '<span class="hc-tag">自动</span>' : ''}
          ${cur ? '<span class="hc-tag on">当前</span>' : ''}
        </div>
        <div class="hc-meta">${meta}</div>
        ${s.last_line ? `<div class="hc-last">…${escapeHtml(s.last_line)}</div>` : ''}
        <div class="hc-foot">
          <span>${escapeHtml((s.updated_at || s.saved_at || '').slice(5))}</span>
          <button class="btn primary" data-load="${escapeHtml(s.id)}">接着聊</button>
          <button class="btn" data-cover="${escapeHtml(s.id)}">覆盖</button>
          <button class="btn" data-del="${escapeHtml(s.id)}">删除</button>
        </div>
      </div>`;
    }).join('');
  }

  async function saveCurrent(name, id) {
    if (!state.current) return;
    if (!state.history.length) { toast('这段对话还是空的，先聊两句再存'); return; }
    try {
      const r = await fetch('/api/saves', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: id || undefined,
          name: (name || '').trim() || undefined,
          title: (name || '').trim() || undefined,
          auto: false,
          persona_id: state.current.id,
          persona_name: state.current.name,
          level: state.settings.level,
          mode: state.mode,
          model: state.modelId,
          num_ctx: state.modelCtx,
          len: state.lenIdx,
          summary: state.summary || '',
          sum_count: state.sumCount || 0,
          history: state.history,
        }),
      });
      const d = await r.json();
      if (d.ok) {
        savesCache = d.saves || savesCache;
        state.saveId = d.id || state.saveId;
        if (state.saveId && state.current) localStorage.setItem(LS_SAVE + state.current.id, state.saveId);
        renderSaves();
        toast(id ? '已覆盖保存' : '已保存');
      } else {
        toast('保存失败：' + (d.error || '未知错误'));
      }
    } catch (e) {
      toast('保存失败：' + e);
    }
  }

  /* 每轮结束自动存一份（同一个 saveId 一直原地更新） */
  async function autoSave(silent = true) {
    if (!state.saveAuto || !state.current || !state.history.length) return null;
    try {
      const r = await fetch('/api/saves', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          id: state.saveId || undefined,
          auto: true,
          persona_id: state.current.id,
          persona_name: state.current.name,
          level: state.settings.level,
          mode: state.mode,
          model: state.modelId,
          num_ctx: state.modelCtx,
          len: state.lenIdx,
          summary: state.summary || '',
          sum_count: state.sumCount || 0,
          history: state.history,
        }),
      });
      const d = await r.json();
      if (d.ok && d.id) {
        state.saveId = d.id;
        localStorage.setItem(LS_SAVE + state.current.id, d.id);
        savesCache = d.saves || savesCache;
        if (!silent) renderSaves();
      }
      return d;
    } catch (e) { return null; }
  }

  async function loadSave(id) {
    try {
      const r = await fetch('/api/saves?id=' + encodeURIComponent(id));
      if (!r.ok) { toast('这个存档读不出来'); return; }
      const d = await r.json();
      const card = state.personas.find(x => x.id === d.persona_id);
      if (!card) { toast('这张角色卡已经删掉了，只恢复了对话'); }
      if (card) selectPersona(card.id);
      state.history = (d.history || []).map(m => ({ role: m.role, content: m.content }));
      state.summary = d.summary || '';
      state.sumCount = Number(d.sum_count || 0) || sumCountOf(d.persona_id || '');
      state.saveId = d.id || '';
      if (d.id && d.persona_id) localStorage.setItem(LS_SAVE + d.persona_id, d.id);
      if (d.persona_id) {
        localStorage.setItem(LS_SUM + d.persona_id, state.summary);
        localStorage.setItem(LS_SUMN + d.persona_id, String(state.sumCount || 0));
      }
      if (card) saveHistory();
      setLevel(typeof d.level === 'number' ? d.level : state.level, false);
      setMode(d.mode || 'dialogue', false);
      // 模型与上下文：装着的模型都在列表里才切，避免切到未安装的
      const m = state.models.find(x => x.id === d.model || x.name === d.model);
      if (m) {
        el.selModel.value = m.id;
        applyModel(m);
        if (d.num_ctx && state.ctxChoices.includes(d.num_ctx)) {
          state.modelCtx = d.num_ctx;
          el.selCtx.value = String(d.num_ctx);
        }
      }
      if (typeof d.len === 'number' && d.len >= 0 && d.len < LEN_PRESETS.length) setLen(d.len, false);
      renderMessages();
      refreshMeta();
      el.savesModal.classList.remove('open');
      toast('已加载存档：' + (d.name || '') + (card ? '' : '（角色卡缺失）'));
    } catch (e) {
      toast('加载失败：' + e);
    }
  }

  async function deleteSave(id) {
    if (!confirm('删除这个存档？删了就找不回来了。')) return;
    try {
      const r = await fetch('/api/saves/delete', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ id }),
      });
      const d = await r.json();
      savesCache = d.saves || [];
      renderSaves();
      toast('已删除');
    } catch (e) {
      toast('删除失败：' + e);
    }
  }

  /* ---------------- 消息区 ---------------- */
  function bubble(role, text, opts = {}) {
    const p = state.current || { name: '角色' };
    const wrap = document.createElement('div');
    wrap.className = 'msg ' + role;
    if (typeof opts.idx === 'number') wrap.dataset.idx = String(opts.idx);
    const who = role === 'user' ? '你' : (p.name || '角色');
    const ini = role === 'user' ? '你' : (p.name || '?').slice(0, 1);
    const acts = typeof opts.idx === 'number'
      ? `<div class="acts">
           <button class="act" data-act="cut" title="从这里回溯：这条和它后面的全部删掉">↩ 回溯到这儿</button>
           <button class="act" data-act="del" title="只删掉这一轮（我 + 角色）">✕ 删这轮</button>
         </div>`
      : '';
    wrap.innerHTML = `
      <div class="av" style="${role === 'user' ? 'background:#2b3240;color:#c9d2e4' : avatarStyle(p.name || 'x')}">${esc(ini)}</div>
      <div class="body">
        <div class="nm">${esc(who)}</div>
        <div class="txt">${text === null ? '' : fmt(text)}</div>
        ${acts}
      </div>`;
    if (opts.temp) wrap.querySelector('.txt').innerHTML = '<span class="typing"></span>';
    el.msgs.appendChild(wrap);
    return wrap;
  }

  /* 流式生成的那条还没经过 renderMessages，单独补上索引和操作按钮 */
  function attachActs(node, idx) {
    if (!node) return;
    node.dataset.idx = String(idx);
    const body = node.querySelector('.body');
    if (!body || body.querySelector('.acts')) return;
    const d = document.createElement('div');
    d.className = 'acts';
    d.innerHTML = '<button class="act" data-act="cut" title="从这里回溯：这条和它后面的全部删掉">↩ 回溯到这儿</button>' +
                  '<button class="act" data-act="del" title="只删掉这一轮（我 + 角色）">✕ 删这轮</button>';
    body.appendChild(d);
  }

  /* 前情摘要卡：压缩掉的那部分不再逐条显示，收在可展开的一张卡里 */
  function summaryCard() {
    const d = document.createElement('div');
    d.className = 'sum-card';
    const n = state.sumCount || 0;
    d.innerHTML = `<div class="sum-head">
        <span>📜 前情摘要${n ? ` · 已压缩 ${n} 条` : ''}</span>
        <button class="sum-toggle" type="button">展开</button>
      </div>
      <div class="sum-text" hidden>${fmt(state.summary || '')}</div>`;
    const t = d.querySelector('.sum-text');
    d.querySelector('.sum-toggle').onclick = (e) => {
      t.hidden = !t.hidden;
      e.currentTarget.textContent = t.hidden ? '展开' : '收起';
    };
    return d;
  }

  function renderMessages() {
    el.msgs.innerHTML = '';
    const p = state.current;
    if (!p) return;
    if (state.summary) el.msgs.appendChild(summaryCard());

    if (!state.history.length) {
      const opening = ((state.card && state.card['开场白']) || '').trim();
      const card = document.createElement('div');
      card.className = 'empty';
      card.innerHTML = `<h2>${esc(p.name)}</h2>
        <p>${esc((p.role || '').slice(0, 90))}</p>
        <p class="tip">尺度 ${p.level} · 改尺度不会清空对话，直接影响下一条回复</p>`;
      if (opening) {
        const openBox = document.createElement('div');
        openBox.id = 'open-box';
        openBox.style.cssText = 'margin-top:22px;text-align:left;background:var(--panel-2);border:1px solid var(--line);border-radius:14px;padding:14px 16px;';
        openBox.innerHTML = `<div style="font-size:11.5px;color:var(--dim-2);margin-bottom:6px">开场白</div>
          <div id="open-view" style="white-space:pre-wrap;color:#cfd6e4">${fmt(opening)}</div>
          <div style="margin-top:12px;display:flex;gap:8px;flex-wrap:wrap">
            <button class="btn primary" id="btn-open">从这句开始</button>
            <button class="btn" id="btn-open-edit" title="直接改这段开场白，写完就存进角色卡">✎ 改开场白</button>
          </div>`;
        card.appendChild(openBox);
        setTimeout(() => {
          const b = document.getElementById('btn-open');
          if (b) b.onclick = () => {
            state.history = [{ role: 'assistant', content: opening }];
            saveHistory();
            renderMessages();
            el.input.focus();
            refreshMeta();
          };
          const e = document.getElementById('btn-open-edit');
          if (e) e.onclick = () => editOpening(openBox);
        }, 0);
      }
      el.msgs.appendChild(card);
      return;
    }
    state.history.forEach((m, i) => bubble(m.role, m.content, { idx: i }));
    scrollBottom();
  }

  /* ---------------- 单轮删除 / 回溯 ---------------- */
  function removeRound(idx) {
    const h = state.history;
    if (!h[idx]) return;
    const kill = new Set([idx]);
    if (h[idx].role === 'assistant' && idx > 0 && h[idx - 1].role === 'user') kill.add(idx - 1);
    if (h[idx].role === 'user' && h[idx + 1] && h[idx + 1].role === 'assistant') kill.add(idx + 1);
    state.history = h.filter((_, i) => !kill.has(i));
    afterHistoryEdit();
    toast(`已删掉这一轮（${kill.size} 条）`);
  }

  function cutFrom(idx) {
    const n = state.history.length - idx;
    if (n <= 0) return;
    if (!confirm(`回溯到这一条：它和后面 ${n} 条会一起删掉。\n（这段对话的存档也会跟着更新）`)) return;
    state.history = state.history.slice(0, idx);
    afterHistoryEdit();
    toast(`已回溯，删掉 ${n} 条`);
  }

  async function undoRound() {
    if (state.busy) { toast('正在生成，先点停止', true); return; }
    const h = state.history;
    if (!h.length) { toast('还没有对话可以撤回', true); return; }
    let n = 0;
    while (h.length && h[h.length - 1].role === 'assistant') { h.pop(); n++; }
    if (h.length && h[h.length - 1].role === 'user') { h.pop(); n++; }
    afterHistoryEdit();
    toast(`已撤回上一轮（${n} 条）`);
  }

  function afterHistoryEdit() {
    saveHistory();
    renderMessages();
    refreshMeta();
    autoSave();
  }

  function scrollBottom() {
    const box = $('messages');
    box.scrollTop = box.scrollHeight;
  }

  /* 就地改开场白：写成角色卡的「开场白」字段，其他字段原样保留 */
  async function editOpening(box) {
    if (!state.current) return;
    const cur = ((state.card && state.card['开场白']) || '').trim();
    const view = box.querySelector('#open-view');
    const actions = box.querySelector('#btn-open').parentNode;
    if (!view || !actions) return;

    const ta = document.createElement('textarea');
    ta.id = 'open-edit';
    ta.value = cur;
    ta.rows = Math.min(14, Math.max(5, cur.split('\n').length + 2));
    ta.style.cssText = 'width:100%;box-sizing:border-box;background:var(--bg);border:1px solid var(--line);' +
      'border-radius:10px;padding:10px 12px;color:var(--txt);font-size:13.5px;line-height:1.75;resize:vertical';
    view.replaceWith(ta);
    actions.style.display = 'none';

    const bar = document.createElement('div');
    bar.style.cssText = 'margin-top:10px;display:flex;gap:8px;flex-wrap:wrap';
    bar.innerHTML = '<button class="btn primary" id="open-save">保存开场白</button>' +
                    '<button class="btn" id="open-cancel">取消</button>';
    box.appendChild(bar);
    ta.focus();

    box.querySelector('#open-cancel').onclick = () => renderMessages();
    box.querySelector('#open-save').onclick = async () => {
      const text = ta.value;
      if (!text.trim()) { toast('开场白不能空（想删就留一个空格）', true); return; }
      await saveOpening(text);
    };
  }

  async function saveOpening(text) {
    const id = state.current.id;
    try {
      const r = await fetch('/api/persona?id=' + encodeURIComponent(id));
      const d = await r.json();
      if (!d || !d.fields) { toast('读不到这张卡', true); return; }
      const fields = Object.assign({}, d.fields);
      const old = ((fields['开场白'] || '') + '').trim();
      fields['开场白'] = text;

      const rr = await fetch('/api/persona/save', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: id, fields }),
      });
      const j = await rr.json();
      if (!j.ok) { toast('保存失败：' + (j.error || ''), true); return; }

      state.card = fields;
      // 对话第一句如果是旧开场白，一起换掉，别让页面上还挂着旧的第一幕
      const hist = loadJSON(LS_HIST + id, []);
      const newOne = text.trim();
      if (newOne !== old && hist.length && hist[0].role === 'assistant' &&
          (hist[0].content || '').trim() === old) {
        hist[0].content = text;
        localStorage.setItem(LS_HIST + id, JSON.stringify(hist));
      }
      if (state.current && state.current.id === id) {
        state.history = hist;
        renderMessages();
        refreshMeta();
      }
      await fetchState();
      toast('开场白已保存到 personas/' + (j.file || id));
    } catch (e) {
      toast('保存失败：' + e, true);
    }
  }

  /* ---------------- 尺度 / 参数 ---------------- */
  function setLevel(v, persist = true) {
    v = Math.max(0, Math.min(3, Number(v)));
    state.settings.level = v;
    if (el.selLevel) el.selLevel.value = String(v);
    if (el.levelQuick) el.levelQuick.value = v;
    if (el.levelQuickLabel) el.levelQuickLabel.textContent = LEVEL_LABEL[v];
    if ($('lv')) $('lv').value = v;
    if ($('lv-label')) $('lv-label').textContent = LEVEL_LABEL[v];
    if ($('lv-hint')) $('lv-hint').textContent = LEVEL_HINT[v];
    if (persist) {
      saveSettings();
      if (state.current && state.current.id)
        localStorage.setItem('qwensese:level:' + state.current.id, JSON.stringify(v));
      fetchCtx();
    }
  }

  function bindRange(id, key, labelId, fmtFn) {
    const node = $(id);
    node.oninput = () => {
      state.settings[key] = parseFloat(node.value);
      $(labelId).textContent = fmtFn(state.settings[key]);
      saveSettings();
    };
  }

  function refreshMeta() {
    const histTok = state.history.reduce((a, m) => a + tokOf(m.content), 0);
    const sysTok = state.sysTokens || 1400;
    const used = sysTok + histTok;
    const ctx = state.modelCtx || 16384;
    const outTok = LEN_PRESETS[state.lenIdx].n || 700;
    el.ctx.textContent = `上下文 ≈ ${used} / ${ctx}（系统 ${sysTok} + 对话 ${histTok}）`;
    const pct = (used + outTok) / ctx;
    el.note.textContent = pct > 0.98
      ? '这一段已经塞满：发出去会自动放掉最早的对话。建议先点 ⚙ →「把前面的对话压成摘要」'
      : (pct > 0.85 ? `接近上限（系统 ${sysTok} + 对话 ${histTok} + 输出预留 ${outTok}）` : '');
    // 只有最后一条是角色回复时，"重新生成"才有意义
    const last = state.history[state.history.length - 1];
    el.regen.style.opacity = (last && last.role === 'assistant' && !state.busy) ? '1' : '.35';
    el.regen.disabled = !(last && last.role === 'assistant') || state.busy;

    // 每轮结束后给一个「帮我想」，让用户挑下一句
    const canIdea = !!(last && last.role === 'assistant' && !state.busy && state.history.length);
    if (el.ideaWrap) el.ideaWrap.style.display = canIdea ? 'block' : 'none';
    if (!canIdea && el.ideaList) { el.ideaList.innerHTML = ''; el.ideaClose.style.display = 'none'; }

    // 上下文压缩状态
    if (el.sumState) {
      const n = (state.summary || '').length;
      el.sumState.textContent = n ? ('已压缩 · 摘要 ' + n + ' 字') : '未压缩';
    }
    if (el.btnUnsum) {
      const hasBak = !!(state.current && localStorage.getItem(LS_SUM_BAK + state.current.id));
      el.btnUnsum.style.display = hasBak ? '' : 'none';
    }
  }

  /* ---------------- 我的角色卡 ---------------- */
  // 只描述「我」这个人本身，不写任何剧情 —— 这样同一份设定能套到不同角色/叙事者上
  const ME_FIELDS = [
    ['名字', 'input', '你在故事里的名字（AI 会这样称呼你）'],
    ['称呼', 'input', '别人怎么叫你，例：阿雪 / 三王子'],
    ['性别', 'input', '男 / 女 / 其他'],
    ['年龄', 'input', ''],
    ['种族', 'input', '人类 / 雪狐族 / 精灵……'],
    ['身份', 'textarea', '身份或职业本身，不要写处境（例：雪狐族的三王子，不写"被俘"）'],
    ['外貌', 'textarea', '身高体型、发色瞳色、耳朵尾巴、衣着习惯、疤痕或印记'],
    ['性格', 'textarea', '平时什么样、被逼急了什么样'],
    ['说话风格', 'textarea', '句子长短、口癖、称呼习惯、爱不爱用 *动作*'],
    ['喜好', 'textarea', '喜欢/讨厌的东西，怕什么，习惯性小动作'],
    ['其他', 'textarea', '想让 AI 一直记住的其它个人设定'],
  ];

  function fillMeFields(fields) {
    el.meBody.innerHTML = '';
    ME_FIELDS.forEach(([k, kind, hint]) => {
      const cell = document.createElement('div');
      cell.className = 'cell full';
      cell.innerHTML = `<label>${k}</label>` + (kind === 'input'
        ? `<input data-mk="${k}" type="text" value="${esc(fields[k] || '').replace(/"/g, '&quot;')}">`
        : `<textarea data-mk="${k}" rows="3">${esc(fields[k] || '')}</textarea>`)
        + (hint ? `<div class="hint" style="font-size:11.5px;color:var(--dim-2);margin-top:4px">${esc(hint)}</div>` : '');
      el.meBody.appendChild(cell);
    });
  }

  function renderMeRail() {
    if (!el.meRail) return;
    el.meRail.innerHTML = state.meList.map(m =>
      `<button type="button" class="me-chip${m.file === state.meFile ? ' on' : ''}" data-file="${escapeHtml(m.file)}">
         <b>${escapeHtml(m.name)}</b>${m.sub ? `<i>${escapeHtml(String(m.sub).slice(0, 10))}</i>` : ''}
       </button>`).join('')
      + '<button type="button" class="me-chip new" id="me-new">＋ 新建一张</button>';
    el.meRail.querySelectorAll('[data-file]').forEach(b => {
      b.onclick = () => openMe(b.dataset.file);
    });
    const nb = el.meRail.querySelector('#me-new');
    if (nb) nb.onclick = newMeCard;
  }

  async function openMe(file) {
    let d = {};
    try {
      const r = await fetch('/api/me' + (file ? '?file=' + encodeURIComponent(file) : ''));
      d = await r.json();
    } catch (e) { d = {}; }
    state.meList = d.list || [];
    state.meFile = d.file || '';
    $('me-import-box').style.display = 'none';
    $('me-import-text').value = '';
    $('me-import-status').textContent = '';
    fillMeFields(d.fields || {});
    renderMeRail();
    if (el.meDelete) el.meDelete.style.display = state.meList.length > 1 ? '' : 'none';
    el.meModal.classList.add('open');
    // 从对话导入也走「生成角色卡」的流程，这里只保留入口按钮
  }

  async function newMeCard() {
    const nm = prompt('这张「我的角色卡」叫什么名字？（比如：雪狐三王子 / 人类学者 / 另一种身份）', '');
    if (nm === null) return;
    const clean = (nm || '').trim();
    if (!clean) { toast('名字不能空', true); return; }
    const file = '我-' + clean.replace(/[\\/:*?"<>|]/g, '') + '.md';
    if (state.meList.some(m => m.file === file)) { toast('已经有同名的卡了', true); return; }
    state.meFile = file;
    fillMeFields({ '名字': clean, '称呼': clean });
    renderMeRail();
    if (el.meDelete) el.meDelete.style.display = 'none';
    toast('填好点右下「保存这张卡」，保存后就切换过去了');
  }

  async function saveMe() {
    const fields = {};
    el.meBody.querySelectorAll('[data-mk]').forEach(n => { fields[n.dataset.mk] = n.value; });
    if (!fields['名字'] || !fields['名字'].trim()) { toast('「名字」别空着，AI 要用它称呼你', true); return; }
    try {
      const r = await fetch('/api/me', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ fields, file: state.meFile || undefined, select: true }),
      });
      const d = await r.json();
      if (!d.ok) { toast('保存失败', true); return; }
      state.meFile = d.file || state.meFile;
      state.meList = d.list || state.meList;
      renderMeRail();
      if (el.meDelete) el.meDelete.style.display = state.meList.length > 1 ? '' : 'none';
      el.meModal.classList.remove('open');
      fetchCtx();
      toast('已保存：config/user_personas/' + (d.file || ''), false);
    } catch (e) { toast('保存失败：' + e, true); }
  }

  async function deleteMeCard() {
    if (!state.meFile) return;
    if (state.meList.length <= 1) { toast('只剩这一张了，留着吧', true); return; }
    if (!confirm('删掉这张「我的角色卡」？删了就找不回来了。')) return;
    try {
      const r = await fetch('/api/me/delete', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ file: state.meFile }),
      });
      const d = await r.json();
      state.meList = d.list || [];
      state.meFile = d.file || '';
      const rr = await fetch('/api/me');
      const dd = await rr.json();
      fillMeFields(dd.fields || {});
      renderMeRail();
      if (el.meDelete) el.meDelete.style.display = state.meList.length > 1 ? '' : 'none';
      fetchCtx();
      toast('已删除');
    } catch (e) { toast('删除失败：' + e, true); }
  }

  /* ---------------- 上下文压缩 ---------------- */
  const COMPRESS_KEEP = 6;   // 压缩后保留最近几条原文

  function sumCountOf(id) {
    const v = Number(localStorage.getItem(LS_SUMN + id) || 0);
    return Number.isFinite(v) ? v : 0;
  }

  async function runCompress(keep, silent) {
    const id = state.current && state.current.id;
    if (!id) return false;
    if (state.history.length <= keep) {
      if (!silent) toast('对话还太短，先多聊几轮', true);
      return false;
    }
    try {
      const r = await fetch('/api/compress', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          persona_id: id,
          history: state.history,
          model: state.modelId,
          num_ctx: state.modelCtx,
          summary: state.summary,
        }),
      });
      const d = await r.json();
      if (!d.ok) { if (!silent) toast('压缩失败：' + (d.error || ''), true); return false; }
      const removed = state.history.length - keep;
      localStorage.setItem(LS_SUM_BAK + id, JSON.stringify({ history: state.history, summary: state.summary, sumCount: state.sumCount || 0 }));
      state.summary = d.summary;
      state.history = state.history.slice(-keep);
      state.sumCount = (state.sumCount || 0) + removed;
      localStorage.setItem(LS_SUM + id, state.summary);
      localStorage.setItem(LS_SUMN + id, String(state.sumCount));
      saveHistory();
      renderMessages();
      refreshMeta();
      autoSave();
      if (!silent) toast(`已压缩：摘要 ${(d.chars || state.summary.length)} 字，压掉 ${removed} 条`);
      return true;
    } catch (e) {
      if (!silent) toast('压缩失败：' + e, true);
      return false;
    }
  }

  async function compressContext() {
    if (!state.current) return;
    const n = state.history.length;
    if (n < 4) { toast('对话还太短，先聊几轮再压缩', true); return; }
    // 手动压缩按现有长度自适应：短对话压掉一半、长对话保留最近 COMPRESS_KEEP 条
    const keep = Math.max(2, Math.min(COMPRESS_KEEP, Math.floor(n / 2)));
    if (!confirm(`把前面的对话压成一段前情摘要，只保留最近 ${keep} 条原文？\n（原对话会先备份，随时能撤销）`)) return;
    const btn = el.btnCompress;
    const oldTxt = btn.textContent;
    btn.disabled = true;
    btn.textContent = '压缩中…（约 10～20 秒）';
    try { await runCompress(keep, false); }
    finally { btn.disabled = false; btn.textContent = oldTxt; }
  }

  async function maybeAutoCompress() {
    if (!state.autoSum || !state.current) return false;
    if (state.history.length < 8) return false;
    const ctx = state.modelCtx || 16384;
    const used = (state.sysTokens || 1400) + state.history.reduce((a, m) => a + tokOf(m.content), 0);
    if (used < ctx * 0.72) return false;
    el.note.textContent = '上下文快满了，正在自动压缩前面的对话…';
    const ok = await runCompress(COMPRESS_KEEP, true);
    if (ok) toast(`上下文快满，已自动压缩 ${state.summary.length} 字摘要（可点 ⚙ 撤销）`);
    return ok;
  }

  function undoCompress() {
    const id = state.current && state.current.id;
    if (!id) return;
    const raw = localStorage.getItem(LS_SUM_BAK + id);
    if (!raw) { toast('没有可撤销的压缩', true); return; }
    try {
      const bak = JSON.parse(raw);
      state.history = bak.history || [];
      state.summary = bak.summary || '';
      state.sumCount = bak.sumCount || 0;
      localStorage.setItem(LS_SUM + id, state.summary);
      localStorage.setItem(LS_SUMN + id, String(state.sumCount));
      localStorage.removeItem(LS_SUM_BAK + id);
      saveHistory();
      renderMessages();
      refreshMeta();
      autoSave();
      toast('已撤销压缩，历史恢复成逐条');
    } catch (e) { toast('撤销失败：' + e, true); }
  }

  /* ---------------- 帮我想（下一步说什么） ---------------- */
  let ideaBusy = false;

  async function fetchIdeas() {
    if (ideaBusy || !state.current) return;
    ideaBusy = true;
    el.ideaBtn.disabled = true;
    el.ideaBtn.textContent = '想…';
    el.ideaClose.style.display = 'none';
    el.ideaList.innerHTML = '<div class="idea-hint">正在读这段对话，想几条你可以说的…</div>';
    try {
      const r = await fetch('/api/suggest', {
        method: 'POST', headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({
          persona_id: state.current.id,
          level: state.settings.level,
          mode: state.mode,
          model: state.modelId,
          num_ctx: state.modelCtx,
          history: state.history.slice(-10),
          summary: state.summary || undefined,
          n: 4,
        }),
      });
      const d = await r.json();
      renderIdeas(d.lines || [], d.ok ? '' : (d.error || '生成失败'));
    } catch (e) {
      renderIdeas([], String(e));
    } finally {
      ideaBusy = false;
      el.ideaBtn.disabled = false;
      el.ideaBtn.textContent = '💡 帮我想';
    }
  }

  function renderIdeas(lines, err) {
    el.ideaList.innerHTML = '';
    if (err) {
      el.ideaList.innerHTML = '<div class="idea-hint">没想出来（' + esc(String(err).slice(0, 80)) + '），再点一次试试</div>';
      return;
    }
    if (!lines.length) {
      el.ideaList.innerHTML = '<div class="idea-hint">这次没给建议，再点一次</div>';
      return;
    }
    lines.forEach((t, i) => {
      const b = document.createElement('button');
      b.type = 'button';
      b.className = 'idea-item';
      b.innerHTML = '<span class="idea-n">' + (i + 1) + '</span><span class="idea-t">' + esc(t) + '</span>';
      b.onclick = () => {
        el.input.value = t;
        el.input.style.height = 'auto';
        el.input.style.height = Math.min(180, el.input.scrollHeight) + 'px';
        el.input.focus();
        el.ideaClose.style.display = '';
        toast('已填进输入栏，改完按 Enter 发送');
      };
      el.ideaList.appendChild(b);
    });
    el.ideaClose.style.display = '';
  }

  /* ---------------- 模型 / 长度 / 模式 ---------------- */
  async function fetchModels() {
    let list = [];
    let up = true;
    try {
      const r = await fetch('/api/models');
      const d = await r.json();
      list = d.models || [];
      up = d.ollama_ok !== false;
    } catch (e) {}
    state.ollamaOk = up;
    renderOllamaChip(up);
    const anyReal = list.some(m => !m.missing);
    if (!up || !anyReal) {
      el.dot.className = 'dot';
      el.foot.innerHTML = up
        ? '没有可用的模型文件<br>先运行 <b style="color:var(--text)">scripts/import-model.ps1</b>'
        : 'Ollama 服务没起来<br>点顶栏 <b style="color:var(--text)">启动服务</b>，或运行 <b style="color:var(--text)">scripts/start.ps1</b>';
    }
    state.models = list;
    const saved = localStorage.getItem(LS_MODEL);
    let pick = list.find(m => m.id === saved || m.name === saved);
    if (!pick) pick = list.find(m => m.default) || list.find(m => !m.missing) || list[0];
    if (pick) applyModel(pick);
    // 手机上名字太长会被截断，只显示模型代号（27B / 14B）
    const narrow = window.innerWidth <= 700;
    el.selModel.innerHTML = list.map(m => {
      const label = narrow ? String(m.label || '').split(' · ')[0] : m.label;
      return `<option value="${m.id}"${m.id === state.modelId ? ' selected' : ''}>${escapeHtml(label)}</option>`;
    }).join('');
  }

  function renderOllamaChip(up) {
    let btn = $('btn-ollama');
    if (up) {
      if (btn) btn.remove();
      return;
    }
    if (!btn) {
      btn = document.createElement('button');
      btn.id = 'btn-ollama';
      btn.className = 'chip-btn';
      btn.textContent = '启动服务';
      btn.title = 'Ollama 没起来：点一下就地启动，不用关页面';
      btn.style.borderColor = 'rgba(255,138,128,.55)';
      btn.style.color = '#ffb4ab';
      btn.onclick = async () => {
        const old = btn.textContent;
        btn.textContent = '启动中…';
        btn.disabled = true;
        try {
          const r = await fetch('/api/ollama/start', { method: 'POST' });
          const d = await r.json();
          if (!d.ok) { toast('启动失败：看看 logs\ollama.web.err.log', true); return; }
          await fetchModels();
          await fetchState();     // 左下角提示跟着恢复
          toast('服务已启动，模型也读到了');
          refreshMeta();
        } catch (e) {
          toast('启动失败：' + e, true);
        } finally {
          btn.textContent = old;
          btn.disabled = false;
        }
      };
      el.selModel.parentNode.insertBefore(btn, el.selModel);
    }
  }

  function applyModel(m) {
    state.modelId = m.id;
    state.modelCtx = m.num_ctx || 16384;
    localStorage.setItem(LS_MODEL, m.id);
    // 上下文档位跟着模型走，每个模型各记一个偏好
    const choices = m.ctx_choices && m.ctx_choices.length ? m.ctx_choices : [state.modelCtx];
    const saved = parseInt(localStorage.getItem('qwensese:ctx:' + m.id) || '0', 10);
    const pick = choices.indexOf(saved) >= 0 ? saved : (choices.indexOf(state.modelCtx) >= 0 ? state.modelCtx : choices[0]);
    state.ctxChoices = choices;
    state.modelCtx = pick;
    el.selCtx.innerHTML = choices.map(v =>
      `<option value="${v}"${v === pick ? ' selected' : ''}>${ctxLabel(v)}</option>`).join('');
    el.selCtx.disabled = choices.length <= 1;
    refreshMeta();
  }

  function ctxLabel(v) {
    return (v >= 1024 ? (v / 1024) + 'k' : v) + (v === 16384 && state.modelId && /27b/.test(state.modelId) ? '（会掉速）' : '');
  }

  function setLen(idx, persist = true) {
    state.lenIdx = Math.max(0, Math.min(LEN_PRESETS.length - 1, Number(idx)));
    el.selLen.value = String(state.lenIdx);
    if (persist) localStorage.setItem(LS_LEN, String(state.lenIdx));
    $('len-hint') && ($('len-hint').textContent = LEN_PRESETS[state.lenIdx].hint);
  }

  function setMode(mode, persist = true) {
    state.mode = mode === 'narrator' ? 'narrator' : 'dialogue';
    const narr = state.mode === 'narrator';
    el.modeBtn.textContent = narr ? '叙事者' : '对话';
    el.modeBtn.classList.toggle('on', narr);
    el.modeBtn.title = narr
      ? '现在是叙事者模式：第三人称讲故事、可多人物同场（点一下切回角色对话）'
      : '现在是角色对话模式：AI 扮演这张卡里的人（点一下切到叙事者）';
    if (persist && state.current) localStorage.setItem(LS_MODE + state.current.id, state.mode);
  }

  /* 超长档保底：写不够 600 字就自动接着写，续在同一段里 */
  async function continueWriting(acc, minChars, txtNode) {
    let out = acc;
    for (let pass = 0; pass < 3 && out.length < minChars; pass++) {
      if (!state.abort || !state.current) break;
      try {
        const res = await fetch('/api/chat', {
          method: 'POST',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            persona_id: state.current.id,
            level: state.settings.level,
            use_style: state.settings.style,
            extra_system: '接着上文继续往下写（不是重新开头）：从断点续写，把这一轮写满，' +
                          '总字数至少到 ' + minChars + ' 个汉字。不要重复已经写过的句子，不要总结、不要收尾。',
            temperature: state.settings.temp,
            top_p: state.settings.topP,
            repeat_penalty: state.settings.rep,
            model: state.modelId || undefined,
            num_ctx: state.modelCtx || undefined,
            num_predict: Math.max(600, LEN_PRESETS[state.lenIdx].n),
            mode: state.mode,
            summary: state.summary || undefined,
            messages: state.history,   // 最后一条是刚写的那段，模型会接着写
          }),
          signal: state.abort.signal,
        });
        if (!res.ok) break;
        const reader = res.body.getReader();
        const dec = new TextDecoder();
        let buf = '';
        let added = '';
        while (true) {
          const { value, done } = await reader.read();
          if (done) break;
          buf += dec.decode(value, { stream: true });
          let i;
          while ((i = buf.indexOf('\n')) >= 0) {
            const line = buf.slice(0, i).trim();
            buf = buf.slice(i + 1);
            if (!line) continue;
            let j;
            try { j = JSON.parse(line); } catch (e) { continue; }
            if (j.message && j.message.content) {
              added += j.message.content;
              txtNode.innerHTML = fmt(out + added) + '<span class="typing"></span>';
              scrollBottom();
            }
          }
        }
        if (!added) break;
        out += added;
        txtNode.innerHTML = fmt(out);
      } catch (e) {
        break;
      }
    }
    return out;
  }

  /* ---------------- 发送 ---------------- */
  // opts.regenerate = true：删掉最后一条角色回复，用同样的上文重新生成
  async function send(opts = {}) {
    if (state.busy || !state.current) return;
    const regen = !!opts.regenerate;

    if (regen) {
      const last = state.history[state.history.length - 1];
      if (!last || last.role !== 'assistant') { toast('没有可重新生成的回复', true); return; }
      state.history.pop();
      const nodes = el.msgs.querySelectorAll('.msg.assistant');
      const lastNode = nodes[nodes.length - 1];
      if (lastNode) lastNode.remove();
      saveHistory();
    } else {
      const text = el.input.value.trim();
      if (!text) return;
      state.history.push({ role: 'user', content: text });
      el.input.value = '';
      el.input.style.height = 'auto';
      const uNode = bubble('user', text);
      attachActs(uNode, state.history.length - 1);
      if (el.msgs.querySelector('.empty')) el.msgs.querySelector('.empty').remove();
      saveHistory();
    }
    scrollBottom();
    refreshMeta();

    // 上下文快满时先自动压缩（手动压缩入口不变）
    const compacted = await maybeAutoCompress();
    if (compacted) renderMessages();
    scrollBottom();

    const node = bubble('assistant', null, { temp: true });
    const txtNode = node.querySelector('.txt');
    txtNode.textContent = '';

    state.busy = true;
    el.send.disabled = true;
    el.stop.style.display = '';
    state.abort = new AbortController();

    const payload = {
      persona_id: state.current.id,
      level: state.settings.level,
      use_style: state.settings.style,
      extra_system: state.settings.extra || '',
      temperature: state.settings.temp,
      top_p: state.settings.topP,
      repeat_penalty: state.settings.rep,
      model: state.modelId || undefined,
      num_ctx: state.modelCtx || undefined,
      num_predict: LEN_PRESETS[state.lenIdx].n,
      min_chars: LEN_PRESETS[state.lenIdx].min || 0,
      mode: state.mode,
      summary: state.summary || undefined,
      messages: state.history.filter(m => m.role === 'user' || m.role === 'assistant'),
    };

    // 发出去之前按预算裁一次：system + 对话 + 输出预留 必须塞进 num_ctx
    {
      const ctx = state.modelCtx || 16384;
      const outTok = LEN_PRESETS[state.lenIdx].n || 700;
      const budget = ctx - outTok - Math.max(64, Math.round(ctx * 0.03));
      const sysTok = state.sysTokens || 1400;
      let total = sysTok + state.history.reduce((a, m) => a + tokOf(m.content), 0);
      let dropped = 0;
      while (total > budget && state.history.length > 5) {
        total -= tokOf(state.history[0].content);
        state.history.shift();
        dropped += 1;
      }
      if (dropped) {
        saveHistory();
        renderMessages();
        toast(`上下文满了，已放掉最早的 ${dropped} 条对话（想留住旧剧情：⚙ → 压缩上下文）`, true);
      }
    }

    let acc = '';
    let raf = null;
    const paint = () => {
      raf = null;
      txtNode.innerHTML = fmt(acc) + '<span class="typing"></span>';
      scrollBottom();
    };

    try {
      const res = await fetch('/api/chat', {
        method: 'POST',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify(payload),
        signal: state.abort.signal,
      });
      if (!res.ok) {
        let msg = 'HTTP ' + res.status;
        try { msg = (await res.json()).error || msg; } catch (e) {}
        throw new Error(msg);
      }
      const reader = res.body.getReader();
      const dec = new TextDecoder();
      let buf = '';
      let final = null;

      while (true) {
        const { value, done } = await reader.read();
        if (done) break;
        buf += dec.decode(value, { stream: true });
        let i;
        while ((i = buf.indexOf('\n')) >= 0) {
          const line = buf.slice(0, i).trim();
          buf = buf.slice(i + 1);
          if (!line) continue;
          let j;
          try { j = JSON.parse(line); } catch (e) { continue; }
          if (j.error) throw new Error(j.error);
          if (j.note) { el.note.textContent = j.note; }
          if (j.message && j.message.content) {
            acc += j.message.content;
            if (!raf) raf = requestAnimationFrame(paint);
          }
          if (j.done) final = j;
        }
      }

      if (raf) { cancelAnimationFrame(raf); raf = null; }
      txtNode.innerHTML = fmt(acc);
      scrollBottom();

      state.history.push({ role: 'assistant', content: acc });
      attachActs(node, state.history.length - 1);
      saveHistory();

      // 超长档：不到 600 字就自动续写，续在同一段气泡里
      const minChars = LEN_PRESETS[state.lenIdx].min || 0;
      if (minChars > 0 && acc.trim().length && acc.length < minChars && state.abort && !state.abort.signal.aborted) {
        el.note.textContent = `只有 ${acc.length} 字，正在自动续写到 ${minChars}+ 字…`;
        const fuller = await continueWriting(acc, minChars, txtNode);
        if (fuller.length > acc.length) {
          acc = fuller;
          state.history[state.history.length - 1] = { role: 'assistant', content: acc };
          saveHistory();
          el.note.textContent = `已续写到 ${acc.length} 字`;
        }
      }
      refreshMeta();

      if (final && final.prompt_eval_count) {
        el.ctx.title = `上一次实际 prompt：${final.prompt_eval_count} tokens（估算值对得上就说明比例没偏）`;
      }
      if (final) {
        const tps = final.eval_count && final.eval_duration
          ? (final.eval_count / (final.eval_duration / 1e9)).toFixed(1) : '—';
        el.tps.textContent = `${tps} tok/s · ${final.eval_count || 0} tokens`;
        if (final.done_reason === 'length') el.note.textContent = '被 num_predict 截断了';
      }
    } catch (e) {
      // 停止时可能还有一帧 requestAnimationFrame 排队没跑，会把手写的「已停止」覆盖成光标
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      if (e.name === 'AbortError') {
        txtNode.innerHTML = fmt(acc) + '<span style="color:var(--dim-2)">（已停止）</span>';
        if (acc) { state.history.push({ role: 'assistant', content: acc }); attachActs(node, state.history.length - 1); saveHistory(); }
      } else {
        txtNode.innerHTML = `<span style="color:var(--danger)">出错：${esc(e.message)}</span>`;
        state.history.pop();   // 回滚这条用户消息，方便重发
        saveHistory();
        toast(e.message, true);
      }
    } finally {
      if (raf) { cancelAnimationFrame(raf); raf = null; }
      state.busy = false;
      el.send.disabled = false;
      el.stop.style.display = 'none';
      state.abort = null;
      // 兜底：停止后残留的光标一律清掉（否则会一直闪）
      txtNode.querySelectorAll('.typing').forEach(n => n.remove());
      refreshMeta();   // 必须放在这里：帮我想 / 重新生成 都要等 busy 变回 false 才会出现
      // 这一轮写完了 → 自动存到本机（同一段对话原地更新）
      if (state.history.length) autoSave();
    }
  }

  /* ---------------- 角色卡编辑 ---------------- */
  const FIELDS = [
    ['名字', 'input', ''],
    ['年龄', 'input', ''],
    ['身份', 'textarea', ''],
    ['与用户的关系', 'textarea', ''],
    ['前情提要', 'textarea', '已经发生过的剧情。让模型能接着演下去的关键。'],
    ['外貌', 'textarea', ''],
    ['性格', 'textarea', ''],
    ['说话风格', 'textarea', ''],
    ['行为准则', 'textarea', ''],
    ['尺度', 'input', '必须是 0～3 的整数'],
    ['禁忌', 'textarea', ''],
    ['开场白', 'textarea', ''],
    ['示例对话', 'textarea', ''],
  ];

  async function openEditor(file, isNew, preset) {
    state.editingFile = isNew ? null : file;
    el.modalTitle.textContent = isNew ? '新建角色卡' : '编辑：' + file;
    el.modalBody.innerHTML = '';

    let fields = {};
    if (preset) {
      fields = preset;
      el.modalTitle.textContent = '从对话生成的角色卡（检查后保存）';
    } else if (!isNew) {
      const r = await fetch('/api/persona?id=' + encodeURIComponent(file));
      const d = await r.json();
      fields = d.fields || {};
    } else {
      fields = { 尺度: '2' };
    }

    FIELDS.forEach(([key, kind, hint]) => {
      const cell = document.createElement('div');
      cell.className = 'cell' + (['名字', '年龄', '尺度'].includes(key) ? '' : ' full');
      const val = fields[key] || '';
      cell.innerHTML = `<label>${key}</label>` + (kind === 'input'
        ? `<input data-k="${key}" type="text" value="${esc(val).replace(/"/g, '&quot;')}">`
        : `<textarea data-k="${key}" rows="${key === '示例对话' ? 6 : 3}">${esc(val)}</textarea>`)
        + (hint ? `<div class="hint" style="font-size:11.5px;color:var(--dim-2);margin-top:4px">${hint}</div>` : '');
      el.modalBody.appendChild(cell);
    });

    // 卡里手加的字段（【世界观】【NPC 池】【结尾格式】……）也必须能编辑，
    // 否则一点保存它们就被丢掉了。
    const known = new Set(FIELDS.map(f => f[0]));
    Object.keys(fields).filter(k => !known.has(k)).forEach(key => {
      const cell = document.createElement('div');
      cell.className = 'cell full';
      cell.innerHTML = `<label>${esc(key)} <span style="font-size:11px;color:var(--dim-2)">（手加字段）</span></label>
        <textarea data-k="${esc(key)}" rows="4">${esc(fields[key] || '')}</textarea>`;
      el.modalBody.appendChild(cell);
    });

    el.modal.classList.add('open');
  }

  async function saveEditor() {
    const fields = {};
    el.modalBody.querySelectorAll('[data-k]').forEach(n => { fields[n.dataset.k] = n.value; });
    if (!fields['名字'] || !fields['名字'].trim()) { toast('名字不能空', true); return; }
    // 记住旧开场白：如果对话第一句就是它，保存后要跟着一起换掉
    const oldOpening = ((state.card && state.card['开场白']) || '').trim();
    const body = { file: state.editingFile, fields };
    const r = await fetch('/api/persona/save', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(body),
    });
    const d = await r.json();
    if (!d.ok) { toast(d.error || '保存失败', true); return; }
    el.modal.classList.remove('open');
    toast('已写入 personas/' + d.file);
    await fetchState();
    selectPersona(d.file);

    // 开场白改过了，而且当前对话的第一句正是旧开场白 → 直接换成新的
    const newOpening = (fields['开场白'] || '').trim();
    const id = d.file;
    const hist = loadJSON(LS_HIST + id, []);
    if (newOpening && newOpening !== oldOpening && hist.length &&
        hist[0].role === 'assistant' && (hist[0].content || '').trim() === oldOpening) {
      hist[0].content = newOpening;
      localStorage.setItem(LS_HIST + id, JSON.stringify(hist));
      if (state.current && state.current.id === id) {
        state.history = hist;
        renderMessages();
      }
      toast('开场白已换，对话第一句也跟着更新了');
    }
  }

  async function deleteCard() {
    if (!state.editingFile) return;
    if (!confirm('删除这张角色卡文件？（不可撤销）')) return;
    await fetch('/api/persona/delete', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ file: state.editingFile }),
    });
    el.modal.classList.remove('open');
    await fetchState();
    if (state.personas[0]) selectPersona(state.personas[0].id);
    toast('已删除');
  }

  /* ---------------- 从对话导入 ---------------- */
  function importLog(line, cls) {
    const box = $('import-log');
    if (!box) return;
    box.style.display = 'block';
    const div = document.createElement('div');
    div.innerHTML = (cls === 'err' ? '<span style="color:var(--danger)">' : '') + esc(line) +
      (cls === 'err' ? '</span>' : '');
    box.appendChild(div);
    box.scrollTop = box.scrollHeight;
  }

  /* ---------------- 生成角色卡：对话提取 / 描述生成 → 角色卡 or 自我角色卡 ---------------- */
  const gen = { target: 'persona', mode: 'dialog', fields: {}, source: '' };

  function genSync() {
    const tg = $('gen-target'), md = $('gen-mode');
    if (tg) tg.querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('on', b.dataset.v === gen.target));
    if (md) md.querySelectorAll('.seg-btn').forEach(b => b.classList.toggle('on', b.dataset.v === gen.mode));
    const dialog = gen.mode === 'dialog';
    const whoWrap = $('gen-who-wrap');
    if (whoWrap) whoWrap.style.display = (dialog && gen.target === 'persona') ? '' : 'none';
    const nm = $('gen-nomodel-wrap');
    if (nm) nm.style.display = dialog ? '' : 'none';
    const ta = $('import-text');
    if (dialog) {
      $('gen-label').textContent = '把现成的对话粘进来（每行「名字：内容」，几百字到几万字都行）';
      ta.placeholder = '林晚：*她抬起眼* "打烊了。"\n你：我就是想坐一会儿。\n林晚：……坐吧。';
      $('gen-hint').textContent = '支持 名字：内容 / 【名字】内容 / **名字**：内容；整段没有名字开头也能分析。只写人设，不写剧情。';
    } else {
      $('gen-label').textContent = '用口语描述这个人：长相、性格、说话方式、习惯、和你的关系…';
      ta.placeholder = '例：二十七八岁的旧书店老板娘，短发，说话很慢，爱给人倒茶。对客人嘴上刻薄，其实心软。尺度偏暧昧。';
      $('gen-hint').textContent = '关键词堆也行。写清 长相 / 语气 / 关系 / 尺度，生成的卡会更准；只写人设，不写剧情和 NPC。';
    }
  }

  function openGen() {
    gen.target = 'persona';
    gen.mode = 'dialog';
    gen.fields = {};
    gen.source = '';
    $('import-text').value = '';
    $('import-log').innerHTML = '';
    $('import-log').style.display = 'none';
    $('gen-step1').style.display = 'block';
    $('gen-step2').style.display = 'none';
    $('import-run').style.display = '';
    $('gen-back').style.display = 'none';
    $('gen-confirm').style.display = 'none';
    genSync();
    $('import-modal').classList.add('open');
    setTimeout(() => $('import-text').focus(), 40);
  }

  async function genFromDialog(text, warnings) {
    const payload = {
      text,
      narrator: $('import-narrator').value || null,
      use_model: !$('import-nomodel').checked,
    };
    const res = await fetch('/api/import', {
      method: 'POST', headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify(payload),
    });
    if (!res.ok) {
      let msg = 'HTTP ' + res.status;
      try { msg = (await res.json()).error || msg; } catch (e) {}
      throw new Error(msg);
    }
    const reader = res.body.getReader();
    const dec = new TextDecoder();
    let buf = '', draft = null;
    while (true) {
      const { value, done } = await reader.read();
      if (done) break;
      buf += dec.decode(value, { stream: true });
      let i;
      while ((i = buf.indexOf('\n')) >= 0) {
        const line = buf.slice(0, i).trim();
        buf = buf.slice(i + 1);
        if (!line) continue;
        let j;
        try { j = JSON.parse(line); } catch (e) { continue; }
        if (j.stage === 'error') throw new Error(j.error || '分析失败');
        if (j.stage === 'parse') {
          importLog(`认出发言人：${(j.speakers || []).join('、') || '（无）'}　共 ${j.lines} 条`);
          if (j.role) importLog(`角色 = ${j.role}` + (j.user ? `　你 = ${j.user}` : ''));
        } else if (j.stage === 'direct') {
          importLog(`文本 ${j.chars} 字，一次直接分析`);
        } else if (j.stage === 'chunk') {
          importLog(`分段摘要 ${j.i}/${j.n} …`);
        } else if (j.stage === 'reduce') {
          importLog(`合并 ${j.chunks} 段摘要`);
        } else if (j.stage === 'synth') {
          importLog('正在合成角色卡（这一步最慢，约 10～40 秒）…');
        } else if (j.stage === 'done') {
          draft = j.draft;
          (j.warnings || []).forEach(w => warnings.push(w));
        }
      }
    }
    if (!draft) throw new Error('没有拿到结果');
    return draft;
  }

  async function genRun() {
    const text = $('import-text').value.trim();
    const btn = $('import-run');
    if (gen.mode === 'dialog' && text.length < 40) { toast('对话太短，多贴几段', true); return; }
    if (gen.mode === 'desc' && text.length < 6) { toast('描述太短，写几句话或几个关键词', true); return; }
    gen.source = text;
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '生成中…（十几秒到一分钟）';
    $('import-log').innerHTML = '';
    $('import-log').style.display = 'block';
    importLog(gen.mode === 'dialog' ? '开始解析对话…' : '开始按描述生成…');
    const warnings = [];
    try {
      let fields = {};
      if (gen.mode === 'dialog' && gen.target === 'persona') {
        fields = await genFromDialog(text, warnings);
      } else if (gen.mode === 'dialog' && gen.target === 'me') {
        const r = await fetch('/api/me/import', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, model: state.modelId || undefined, num_ctx: state.modelCtx || undefined }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || '提取失败');
        fields = d.fields || {};
      } else {
        const r = await fetch('/api/card/gen', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({
            text, target: gen.target, level: state.settings.level,
            model: state.modelId || undefined, num_ctx: state.modelCtx || undefined,
          }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || '生成失败');
        fields = d.fields || {};
      }
      importLog('生成完成，进预览页。');
      showGenPreview(fields, warnings);
    } catch (e) {
      importLog('失败：' + (e.message || e), 'err');
      toast(String(e.message || e), true);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  function genKeys() {
    return gen.target === 'me' ? ME_FIELDS.map(x => x[0]) : FIELDS.map(x => x[0]);
  }

  function showGenPreview(fields, warnings) {
    gen.fields = Object.assign({}, fields);
    const keys = genKeys();
    const filled = keys.filter(k => String(gen.fields[k] || '').trim()).length;
    $('gen-preview-info').textContent =
      `预览：${gen.target === 'me' ? '自我角色卡' : '角色卡'} · 填好 ${filled}/${keys.length} 个字段`;
    const grid = $('gen-preview');
    const LONG = ['外貌', '性格', '说话风格', '行为准则', '禁忌', '开场白', '示例对话', '其他', '喜好', '前情提要', '与用户的关系', '身份'];
    grid.innerHTML = '';
    keys.forEach(k => {
      const cell = document.createElement('div');
      cell.className = 'cell full';
      const v = String(gen.fields[k] || '');
      cell.innerHTML = `<label>${k}</label>` + (LONG.includes(k)
        ? `<textarea data-gf="${k}" rows="${k === '示例对话' ? 6 : 3}">${esc(v)}</textarea>`
        : `<input data-gf="${k}" type="text" value="${esc(v).replace(/"/g, '&quot;')}">`);
      grid.appendChild(cell);
    });
    if (warnings && warnings.length) importLog('注意：' + warnings.join('；'), 'err');
    $('gen-step1').style.display = 'none';
    $('gen-step2').style.display = 'block';
    $('import-run').style.display = 'none';
    $('gen-back').style.display = '';
    $('gen-confirm').style.display = '';
  }

  function genBack() {
    $('gen-step1').style.display = 'block';
    $('gen-step2').style.display = 'none';
    $('import-run').style.display = '';
    $('gen-back').style.display = 'none';
    $('gen-confirm').style.display = 'none';
  }

  async function genConfirm() {
    const fields = {};
    $('gen-preview').querySelectorAll('[data-gf]').forEach(n => { fields[n.dataset.gf] = n.value.trim(); });
    const name = (fields['名字'] || '').trim();
    if (!name) { toast('「名字」别空着，AI 要用它称呼', true); return; }
    const safe = name.replace(/[\\/:*?"<>|]/g, '');
    const btn = $('gen-confirm');
    const old = btn.textContent;
    btn.disabled = true;
    btn.textContent = '保存中…';
    try {
      if (gen.target === 'me') {
        const file = '我-' + safe + '.md';
        const r = await fetch('/api/me', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ fields, file, select: true }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error('保存失败');
        state.meList = d.list || state.meList;
        state.meFile = d.file || file;
        $('import-modal').classList.remove('open');
        fetchCtx();
        toast('已加入自我角色卡：' + (d.file || file));
      } else {
        const file = '角色-' + safe + '.md';
        if (state.personas.some(p => p.id === file) && !confirm('已经有一张同名角色卡，覆盖它？')) return;
        const r = await fetch('/api/persona/save', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ file, fields }),
        });
        const d = await r.json();
        if (!d.ok) throw new Error(d.error || '保存失败');
        await fetchState();
        const p = state.personas.find(x => x.id === (d.file || file));
        if (p) selectPersona(p.id);
        $('import-modal').classList.remove('open');
        toast('已加入角色卡：' + (d.file || file));
      }
    } catch (e) {
      toast(String(e.message || e), true);
    } finally {
      btn.disabled = false;
      btn.textContent = old;
    }
  }

  /* ---------------- 事件绑定 ---------------- */
  function bind() {
    el.send.onclick = send;
    el.stop.onclick = () => { if (state.abort) state.abort.abort(); };

    el.input.oninput = () => {
      el.input.style.height = 'auto';
      el.input.style.height = Math.min(180, el.input.scrollHeight) + 'px';
    };
    el.input.onkeydown = (e) => {
      if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); send(); }
    };

    if (el.levelQuick) el.levelQuick.oninput = () => setLevel(el.levelQuick.value);
    el.selLevel.onchange = () => {
      setLevel(el.selLevel.value);
      toast('尺度：' + LEVEL_LABEL[Number(el.selLevel.value)]);
    };
    $('lv').oninput = () => setLevel($('lv').value);

    $('btn-drawer').onclick = () => { el.drawer.classList.add('open'); el.scrim.style.display = 'block'; };
    const closeDrawer = () => { el.drawer.classList.remove('open'); el.scrim.style.display = 'none'; };
    $('drawer-close').onclick = closeDrawer;
    el.scrim.onclick = closeDrawer;

    el.btnMe.onclick = () => openMe();
    $('me-close').onclick = () => el.meModal.classList.remove('open');
    $('me-cancel').onclick = () => el.meModal.classList.remove('open');
    $('me-save').onclick = () => saveMe();
    if (el.meDelete) el.meDelete.onclick = () => deleteMeCard();
    if (el.undo) el.undo.onclick = () => undoRound();

    // 消息上的「回溯到这儿 / 删这轮」
    el.msgs.addEventListener('click', (ev) => {
      const b = ev.target.closest('.act');
      if (!b) return;
      const box = b.closest('.msg');
      const idx = Number(box && box.dataset.idx);
      if (!box || Number.isNaN(idx)) return;
      if (b.dataset.act === 'del') removeRound(idx);
      else cutFrom(idx);
    });
    $('me-import-btn').onclick = () => {
      const box = $('me-import-box');
      box.style.display = box.style.display === 'none' ? 'block' : 'none';
    };
    $('me-import-run').onclick = async () => {
      const text = $('me-import-text').value.trim();
      const st = $('me-import-status');
      if (text.length < 40) { toast('贴的对话太短了，多贴几段', true); return; }
      const btn = $('me-import-run');
      btn.disabled = true;
      const oldTxt = btn.textContent;
      btn.textContent = '分析中…（约 10～30 秒）';
      st.textContent = '';
      try {
        const r = await fetch('/api/me/import', {
          method: 'POST', headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ text, model: state.modelId || undefined, num_ctx: state.modelCtx || undefined }),
        });
        const d = await r.json();
        if (!d.ok) { st.textContent = d.error || '提取失败'; return; }
        let filled = 0;
        Object.keys(d.fields || {}).forEach(k => {
          const node = document.querySelector('#me-body [data-mk="' + k + '"]');
          if (node) { node.value = d.fields[k]; filled++; }
        });
        st.textContent = `已填好 ${filled} 个字段，检查一下再点「保存」`;
        toast('提取完成，检查后保存');
      } catch (e) {
        st.textContent = '提取失败：' + e;
      } finally {
        btn.disabled = false;
        btn.textContent = oldTxt;
      }
    };
    el.btnCompress.onclick = () => compressContext();
    el.btnUnsum.onclick = () => undoCompress();

    el.ideaBtn.onclick = () => fetchIdeas();
    el.ideaClose.onclick = () => { el.ideaList.innerHTML = ''; el.ideaClose.style.display = 'none'; };

    $('btn-reopen').onclick = () => {
      if (!state.current) return;
      const opening = ((state.card && state.card['开场白']) || '').trim();
      if (!opening) { toast('这张卡没有开场白，先点 ✎ 填一个', true); return; }
      if (state.history.length && !confirm('会用开场白重开：当前这段对话被清空（角色卡不动），继续？')) return;
      state.history = [{ role: 'assistant', content: opening }];
      saveHistory();
      renderMessages();
      refreshMeta();
      closeDrawer();
      toast('已用开场白重开');
    };

    $('btn-import').onclick = () => openGen();
    $('import-close').onclick = $('import-cancel').onclick = () => $('import-modal').classList.remove('open');
    $('import-run').onclick = () => genRun();
    $('gen-confirm').onclick = () => genConfirm();
    $('gen-back').onclick = () => genBack();
    ['gen-target', 'gen-mode'].forEach(id => {
      const box = $(id);
      if (box) box.addEventListener('click', (ev) => {
        const b = ev.target.closest('.seg-btn');
        if (!b) return;
        if (id === 'gen-target') gen.target = b.dataset.v;
        else gen.mode = b.dataset.v;
        genSync();
      });
    });

    const asum = $('auto-sum-chk');
    if (asum) {
      asum.checked = state.autoSum;
      asum.onchange = () => {
        state.autoSum = asum.checked;
        localStorage.setItem('qwensese:autosum', state.autoSum ? '1' : '0');
        toast(state.autoSum ? '已开启自动压缩' : '已关闭自动压缩（手动按钮不受影响）');
      };
    }

    // 模型 / 长度 / 模式 / 重新生成
    el.selModel.onchange = () => {
      const m = state.models.find(x => x.id === el.selModel.value) || {};
      if (m.offline || (!state.ollamaOk && m.missing)) {
      toast('Ollama 服务没起来，点顶栏「启动服务」或运行 scripts/start.ps1', true);
      return;
    }
    if (m.missing) { toast('这个模型还没安装，先运行 scripts/import-model.ps1', true); return; }
      applyModel(m);
      toast(`换成 ${m.label || m.id}（上下文 ${m.num_ctx}）`);
    };
    el.selLen.onchange = () => {
      setLen(el.selLen.value);
      toast(`回复长度：${LEN_PRESETS[state.lenIdx].k}（最多 ${LEN_PRESETS[state.lenIdx].n} token）`);
    };
    el.selCtx.onchange = () => {
      const v = parseInt(el.selCtx.value, 10);
      state.modelCtx = v;
      localStorage.setItem('qwensese:ctx:' + state.modelId, String(v));
      refreshMeta();
      const slow = /27b/.test(state.modelId) && v > 12288;
      toast(slow
        ? `上下文 ${v} —— 27B 超过 12k 会掉到约 1/3 速度，只建议偶尔用`
        : `上下文 ${v}（下一条回复生效）`);
    };
    el.modeBtn.onclick = () => {
      setMode(state.mode === 'narrator' ? 'dialogue' : 'narrator');
      toast(state.mode === 'narrator'
        ? '叙事者模式：它现在是讲故事的人，可以多人同场'
        : '角色对话模式：它扮演这张卡里的人');
    };
    el.regen.onclick = () => send({ regenerate: true });

    // 对话存档
    el.btnSaves.onclick = openSaves;
    $('saves-close').onclick = () => el.savesModal.classList.remove('open');
    $('saves-cancel').onclick = () => el.savesModal.classList.remove('open');
    $('save-now').onclick = () => saveCurrent(el.saveName.value);
    if (el.autoSaveChk) {
      el.autoSaveChk.checked = state.saveAuto;
      el.autoSaveChk.onchange = () => {
        state.saveAuto = el.autoSaveChk.checked;
        localStorage.setItem('qwensese:autosave', state.saveAuto ? '1' : '0');
        toast(state.saveAuto ? '已开启每轮自动保存' : '已关闭自动保存（手动保存不受影响）');
        if (state.saveAuto) autoSave(false);
      };
    }
    el.savesList.addEventListener('click', ev => {
      const t = ev.target.closest('button');
      if (!t) return;
      if (t.dataset.load) loadSave(t.dataset.load);
      else if (t.dataset.cover) {
        const s = savesCache.find(x => x.id === t.dataset.cover);
        saveCurrent(s ? s.name : '', t.dataset.cover);
      } else if (t.dataset.del) deleteSave(t.dataset.del);
    });

    // 「从剪贴板粘贴」：先复制对话，再点这里，省掉手动 Ctrl+V
    $('import-paste').onclick = async () => {
      const ta = $('import-text');
      if (!navigator.clipboard || !navigator.clipboard.readText) {
        toast('这个浏览器不支持读剪贴板，直接点输入框按 Ctrl+V', true);
        ta.focus();
        return;
      }
      try {
        const txt = await navigator.clipboard.readText();
        if (!txt || !txt.trim()) {
          toast('剪贴板是空的', true);
          ta.focus();
          return;
        }
        ta.value = ta.value.trim() ? ta.value.trim() + '\n' + txt.trim() : txt.trim();
        const n = ta.value.length;
        importLog(`已粘贴，当前共 ${n} 字。点「开始分析」。`);
        toast(`已粘贴 ${n} 字`);
      } catch (e) {
        toast('浏览器拒绝了读剪贴板（需要点一次"允许"），直接按 Ctrl+V 也行', true);
        ta.focus();
      }
    };

    $('btn-edit').onclick = () => { if (state.current) openEditor(state.current.id, false); };
    $('btn-new-card').onclick = () => openEditor(null, true);
    $('btn-clear').onclick = () => {
      if (!state.current) return;
      if (!confirm('清空与「' + state.current.name + '」的全部对话记录？\n（历史对话里存过的还在，可以接着聊）')) return;
      state.history = [];
      state.summary = '';
      state.sumCount = 0;
      state.saveId = '';
      const id = state.current.id;
      localStorage.removeItem(LS_SAVE + id);
      localStorage.removeItem(LS_SUM + id);
      localStorage.removeItem(LS_SUMN + id);
      saveHistory();
      renderMessages();
      refreshMeta();
      el.tps.textContent = '—';
      toast('已清空，下次发消息会存成一段新的对话');
    };

    $('modal-close').onclick = $('modal-cancel').onclick = () => el.modal.classList.remove('open');
    $('modal-save').onclick = saveEditor;
    $('modal-delete').onclick = deleteCard;

    bindRange('temp', 'temp', 'temp-label', v => v.toFixed(2));
    bindRange('topp', 'topP', 'topp-label', v => v.toFixed(2));
    bindRange('rep', 'rep', 'rep-label', v => v.toFixed(2));

    $('use-style').onchange = (e) => { state.settings.style = e.target.checked; saveSettings(); };
    $('extra').oninput = (e) => { state.settings.extra = e.target.value; saveSettings(); };
  }

  /* ---------------- 窄屏适配 ---------------- */
  function adaptMobile() {
    if (!el.input) return;
    const narrow = window.innerWidth <= 700;
    const want = narrow ? '说点什么…' : '说点什么…（Enter 发送，Shift+Enter 换行）';
    if (el.input.placeholder !== want) el.input.placeholder = want;
  }
  window.addEventListener('resize', adaptMobile);

  /* ---------------- 启动 ---------------- */
  async function boot() {
    state.settings = Object.assign({ level: null, temp: 0.8, topP: 0.92, rep: 1.05, style: true, extra: '' },
                                   loadJSON(LS_SET, {}));
    $('temp').value = state.settings.temp; $('temp-label').textContent = state.settings.temp.toFixed(2);
    $('topp').value = state.settings.topP; $('topp-label').textContent = state.settings.topP.toFixed(2);
    $('rep').value = state.settings.rep; $('rep-label').textContent = state.settings.rep.toFixed(2);
    $('use-style').checked = state.settings.style !== false;
    $('extra').value = state.settings.extra || '';

    state.saveAuto = localStorage.getItem('qwensese:autosave') !== '0';
    state.autoSum = localStorage.getItem('qwensese:autosum') !== '0';
    bind();
    adaptMobile();
    try {
      await fetchState();
    } catch (e) {
      toast('连不上本地服务', true);
      return;
    }
    setLen(localStorage.getItem(LS_LEN) || 1, false);
    await fetchModels();
    const last = localStorage.getItem(LS_CUR);
    const pick = state.personas.find(p => p.id === last) || state.personas[0];
    if (pick) selectPersona(pick.id);

    // 自测入口：?demo=1 自动发一句；?demo=canned 只加载一段示例对话（不发请求）
    const q = new URLSearchParams(location.search).get('demo');
    if (q === 'canned') {
      state.history = [
        { role: 'user', content: '（推门进来，铜铃响了一声）今天不买书，就是想坐一会儿。' },
        { role: 'assistant', content: '*她正把最后一摞精装书塞进防尘袋，听见动静，手停在半空，没立刻回头。*\n\n“嗯。”\n\n*过了两秒，她才转过身，看见是你，肩膀微微松了一下。她把那本书随手扔在柜台角落，发出沉闷的一声响。*\n\n“坐吧。”\n\n*她没去里间拿杯子，只是从柜台后面绕出来，顺手把身后的玻璃门拉上，锁扣“咔哒”一声咬合。*\n\n“不买书……” *她的手指无意识地划过那一排排书脊，声音很轻。* “那这一周的茶钱，算你请我的。桂花乌龙凉了，但还没坏。”\n\n*她忽然停下动作，侧过头，目光在你脸上停留了一瞬，又很快移开。* “外面风大吗？”' },
        { role: 'user', content: '（把门带上）风挺大的。' },
        { role: 'assistant', content: '*门合拢的瞬间，屋里那层薄薄的暖意像是被留了下来。她走回柜台后面，提起那把粗陶壶，倒水的声音在安静里显得很响。*\n\n“那就别急着走。”\n\n*杯子推到你面前，热气糊上来，桂花的甜味被泡开了。她没坐，靠在柜台边上，两只手抄在袖子里，看着你。*\n\n“外面风大，回去也是一个人。” *她顿了一下，像是觉得自己说得太多，补了一句。* “茶还有半壶。”' },
      ];
      renderMessages();
      refreshMeta();
    } else if (q === 'import') {
      $('import-modal').classList.add('open');
      if (!$('import-text').value) {
        $('import-text').value = [
          '林晚：*她从梯子上低头看下来，手里还捏着一本没归位的诗集。* “……九点四十五。按规矩，算打烊了。”',
          '你：那我明天再来。',
          '林晚：*她把书塞进架子的缝隙，慢慢下了梯子。* “明天也这个点？”',
          '你：不一定。看心情。',
          '林晚：“看心情。” *她重复了一遍。* “行啊。那我就不锁门了。”',
          '你：（笑）不怕丢东西？',
          '林晚：“这店里最值钱的是那壶桂花乌龙，你喝过三次，也没见你搬走。”',
          '你：记这么清楚？',
          '林晚：“做生意的，记性不好早饿死了。” *她把杯子推到你面前。* “凉的。不嫌弃就喝。”',
        ].join('\n');
      }
    } else if (q) {
      setTimeout(() => {
        el.input.value = (q === '1' || q === '')
          ? '（推门进来，铜铃响了一声）今天不买书，就是想坐一会儿。'
          : q;
        send();
      }, 600);
    }
  }

  boot();
})();

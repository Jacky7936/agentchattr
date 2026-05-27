/**
 * cockpit.js — Mission Cockpit shell (Slice 1)
 *
 * Adds an opt-in "Cockpit" view that hides the existing chat surface
 * (#content-area) and shows a STANDING BY shell + a live transcript
 * mirroring the active channel's messages.
 *
 * No mission data model yet (Slice 2). No briefing / intervention / delivery
 * flows yet (Slices 2-4). This slice exists to land the visual system.
 *
 * Toggle: topbar button (#cockpit-toggle) or URL `?cockpit=1`.
 * Persisted in localStorage under `agentchattr-cockpit`.
 *
 * Safety: all DOM construction uses createElement + textContent.
 * Never inject unsanitised strings via the DOM's direct-write APIs.
 */

(function () {
  const STORAGE_KEY = 'agentchattr-cockpit';

  function isCockpitOn() {
    return document.body.classList.contains('cockpit-active');
  }

  function applyCockpitStateBase(on) {
    document.body.classList.toggle('cockpit-active', !!on);
    const btn = document.getElementById('cockpit-toggle');
    if (btn) {
      btn.classList.toggle('active', !!on);
      btn.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    try {
      localStorage.setItem(STORAGE_KEY, on ? '1' : '0');
    } catch (e) { /* private mode etc. */ }
  }

  // Public toggle (wired from the topbar button onclick).
  // Task 8 wraps this to add transcript backfill on activation.
  let applyCockpitState = applyCockpitStateBase;

  function toggleCockpitMode() {
    applyCockpitState(!isCockpitOn());
  }
  window.toggleCockpitMode = toggleCockpitMode;

  // Initial state: URL param wins, then localStorage, then default off.
  function initState() {
    try {
      const params = new URLSearchParams(location.search);
      if (params.has('cockpit')) {
        applyCockpitState(params.get('cockpit') === '1');
        return;
      }
    } catch (e) { /* old browsers */ }
    try {
      applyCockpitState(localStorage.getItem(STORAGE_KEY) === '1');
    } catch (e) {
      applyCockpitState(false);
    }
  }

  // Defer initState so any later-loaded module (e.g. Task 8) has a chance
  // to call setApplyCockpitState before auto-activation consumes the base.
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initState);
  } else {
    Promise.resolve().then(initState);
  }

  // Expose for Task 8 to wrap.
  window.__cockpit = {
    setApplyCockpitState(fn) {
      if (typeof fn !== 'function') {
        console.error('[cockpit] setApplyCockpitState requires a function, got', typeof fn);
        return;
      }
      applyCockpitState = fn;
    },
    getApplyCockpitState() { return applyCockpitState; },
    isCockpitOn,
  };
})();

/* Transcript renderer — appended in Task 8.
 * Reads live messages via Hub.on('message', ...) and renders them
 * into #cockpit-messages using safe DOM APIs only.
 */
(function () {
  const cockpit = window.__cockpit;
  if (!cockpit) {
    console.error('[cockpit] base module not loaded; transcript disabled');
    return;
  }

  function activeChannel() {
    try { if (typeof window.activeChannel === 'string') return window.activeChannel; } catch (e) {}
    try { return localStorage.getItem('agentchattr-channel') || 'general'; } catch (e) { return 'general'; }
  }

  function agentColor(sender) {
    try {
      const cfg = window.baseColors || window.agentConfig || {};
      const entry = cfg[sender] || cfg[(sender || '').toLowerCase()];
      if (entry && entry.color) return entry.color;
    } catch (e) {}
    return '';
  }

  function renderCockpitMessage(msg) {
    if (!msg || msg.type === 'join' || msg.type === 'leave') return;
    const container = document.getElementById('cockpit-messages');
    if (!container) return;

    const el = document.createElement('div');
    el.className = 'cockpit-msg';
    if (msg.id) el.dataset.id = String(msg.id);

    const meta = document.createElement('div');
    meta.className = 'cockpit-msg-meta';

    const who = document.createElement('span');
    who.className = 'who';
    who.textContent = msg.sender || '';
    const color = agentColor(msg.sender);
    if (color) who.style.color = color;
    meta.appendChild(who);

    const when = document.createElement('span');
    when.className = 'when';
    when.textContent = msg.time || '';
    meta.appendChild(when);

    const body = document.createElement('div');
    body.className = 'cockpit-msg-body';
    body.textContent = msg.text || '';

    el.appendChild(meta);
    el.appendChild(body);
    container.appendChild(el);
    container.scrollTop = container.scrollHeight;
  }

  function clearCockpitMessages() {
    const container = document.getElementById('cockpit-messages');
    if (container) {
      while (container.firstChild) container.removeChild(container.firstChild);
    }
  }

  function updateCockpitChannelLabel() {
    const label = document.getElementById('cockpit-channel-label');
    if (label) label.textContent = '#' + activeChannel();
  }

  function onHubMessage(event) {
    if (!cockpit.isCockpitOn()) return;
    const msg = event && event.data;
    if (!msg) return;
    const ch = msg.channel || 'general';
    if (ch !== activeChannel()) return;
    renderCockpitMessage(msg);
  }

  try {
    if (window.Hub && typeof window.Hub.on === 'function') {
      window.Hub.on('message', onHubMessage);
    }
  } catch (e) {
    console.error('[cockpit] failed to subscribe to Hub', e);
  }

  // Wrap applyCockpitState so backfill happens on each activation.
  const baseApply = cockpit.getApplyCockpitState();
  cockpit.setApplyCockpitState(function (on) {
    baseApply(on);
    if (!on) return;
    clearCockpitMessages();
    updateCockpitChannelLabel();
    // Backfill: copy whatever's currently in #messages into the cockpit
    // transcript so a fresh activation isn't empty.
    try {
      document.querySelectorAll('#messages .message').forEach(function (node) {
        // Filter by data-channel so backfill only mirrors the current channel
        if ((node.dataset.channel || 'general') !== activeChannel()) return;
        const senderEl = node.querySelector('.msg-sender');
        const textEl = node.querySelector('.msg-text');
        const timeEl = node.querySelector('.msg-time');
        if (!senderEl || !textEl) return;
        renderCockpitMessage({
          id: node.dataset.id || '',
          // Use dataset.sender (raw key) so agentColor lookup works.
          // Fall back to textContent (display name) for messages without dataset.sender.
          sender: senderEl.dataset.sender || senderEl.textContent || '',
          text: textEl.textContent || '',
          time: timeEl ? (timeEl.textContent || '') : '',
          channel: activeChannel(),
        });
      });
    } catch (e) { /* tolerant */ }
  });
})();

/* Briefing flow — appended in Slice 2.
 * Switches the cockpit head into a form, gathers brief, POSTs /api/missions,
 * then resets the form and switches transcript to the new mission channel.
 * All DOM built with createElement + textContent; no direct-write APIs.
 */
(function () {
  const cockpit = window.__cockpit;
  if (!cockpit) return;

  function knownAgents() {
    const cfg = window.agentConfig || window.baseColors || {};
    return Object.keys(cfg)
      .filter((k) => k && k.toLowerCase() !== 'user' && k.toLowerCase() !== 'system')
      .sort();
  }

  function hexToRgba(hex, alpha) {
    hex = (hex || '').replace(/^#/, '');
    if (hex.length === 3) hex = hex.split('').map(function (c) { return c + c; }).join('');
    if (hex.length !== 6) return '';
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return '';
    return 'rgba(' + r + ', ' + g + ', ' + b + ', ' + alpha + ')';
  }

  function applyAgentAccents(el, agentName) {
    const cfg = window.agentConfig || window.baseColors || {};
    const entry = cfg[agentName] || cfg[(agentName || '').toLowerCase()];
    if (!entry || !entry.color) return;
    const color = entry.color;
    el.style.setProperty('--accent', color);
    const glow = hexToRgba(color, 0.40);
    if (glow) el.style.setProperty('--accent-glow', glow);
    const tint = hexToRgba(color, 0.06);
    if (tint) el.style.setProperty('--accent-tint', tint);
  }

  function buildCrewChip(name) {
    const chip = document.createElement('div');
    chip.className = 'agent-chip';
    chip.dataset.agent = name.toLowerCase();
    chip.dataset.on = 'false';
    applyAgentAccents(chip, name);
    chip.addEventListener('click', function (e) {
      if (e.target && e.target.classList && e.target.classList.contains('role-input')) return;
      chip.dataset.on = chip.dataset.on === 'true' ? 'false' : 'true';
      updateRecap();
    });

    const head = document.createElement('div');
    head.className = 'agent-chip-head';

    const nm = document.createElement('span');
    nm.className = 'agent-chip-name';
    nm.textContent = name;
    head.appendChild(nm);

    const on = document.createElement('span');
    on.className = 'agent-chip-on';
    head.appendChild(on);

    chip.appendChild(head);

    const role = document.createElement('input');
    role.className = 'role-input';
    role.type = 'text';
    role.placeholder = 'role (e.g. builder)';
    chip.appendChild(role);

    return chip;
  }

  function rebuildCrewChips() {
    const container = document.getElementById('cockpit-briefing-crew');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);
    knownAgents().forEach(function (name) {
      container.appendChild(buildCrewChip(name));
    });
  }

  function buildDeliverableRow(text, required) {
    const row = document.createElement('div');
    row.className = 'deliv-item';

    const check = document.createElement('span');
    check.className = 'deliv-check' + (required ? ' on' : '');
    check.addEventListener('click', function () {
      check.classList.toggle('on');
    });
    row.appendChild(check);

    const input = document.createElement('input');
    input.className = 'deliv-text';
    input.type = 'text';
    input.value = text || '';
    input.placeholder = '一條驗收條件';
    row.appendChild(input);

    return row;
  }

  function cockpitAddDeliverable() {
    const list = document.getElementById('cockpit-briefing-deliverables');
    if (!list) return;
    list.appendChild(buildDeliverableRow('', false));
    updateRecap();
  }
  window.cockpitAddDeliverable = cockpitAddDeliverable;

  function rebuildDeliverables() {
    const list = document.getElementById('cockpit-briefing-deliverables');
    if (!list) return;
    while (list.firstChild) list.removeChild(list.firstChild);
    list.appendChild(buildDeliverableRow('Migration plan', true));
    list.appendChild(buildDeliverableRow('Code diff', true));
    list.appendChild(buildDeliverableRow('Test report', true));
  }

  function gatherBrief() {
    const title = (document.getElementById('cockpit-briefing-title') || {}).value || '';
    const objective = (document.getElementById('cockpit-briefing-objective') || {}).value || '';
    const crew = [];
    document.querySelectorAll('#cockpit-briefing-crew .agent-chip').forEach(function (chip) {
      if (chip.dataset.on !== 'true') return;
      const name = chip.dataset.agent || '';
      const roleEl = chip.querySelector('.role-input');
      const role = roleEl ? (roleEl.value || '') : '';
      if (name) crew.push({ agent: name, role: role });
    });
    const deliverables = [];
    document.querySelectorAll('#cockpit-briefing-deliverables .deliv-item').forEach(function (row) {
      const txt = row.querySelector('.deliv-text');
      const chk = row.querySelector('.deliv-check');
      if (!txt || !txt.value.trim()) return;
      deliverables.push({
        text: txt.value.trim(),
        required: chk ? chk.classList.contains('on') : false,
      });
    });
    const eta = parseInt((document.getElementById('cockpit-briefing-eta') || {}).value, 10) || 0;
    const hops = parseInt((document.getElementById('cockpit-briefing-hops') || {}).value, 10) || 0;
    const pause = parseInt((document.getElementById('cockpit-briefing-pause') || {}).value, 10) || 0;
    return {
      title: title.trim(),
      objective: objective.trim(),
      crew: crew,
      reviewer: '',
      deliverables: deliverables,
      eta_minutes: eta,
      hop_budget: hops,
      auto_pause_blockers: pause,
    };
  }

  function updateRecap() {
    const brief = gatherBrief();
    const recap = document.getElementById('cockpit-briefing-recap');
    const btn = document.getElementById('cockpit-launch-btn');
    if (!recap || !btn) return;
    const ready = brief.title && brief.crew.length > 0;
    while (recap.firstChild) recap.removeChild(recap.firstChild);
    const status = document.createElement('span');
    status.textContent = ready ? 'READY ' : 'NEEDS ';
    recap.appendChild(status);
    const crewSpan = document.createElement('b');
    crewSpan.textContent = brief.crew.length + (brief.crew.length === 1 ? ' agent' : ' agents');
    recap.appendChild(crewSpan);
    recap.appendChild(document.createTextNode(brief.title ? ' · titled' : ' · title missing'));
    btn.disabled = !ready;
  }

  function enterCockpitBriefing() {
    document.body.classList.add('cockpit-briefing');
    rebuildCrewChips();
    rebuildDeliverables();
    updateRecap();
    const titleEl = document.getElementById('cockpit-briefing-title');
    if (titleEl) titleEl.focus();
    ['input', 'change'].forEach(function (evt) {
      const form = document.querySelector('.cockpit-briefing-form');
      if (form && !form.dataset.bound) {
        form.addEventListener(evt, updateRecap);
        form.dataset.bound = '1';
      }
    });
  }
  window.enterCockpitBriefing = enterCockpitBriefing;

  function exitCockpitBriefing() {
    document.body.classList.remove('cockpit-briefing');
  }
  window.exitCockpitBriefing = exitCockpitBriefing;

  async function cockpitLaunchMission() {
    const btn = document.getElementById('cockpit-launch-btn');
    if (btn) btn.disabled = true;
    const brief = gatherBrief();
    try {
      const token = window.__SESSION_TOKEN__ || '';
      const r = await fetch('/api/missions', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-session-token': token,
        },
        body: JSON.stringify(brief),
      });
      if (!r.ok) {
        let detail = '';
        try { detail = (await r.json()).detail || ''; } catch (e) {}
        console.error('[cockpit] launch failed', r.status, detail);
        const recap = document.getElementById('cockpit-briefing-recap');
        if (recap) {
          while (recap.firstChild) recap.removeChild(recap.firstChild);
          const err = document.createElement('span');
          err.textContent = 'Launch failed (' + r.status + '): ' + (detail || 'check console');
          err.style.color = 'var(--sem-stop)';
          recap.appendChild(err);
        }
        if (btn) btn.disabled = false;
        return;
      }
      const mission = await r.json();
      try {
        if (typeof window.switchChannel === 'function') {
          window.switchChannel(mission.transcript_channel_id);
        } else if (window.Store && typeof window.Store.set === 'function') {
          window.Store.set('activeChannel', mission.transcript_channel_id);
        }
      } catch (e) {}
      exitCockpitBriefing();
    } catch (e) {
      console.error('[cockpit] launch threw', e);
      if (btn) btn.disabled = false;
    }
  }
  window.cockpitLaunchMission = cockpitLaunchMission;
})();

/* Active mission flow — appended in Slice 3.
 * Subscribes to Hub 'mission' events; when a mission becomes active in the
 * current channel, replaces STANDING BY with a mission head + agent tiles.
 * Each tile exposes Freeze / Redirect / Stop actions that POST to
 * /api/missions/{id}/intervene.
 * Safe DOM only: createElement + textContent.
 */
(function () {
  const cockpit = window.__cockpit;
  if (!cockpit) return;

  const activeMissions = {};

  function currentChannel() {
    try { if (typeof window.activeChannel === 'string') return window.activeChannel; } catch (e) {}
    try { return localStorage.getItem('agentchattr-channel') || 'general'; } catch (e) { return 'general'; }
  }

  function currentMission() {
    return activeMissions[currentChannel()] || null;
  }

  function setActiveMission(mission) {
    if (!mission || !mission.transcript_channel_id) return;
    activeMissions[mission.transcript_channel_id] = mission;
    if (mission.transcript_channel_id === currentChannel()) renderActive();
  }

  function clearActiveIfMatch(missionId) {
    for (const ch in activeMissions) {
      if (activeMissions[ch] && activeMissions[ch].id === missionId) {
        delete activeMissions[ch];
      }
    }
    if (!currentMission()) {
      document.body.classList.remove('cockpit-active-mission');
    }
  }

  function renderActive() {
    const mission = currentMission();
    if (!mission || mission.status !== 'active') {
      document.body.classList.remove('cockpit-active-mission');
      return;
    }
    document.body.classList.add('cockpit-active-mission');

    const titleEl = document.getElementById('cockpit-active-title');
    if (titleEl) titleEl.textContent = mission.title || '';

    const objEl = document.getElementById('cockpit-active-objective');
    if (objEl) objEl.textContent = mission.objective || '';

    const etaEl = document.getElementById('cockpit-active-eta');
    if (etaEl) etaEl.textContent = (mission.eta_minutes ? mission.eta_minutes + 'm' : '—');

    const crewEl = document.getElementById('cockpit-active-crew');
    if (crewEl) crewEl.textContent = String((mission.crew || []).length);

    renderTiles(mission);
  }

  function renderTiles(mission) {
    const container = document.getElementById('cockpit-agent-tiles');
    if (!container) return;
    while (container.firstChild) container.removeChild(container.firstChild);
    (mission.crew || []).forEach(function (member) {
      container.appendChild(buildAgentTile(mission, member));
    });
  }

  function hexToRgba(hex, alpha) {
    hex = (hex || '').replace(/^#/, '');
    if (hex.length === 3) hex = hex.split('').map(function (c) { return c + c; }).join('');
    if (hex.length !== 6) return '';
    const r = parseInt(hex.slice(0, 2), 16);
    const g = parseInt(hex.slice(2, 4), 16);
    const b = parseInt(hex.slice(4, 6), 16);
    if (isNaN(r) || isNaN(g) || isNaN(b)) return '';
    return 'rgba(' + r + ', ' + g + ', ' + b + ', ' + alpha + ')';
  }

  function applyAgentAccents(el, agentName) {
    const cfg = window.agentConfig || window.baseColors || {};
    const entry = cfg[agentName] || cfg[(agentName || '').toLowerCase()];
    if (!entry || !entry.color) return;
    const color = entry.color;
    el.style.setProperty('--accent', color);
    const glow = hexToRgba(color, 0.40);
    if (glow) el.style.setProperty('--accent-glow', glow);
    const tint = hexToRgba(color, 0.06);
    if (tint) el.style.setProperty('--accent-tint', tint);
  }

  function buildAgentTile(mission, member) {
    const agentName = (member.agent || '').toLowerCase();
    const tile = document.createElement('div');
    tile.className = 'agent-tile';
    tile.dataset.agent = agentName;
    applyAgentAccents(tile, agentName);

    const row1 = document.createElement('div');
    row1.className = 'agent-row1';

    const name = document.createElement('span');
    name.className = 'agent-name';
    name.textContent = agentName;
    if (member.role) {
      const role = document.createElement('span');
      role.className = 'role';
      role.textContent = member.role;
      name.appendChild(role);
    }
    row1.appendChild(name);

    const pill = document.createElement('span');
    pill.className = 'pill go';
    pill.style.padding = '2px 8px';
    pill.style.fontSize = '9px';
    pill.textContent = 'Active';
    row1.appendChild(pill);

    tile.appendChild(row1);

    const task = document.createElement('p');
    task.className = 'agent-task';
    task.textContent = '';
    tile.appendChild(task);

    const foot = document.createElement('div');
    foot.className = 'agent-foot';
    const lhs = document.createElement('span');
    lhs.textContent = '';
    foot.appendChild(lhs);

    const actions = document.createElement('div');
    actions.className = 'agent-actions';

    const fBtn = document.createElement('button');
    fBtn.type = 'button';
    fBtn.className = 'agent-action freeze';
    fBtn.title = 'Freeze';
    fBtn.textContent = 'F';
    fBtn.addEventListener('click', function () {
      sendIntervention(mission.id, "freeze", agentName, '');
    });
    actions.appendChild(fBtn);

    const rBtn = document.createElement('button');
    rBtn.type = 'button';
    rBtn.className = 'agent-action redirect';
    rBtn.title = 'Redirect';
    rBtn.textContent = 'R';
    rBtn.addEventListener('click', function () {
      const panel = tile.querySelector('.redirect-panel');
      if (panel) panel.classList.toggle('open');
    });
    actions.appendChild(rBtn);

    const xBtn = document.createElement('button');
    xBtn.type = 'button';
    xBtn.className = 'agent-action stop';
    xBtn.title = 'Stop';
    xBtn.textContent = '×';
    xBtn.addEventListener('click', function () {
      if (!window.confirm('Stop ' + agentName + ' on this mission?')) return;
      sendIntervention(mission.id, "stop", agentName, '');
    });
    actions.appendChild(xBtn);

    foot.appendChild(actions);
    tile.appendChild(foot);

    const panel = document.createElement('div');
    panel.className = 'redirect-panel';

    const ta = document.createElement('textarea');
    ta.placeholder = '新的指令…';
    panel.appendChild(ta);

    const panelFoot = document.createElement('div');
    panelFoot.className = 'redirect-foot';
    const cancel = document.createElement('button');
    cancel.type = 'button';
    cancel.textContent = 'Cancel';
    cancel.addEventListener('click', function () { panel.classList.remove('open'); });
    panelFoot.appendChild(cancel);

    const send = document.createElement('button');
    send.type = 'button';
    send.className = 'send';
    send.textContent = 'Send Redirect';
    send.addEventListener('click', function () {
      const text = (ta.value || '').trim();
      if (!text) return;
      sendIntervention(mission.id, "redirect", agentName, text);
      ta.value = '';
      panel.classList.remove('open');
    });
    panelFoot.appendChild(send);

    panel.appendChild(panelFoot);
    tile.appendChild(panel);

    return tile;
  }

  async function sendIntervention(missionId, action, agent, text) {
    try {
      const token = window.__SESSION_TOKEN__ || '';
      const r = await fetch('/api/missions/' + encodeURIComponent(missionId) + '/intervene', {
        method: 'POST',
        headers: {
          'Content-Type': 'application/json',
          'x-session-token': token,
        },
        body: JSON.stringify({ action: action, agent: agent, text: text }),
      });
      if (!r.ok) console.error('[cockpit] intervene failed', r.status);
    } catch (e) { console.error('[cockpit] intervene threw', e); }
  }

  function onMissionEvent(event) {
    const action = event && event.action;
    const mission = event && (event.data || event.mission);
    if (!mission) return;
    if (action === 'create' || action === 'update') {
      if (mission.status === 'active') setActiveMission(mission);
      else if (mission.status === 'complete' || mission.status === 'failed') {
        clearActiveIfMatch(mission.id);
      }
    }
  }
  try {
    if (window.Hub && typeof window.Hub.on === 'function') {
      window.Hub.on('mission', onMissionEvent);
    }
  } catch (e) { console.error('[cockpit] failed to subscribe to mission events', e); }

  async function bootstrapActiveMission() {
    try {
      const token = window.__SESSION_TOKEN__ || '';
      const r = await fetch('/api/missions', {
        headers: { 'x-session-token': token },
      });
      if (!r.ok) return;
      const list = await r.json();
      const ch = currentChannel();
      const m = (list || []).reverse().find(function (x) {
        return x.status === 'active' && x.transcript_channel_id === ch;
      });
      if (m) setActiveMission(m);
    } catch (e) { /* tolerant */ }
  }
  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', bootstrapActiveMission);
  } else {
    bootstrapActiveMission();
  }
})();

/* Post-flight flow — appended in Slice 4.
 * When a mission status becomes 'complete', switches cockpit to post-flight view:
 * headline + stats + deliverables grid + decisions timeline + actions bar.
 * Accept & download generates a markdown report client-side from /api/missions/{id}/report.
 * Safe DOM only: createElement + textContent.
 */
(function () {
  const cockpit = window.__cockpit;
  if (!cockpit) return;

  let activeMissionId = null;
  let postFlightReport = null;

  function token() { return window.__SESSION_TOKEN__ || ''; }

  async function api(path, opts) {
    opts = opts || {};
    opts.headers = Object.assign({}, opts.headers || {}, { 'x-session-token': token() });
    if (opts.body && !opts.headers['Content-Type']) opts.headers['Content-Type'] = 'application/json';
    return fetch(path, opts);
  }

  try {
    if (window.Hub && typeof window.Hub.on === 'function') {
      window.Hub.on('mission', function (event) {
        const mission = event && (event.data || event.mission);
        if (!mission) return;
        if (mission.status === 'active') activeMissionId = mission.id;
        else if (mission.status === 'complete' || mission.status === 'failed') {
          if (mission.id === activeMissionId) loadPostFlight(mission.id);
        }
      });
    }
  } catch (e) {}

  function fmtDuration(seconds) {
    seconds = Math.max(0, Math.floor(seconds || 0));
    if (seconds < 60) return seconds + 's';
    const m = Math.floor(seconds / 60);
    if (m < 60) return m + 'm';
    const h = Math.floor(m / 60);
    return h + 'h ' + (m % 60) + 'm';
  }

  function fmtTime(ts) {
    if (!ts) return '';
    try {
      const d = new Date(ts * 1000);
      return d.toLocaleTimeString([], { hour: '2-digit', minute: '2-digit' });
    } catch (e) { return ''; }
  }

  function renderStats(rep) {
    const host = document.getElementById('cockpit-post-flight-stats');
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    const items = [
      { num: fmtDuration(rep.stats.duration_seconds), lbl: 'Total' },
      { num: String(rep.stats.interventions), lbl: 'Interv.' },
      { num: rep.stats.deliverables_met + '/' + rep.stats.deliverables_total, lbl: 'Met' },
      { num: String(rep.stats.crew_count), lbl: 'Crew' },
    ];
    items.forEach(function (it) {
      const stat = document.createElement('div');
      stat.className = 'stat';
      const num = document.createElement('span');
      num.className = 'num';
      num.textContent = it.num;
      stat.appendChild(num);
      const lbl = document.createElement('span');
      lbl.className = 'lbl';
      lbl.textContent = it.lbl;
      stat.appendChild(lbl);
      host.appendChild(stat);
    });
  }

  function renderDeliverables(rep) {
    const host = document.getElementById('cockpit-post-flight-deliverables');
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    (rep.deliverables || []).forEach(function (d, idx) {
      const row = document.createElement('div');
      row.className = 'deliv-row' + (d.met ? ' pass' : (d.required ? '' : ' skip'));
      row.title = 'Click to toggle';
      row.addEventListener('click', function () {
        toggleDeliverable(rep.mission_id, idx, !d.met);
      });

      const chk = document.createElement('div');
      chk.className = 'deliv-check';
      chk.textContent = d.met ? '✓' : (d.required ? '·' : '−');
      row.appendChild(chk);

      const body = document.createElement('div');
      const title = document.createElement('div');
      title.className = 'deliv-title';
      title.textContent = d.text || '';
      body.appendChild(title);
      const meta = document.createElement('div');
      meta.className = 'deliv-meta';
      meta.textContent = d.required ? 'required' : 'optional';
      body.appendChild(meta);
      row.appendChild(body);

      host.appendChild(row);
    });
  }

  function renderDecisions(rep) {
    const host = document.getElementById('cockpit-post-flight-decisions');
    if (!host) return;
    while (host.firstChild) host.removeChild(host.firstChild);
    (rep.decisions || []).forEach(function (d) {
      const item = document.createElement('div');
      item.className = 'tl-item';
      const tm = document.createElement('span');
      tm.className = 'tl-time';
      tm.textContent = fmtTime(d.ts);
      item.appendChild(tm);
      const bul = document.createElement('span');
      bul.className = 'tl-bullet ' + (d.type || '');
      item.appendChild(bul);
      const body = document.createElement('div');
      body.className = 'tl-body';
      body.textContent = d.body || d.type || '';
      item.appendChild(body);
      host.appendChild(item);
    });
  }

  function renderHeadlineMeta(rep) {
    const titleEl = document.getElementById('cockpit-post-flight-title');
    if (titleEl) titleEl.textContent = rep.title ? ('Mission complete — ' + rep.title) : 'Mission complete';
    const subEl = document.getElementById('cockpit-post-flight-sub');
    if (subEl) {
      const s = rep.stats;
      subEl.textContent = s.deliverables_met + ' of ' + s.deliverables_total +
        ' deliverables met · ' + s.interventions + ' interventions · ' +
        s.crew_count + ' crew · ' + fmtDuration(s.duration_seconds);
    }
    const meta = document.getElementById('cockpit-post-flight-meta');
    if (meta) {
      const required = rep.stats.deliverables_required;
      const met = rep.stats.deliverables_met;
      meta.textContent = (met >= required ? 'Ready to ship' : 'Required deliverables incomplete') +
        ' · ' + met + '/' + rep.stats.deliverables_total;
    }
  }

  function renderPostFlight(rep) {
    postFlightReport = rep;
    document.body.classList.remove('cockpit-active-mission');
    document.body.classList.add('cockpit-post-flight');
    renderHeadlineMeta(rep);
    renderStats(rep);
    renderDeliverables(rep);
    renderDecisions(rep);
  }

  async function loadPostFlight(missionId) {
    try {
      const r = await api('/api/missions/' + encodeURIComponent(missionId) + '/report');
      if (!r.ok) return;
      const rep = await r.json();
      renderPostFlight(rep);
    } catch (e) { console.error('[cockpit] loadPostFlight', e); }
  }

  async function toggleDeliverable(missionId, idx, met) {
    try {
      const r = await api(
        '/api/missions/' + encodeURIComponent(missionId) +
          '/deliverables/' + encodeURIComponent(idx),
        { method: 'PATCH', body: JSON.stringify({ met: met }) }
      );
      if (!r.ok) return;
      await loadPostFlight(missionId);
    } catch (e) { console.error('[cockpit] toggleDeliverable', e); }
  }

  async function cockpitCompleteMission() {
    if (!activeMissionId) {
      console.warn('[cockpit] no active mission to complete');
      return;
    }
    if (!window.confirm('Mark this mission complete?')) return;
    try {
      const r = await api('/api/missions/' + encodeURIComponent(activeMissionId) + '/complete',
                          { method: 'POST' });
      if (!r.ok) {
        console.error('[cockpit] complete failed', r.status);
        return;
      }
      await loadPostFlight(activeMissionId);
    } catch (e) { console.error('[cockpit] complete threw', e); }
  }
  window.cockpitCompleteMission = cockpitCompleteMission;

  function reportToMarkdown(rep) {
    const lines = [];
    lines.push('# Mission ' + rep.mission_id + ' — ' + (rep.title || ''));
    lines.push('');
    lines.push('Status: **' + rep.status + '**');
    lines.push('');
    if (rep.objective) {
      lines.push('## Objective');
      lines.push(rep.objective);
      lines.push('');
    }
    lines.push('## Stats');
    const s = rep.stats || {};
    lines.push('- Duration: ' + fmtDuration(s.duration_seconds));
    lines.push('- Interventions: ' + s.interventions);
    lines.push('- Deliverables met: ' + s.deliverables_met + ' / ' + s.deliverables_total);
    lines.push('- Crew: ' + s.crew_count);
    lines.push('');
    lines.push('## Deliverables');
    (rep.deliverables || []).forEach(function (d) {
      const mark = d.met ? '✓' : (d.required ? '×' : '−');
      const tag = d.required ? '' : ' (optional)';
      lines.push('- [' + mark + '] ' + (d.text || '') + tag);
    });
    lines.push('');
    lines.push('## Decisions timeline');
    (rep.decisions || []).forEach(function (d) {
      lines.push('- ' + fmtTime(d.ts) + ' · ' + (d.type || '') + ' · ' + (d.body || ''));
    });
    lines.push('');
    lines.push('## Crew');
    (rep.crew || []).forEach(function (m) {
      lines.push('- @' + (m.agent || '') + (m.role ? ' (' + m.role + ')' : ''));
    });
    return lines.join('\n');
  }

  function cockpitDownloadReport() {
    if (!postFlightReport) { console.warn('[cockpit] no report loaded'); return; }
    const md = reportToMarkdown(postFlightReport);
    const blob = new Blob([md], { type: 'text/markdown;charset=utf-8' });
    const url = URL.createObjectURL(blob);
    const a = document.createElement('a');
    a.href = url;
    a.download = postFlightReport.mission_id + '-report.md';
    document.body.appendChild(a);
    a.click();
    document.body.removeChild(a);
    setTimeout(function () { URL.revokeObjectURL(url); }, 1000);
  }
  window.cockpitDownloadReport = cockpitDownloadReport;

  function cockpitArchiveMission() {
    document.body.classList.remove('cockpit-post-flight');
    activeMissionId = null;
    postFlightReport = null;
  }
  window.cockpitArchiveMission = cockpitArchiveMission;
})();

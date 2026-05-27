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

  function buildCrewChip(name) {
    const chip = document.createElement('div');
    chip.className = 'agent-chip';
    chip.dataset.agent = name.toLowerCase();
    chip.dataset.on = 'false';
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
          'Authorization': token ? ('Bearer ' + token) : '',
        },
        body: JSON.stringify(brief),
      });
      if (!r.ok) {
        let detail = '';
        try { detail = (await r.json()).detail || ''; } catch (e) {}
        console.error('[cockpit] launch failed', r.status, detail);
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

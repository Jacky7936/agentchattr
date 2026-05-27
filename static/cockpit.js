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
        const senderEl = node.querySelector('.msg-sender');
        const textEl = node.querySelector('.msg-text');
        const timeEl = node.querySelector('.msg-time');
        if (!senderEl || !textEl) return;
        renderCockpitMessage({
          id: node.dataset.id || '',
          sender: senderEl.textContent || '',
          text: textEl.textContent || '',
          time: timeEl ? (timeEl.textContent || '') : '',
          channel: activeChannel(),
        });
      });
    } catch (e) { /* tolerant */ }
  });
})();

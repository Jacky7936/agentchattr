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

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', initState);
  } else {
    initState();
  }

  // Expose for Task 8 to wrap.
  window.__cockpit = {
    setApplyCockpitState(fn) { applyCockpitState = fn; },
    getApplyCockpitState() { return applyCockpitState; },
    isCockpitOn,
  };
})();

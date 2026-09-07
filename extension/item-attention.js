(() => {
  'use strict';
  const runtime = globalThis.chrome?.runtime;
  if (!runtime?.sendMessage || !runtime?.onMessage || window.top !== window) return;
  if (globalThis.__seenItemAttention) return;
  const MIN_VISIBLE = 0.5, MIN_TEXT = 40, FLUSH_MS = 5000;
  let active = false;
  const states = new Map();
  const normalize = value => String(value || '').replace(/\s+/g, ' ').trim();
  const hex = bytes => Array.from(bytes, byte => byte.toString(16).padStart(2, '0')).join('');
  async function digest(value) {
    return hex(new Uint8Array(await crypto.subtle.digest('SHA-256', new TextEncoder().encode(value))));
  }
  function candidate(element) {
    if (element.parentElement?.closest('article,[role="listitem"]')) return false;
    return normalize(element.textContent).length >= MIN_TEXT;
  }
  async function add(element) {
    if (states.has(element) || !candidate(element)) return;
    const text = normalize(element.textContent);
    const state = {id: crypto.randomUUID(), itemHash: await digest(text), container: element.tagName.toLowerCase(),
      startedAt: Date.now(), lastSeenAt: Date.now(), visibleMs: 0, maxRatio: 0, ratio: 0, activeSince: null};
    if (!element.isConnected) return;
    states.set(element, state); observer.observe(element);
  }
  function pause(state, now) {
    if (state.activeSince !== null) {
      state.visibleMs += Math.max(0, now - state.activeSince);
      state.activeSince = null;
      state.lastSeenAt = now;
    }
  }
  function resume(state, now) {
    if (active && document.visibilityState === 'visible' && state.ratio >= MIN_VISIBLE && state.activeSince === null) {
      state.activeSince = now;
    }
  }
  async function send(state) {
    if (state.visibleMs < 1000) return;
    try {
      await runtime.sendMessage({type: 'seen:item-attention', observation: {
        id: state.id, url: location.href, title: document.title, itemHash: state.itemHash,
        container: state.container, startedAt: state.startedAt, lastSeenAt: state.lastSeenAt,
        visibleMs: Math.round(state.visibleMs), maxRatio: state.maxRatio
      }});
    } catch { /* The cumulative value will be retried at the next checkpoint. */ }
  }
  async function checkpoint() {
    const now = Date.now(), tasks = [];
    for (const [element, state] of states) {
      pause(state, now);
      tasks.push(send(state));
      if (element.isConnected) resume(state, now);
      else states.delete(element);
    }
    await Promise.all(tasks);
  }
  const observer = new IntersectionObserver(entries => {
    const now = Date.now();
    for (const entry of entries) {
      const state = states.get(entry.target);
      if (!state) continue;
      pause(state, now);
      state.ratio = entry.intersectionRatio;
      state.maxRatio = Math.max(state.maxRatio, state.ratio);
      resume(state, now);
    }
  }, {threshold: [0, MIN_VISIBLE, 1]});
  const mutations = new MutationObserver(records => {
    for (const record of records) for (const node of record.addedNodes) {
      if (!(node instanceof Element)) continue;
      if (node.matches('article,[role="listitem"]')) add(node);
      for (const element of node.querySelectorAll('article,[role="listitem"]')) add(element);
    }
  });
  for (const element of document.querySelectorAll('article,[role="listitem"]')) add(element);
  mutations.observe(document, {subtree: true, childList: true});
  runtime.onMessage.addListener(message => {
    if (message.type !== 'seen:item-attention-active') return;
    const now = Date.now();
    for (const state of states.values()) pause(state, now);
    active = message.active === true;
    for (const state of states.values()) resume(state, now);
    if (!active) checkpoint();
  });
  document.addEventListener('visibilitychange', () => {
    const now = Date.now();
    for (const state of states.values()) pause(state, now);
    if (document.visibilityState === 'visible') for (const state of states.values()) resume(state, now);
    else checkpoint();
  });
  window.addEventListener('pagehide', checkpoint);
  setInterval(checkpoint, FLUSH_MS);
  globalThis.__seenItemAttention = {checkpoint};
})();

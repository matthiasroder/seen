const STORAGE_KEY = 'attentionState';

function blankState() { return {current: {}, pending: {}, lastCheckpointAt: 0}; }
function pause(visit, now) {
  if (visit?.activeSince !== null && visit?.activeSince !== undefined) {
    visit.focusedMs += Math.max(0, now - visit.activeSince);
    visit.activeSince = null;
  }
}
function wire(visit, now) {
  return {
    id: visit.id, url: visit.url, title: visit.title, startedAt: visit.startedAt,
    endedAt: visit.endedAt, focusedMs: visit.focusedMs + (visit.activeSince === null ? 0 : Math.max(0, now - visit.activeSince)),
    tabId: visit.tabId
  };
}

export function createAttentionTracker(storage, saveVisit, clock = () => Date.now(), uuid = () => crypto.randomUUID()) {
  async function load() {
    const value = (await storage.get(STORAGE_KEY))[STORAGE_KEY];
    return value && typeof value === 'object' && value.current && value.pending ? value : blankState();
  }
  async function save(state) {
    state.lastCheckpointAt = clock();
    await storage.set({[STORAGE_KEY]: state});
  }
  async function flush(state) {
    let saved = true;
    for (const [id, visit] of Object.entries(state.pending)) {
      try { await saveVisit(wire(visit, clock())); delete state.pending[id]; }
      catch { saved = false; }
    }
    for (const visit of Object.values(state.current)) {
      try { await saveVisit(wire(visit, clock())); }
      catch { saved = false; }
    }
    await save(state);
    return saved;
  }
  function finish(state, tabId, now) {
    const key = String(tabId), visit = state.current[key];
    if (!visit) return;
    pause(visit, now); visit.endedAt = now;
    state.pending[visit.id] = visit; delete state.current[key];
  }
  function newVisit(tab, now, active) {
    return {id: uuid(), url: tab.url, title: tab.title || '', startedAt: now, endedAt: null,
      focusedMs: 0, activeSince: active ? now : null, tabId: tab.id};
  }
  async function commit(state) {
    await save(state);
    await flush(state);
  }
  return {
    async activate(tab) {
      const now = clock(), state = await load();
      for (const visit of Object.values(state.current)) pause(visit, now);
      const key = String(tab.id);
      if (!state.current[key] || state.current[key].url !== tab.url) {
        finish(state, tab.id, now); state.current[key] = newVisit(tab, now, true);
      } else {
        state.current[key].title = tab.title || state.current[key].title;
        state.current[key].activeSince = now;
      }
      await commit(state);
    },
    async pauseAll() {
      const now = clock(), state = await load();
      for (const visit of Object.values(state.current)) pause(visit, now);
      await commit(state);
    },
    async navigate(tab, active) {
      const now = clock(), state = await load();
      finish(state, tab.id, now);
      state.current[String(tab.id)] = newVisit(tab, now, active);
      await commit(state);
    },
    async update(tab) {
      const state = await load(), visit = state.current[String(tab.id)];
      if (visit) visit.title = tab.title || visit.title;
      await commit(state);
    },
    async remove(tabId) {
      const state = await load(); finish(state, tabId, clock()); await commit(state);
    },
    async checkpoint() {
      const now = clock(), state = await load();
      for (const visit of Object.values(state.current)) {
        const running = visit.activeSince !== null;
        pause(visit, now);
        if (running) visit.activeSince = now;
      }
      await commit(state);
    },
    async restart(tab) {
      const now = clock(), state = await load(), endedAt = state.lastCheckpointAt || now;
      for (const visit of Object.values(state.current)) {
        visit.activeSince = null; visit.endedAt = endedAt;
        state.pending[visit.id] = visit;
      }
      state.current = {};
      if (tab) state.current[String(tab.id)] = newVisit(tab, now, true);
      await commit(state);
    },
    async clear() { await storage.remove(STORAGE_KEY); }
  };
}

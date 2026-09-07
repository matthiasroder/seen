import {webUrl, originAllowed, validChunk, senderKey, CHUNK_BYTES} from './core.js';
import {beginUpload, uploadInfo, appendChunk, finishUpload, pendingUploads, readChunk, removeUpload, clearQueue, legacyPages} from './store.js';
import {nativeRequest} from './native.js';
import {diagnosticRecord, appendDiagnostic} from './diagnostics.js';
import {createAttentionTracker} from './attention.js';

let queue = Promise.resolve(), diagnosticFileQueue = Promise.resolve(), flushing = false;
let idleState = 'active';
const serial = job => { const task = queue.then(job); queue = task.catch(() => {}); return task; };
const READ_ONLY_UI = new Set(['seen:status', 'seen:days', 'seen:list', 'seen:get', 'seen:read', 'seen:legacy']);
function syncDiagnosticFile(records) {
  const task = diagnosticFileQueue.then(() => nativeRequest('diagnostics', {records}));
  diagnosticFileQueue = task.catch(() => {});
}
async function preferences() { return {paused: false, ...(await chrome.storage.local.get('preferences')).preferences}; }
async function origins() { return ((await chrome.permissions.getAll()).origins || []).filter(p => /^https?:\/\/(?:\*|[^*/]+)\/\*$/.test(p)); }
async function reportError(error) { await chrome.storage.local.set({lastError: String(error.message || error)}); }
function contentSender(sender) { return sender.id === chrome.runtime.id && !!sender.tab && Number.isInteger(sender.frameId); }
function trustedUI(sender) { return sender.id === chrome.runtime.id && sender.url?.startsWith(chrome.runtime.getURL('')) && !sender.tab?.incognito; }
const CONTENT_DIAGNOSTICS = new Set(['script-loaded', 'capture-started', 'capture-start-error']);
const attention = createAttentionTracker(chrome.storage.local, async visit => {
  const result = await nativeRequest('attention', {visit});
  if (result?.saved !== true) throw new Error('Page attention was not saved.');
});

async function recordDiagnostic(event, values = {}, coalesce = false) {
  try {
    const current = (await chrome.storage.local.get('diagnostics')).diagnostics;
    const diagnostics = appendDiagnostic(current, diagnosticRecord(event, values), coalesce);
    await chrome.storage.local.set({diagnostics});
    syncDiagnosticFile(diagnostics);
  } catch { /* Diagnostics must never interrupt capture. */ }
}

function diagnosticScope(sender, values = {}) {
  return {tabId: sender.tab?.id, frameId: sender.frameId, documentId: sender.documentId || '',
    senderUrl: sender.url || '', ...values};
}

async function captureDecision(sender) {
  if (!contentSender(sender)) return {allowed: false, reason: 'invalid-sender'};
  if (sender.frameId !== 0) return {allowed: false, reason: 'subframe'};
  if (sender.tab.incognito) return {allowed: false, reason: 'incognito'};
  if ((await preferences()).paused) return {allowed: false, reason: 'paused'};
  const tab = await chrome.tabs.get(sender.tab.id).catch(() => null);
  if (!tab) return {allowed: false, reason: 'tab-missing'};
  if (tab.incognito) return {allowed: false, reason: 'incognito'};
  if (!tab.active) return {allowed: false, reason: 'tab-inactive'};
  const window = await chrome.windows.get(tab.windowId).catch(() => null);
  if (!window) return {allowed: false, reason: 'window-missing'};
  if (!window.focused) return {allowed: false, reason: 'window-unfocused'};
  const sites = await origins();
  if (!originAllowed(tab.url, sites)) return {allowed: false, reason: 'top-origin-not-granted'};
  if (!originAllowed(sender.url, sites)) return {allowed: false, reason: 'document-origin-not-granted'};
  return {allowed: true};
}

async function syncCapture() {
  const prefs = await preferences(), sites = await origins();
  const registered = await chrome.scripting.getRegisteredContentScripts();
  if (registered.length) await chrome.scripting.unregisterContentScripts({ids: registered.map(s => s.id)});
  if (!prefs.paused && sites.length) await chrome.scripting.registerContentScripts([{
    id: 'seen-dom', matches: sites, js: ['item-attention.js', 'capture.js'], runAt: 'document_idle', allFrames: false,
    persistAcrossSessions: true, world: 'ISOLATED'
  }]);
  for (const tab of await chrome.tabs.query({})) {
    if (!tab.id || tab.incognito) continue;
    try { await chrome.tabs.sendMessage(tab.id, {type: prefs.paused ? 'seen:stop' : 'seen:start'}); }
    catch {
      if (!prefs.paused && originAllowed(tab.url, sites)) {
        try { await chrome.scripting.executeScript({target: {tabId: tab.id, allFrames: false}, files: ['item-attention.js', 'capture.js']}); } catch { /* Chrome-protected/closing tab. */ }
      }
    }
  }
  await chrome.action.setBadgeText({text: prefs.paused ? 'Ⅱ' : ''});
  await chrome.action.setBadgeBackgroundColor({color: '#42564a'});
}

async function activeForegroundTab() {
  if (idleState !== 'active') return null;
  const [tab] = await chrome.tabs.query({active: true, lastFocusedWindow: true});
  if (!await trackable(tab)) return null;
  const window = await chrome.windows.get(tab.windowId).catch(() => null);
  return window?.focused ? tab : null;
}

async function trackable(tab) {
  if (!tab || tab.incognito || !webUrl(tab.url) || (await preferences()).paused) return false;
  return originAllowed(tab.url, await origins());
}

async function restartAttention() {
  idleState = await chrome.idle.queryState(60);
  await attention.restart(await activeForegroundTab());
  await syncItemAttentionState();
}

async function syncItemAttentionState() {
  const foreground = await activeForegroundTab();
  for (const tab of await chrome.tabs.query({})) {
    if (!tab.id || tab.incognito) continue;
    chrome.tabs.sendMessage(tab.id, {type: 'seen:item-attention-active', active: tab.id === foreground?.id}).catch(() => {});
  }
}

async function flushOutbox() {
  if (flushing) return;
  flushing = true;
  let delivered = 0;
  try {
    for (const record of (await pendingUploads()).filter(r => r.ready)) {
      const known = await nativeRequest('begin', {snapshot: record});
      if (!known.saved) {
        for (let index = 0; index < record.chunks; index++) {
          const data = await readChunk(record.id, index);
          if (!data) throw new Error('Incomplete queue record: ' + record.id);
          await nativeRequest('chunk', {snapshotId: record.id, index, data});
        }
        await nativeRequest('commit', {snapshotId: record.id});
      }
      await removeUpload(record.id);
      if (record.frameId === 0) await recordDiagnostic('capture-saved', {url: record.url, tabId: record.tabId,
        frameId: record.frameId, documentId: record.documentId, bytes: record.bytes}, true);
      delivered++;
    }
    if (delivered) {
      await chrome.storage.local.remove('lastError');
      nativeRequest('index').catch(() => {});
    }
  } catch (error) { await recordDiagnostic('delivery-error', {error: String(error.message || error)}); await reportError(error); }
  finally { flushing = false; }
}

async function handle(message, sender) {
  if (!message || typeof message.type !== 'string') throw new Error('Invalid request.');
  const type = message.type;
  if (type === 'seen:diagnostic') {
    if (!contentSender(sender) || sender.tab.incognito || !CONTENT_DIAGNOSTICS.has(message.event)) return false;
    if (sender.frameId !== 0) return true;
    await recordDiagnostic(message.event, diagnosticScope(sender, {url: String(message.url || ''),
      visibility: String(message.visibility || ''), stage: String(message.stage || ''), error: String(message.error || '')}), true);
    return true;
  }
  if (type === 'seen:allowed') {
    const decision = await captureDecision(sender);
    if (!decision.allowed && sender.frameId === 0) await recordDiagnostic('capture-rejected', diagnosticScope(sender,
      {stage: 'start', reason: decision.reason, url: sender.url || ''}), true);
    return decision;
  }
  if (type === 'seen:item-attention') {
    const decision = await captureDecision(sender);
    if (!decision.allowed || idleState !== 'active' || !message.observation || typeof message.observation !== 'object') return false;
    const result = await nativeRequest('item-attention', {observation: {...message.observation,
      tabId: sender.tab.id, documentId: sender.documentId || ''}});
    return result?.saved === true;
  }
  if (type === 'seen:begin') {
    const decision = await captureDecision(sender);
    const reason = decision.allowed && message.url !== sender.url ? 'url-mismatch' : decision.reason;
    if (reason) {
      if (sender.frameId === 0) await recordDiagnostic('capture-rejected', diagnosticScope(sender,
        {stage: 'begin', reason, url: String(message.url || ''), bytes: message.bytes}), true);
      return {allowed: false, reason};
    }
    if (!Number.isSafeInteger(message.bytes) || message.bytes <= 0 ||
        message.chunks !== Math.ceil(message.bytes / CHUNK_BYTES)) throw new Error('Invalid snapshot header.');
    const id = crypto.randomUUID(), top = await chrome.tabs.get(sender.tab.id);
    await beginUpload({id, owner: senderKey(sender), url: message.url, topUrl: top.url, title: String(message.title || ''),
      capturedAt: Number.isFinite(message.capturedAt) ? message.capturedAt : Date.now(), frameId: sender.frameId,
      tabId: sender.tab.id, documentId: sender.documentId || '', bytes: message.bytes, chunks: message.chunks});
    return {allowed: true, id};
  }
  if (type === 'seen:chunk' || type === 'seen:finish') {
    if (!contentSender(sender) || sender.tab.incognito || (await preferences()).paused) throw new Error('Capture is not allowed.');
    const record = await uploadInfo(message.uploadId);
    if (!record || record.owner !== senderKey(sender)) throw new Error('Unknown capture session.');
    if (type === 'seen:chunk') {
      if (!validChunk(message.data) || !Number.isSafeInteger(message.index)) throw new Error('Invalid DOM chunk.');
      await appendChunk(record.id, message.index, message.data);
    } else {
      const bytes = new Uint8Array(record.bytes);
      let offset = 0;
      for (let index = 0; index < record.chunks; index++) {
        const part = await readChunk(record.id, index);
        if (!part) throw new Error('Incomplete snapshot.');
        const binary = atob(part);
        if (binary.length !== Math.min(CHUNK_BYTES, record.bytes - offset)) throw new Error('Incorrect chunk size.');
        for (let i = 0; i < binary.length; i++) bytes[offset++] = binary.charCodeAt(i);
      }
      if (offset !== record.bytes) throw new Error('Incomplete snapshot.');
      // The extension worker is a secure context even when the source page uses HTTP.
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
      await finishUpload(record.id, hash);
      // Acknowledge durable local queuing before optional native delivery.
      setTimeout(() => serial(flushOutbox).catch(reportError), 0);
    }
    return {queued: true};
  }
  if (type === 'seen:capture-error') {
    if (contentSender(sender) && !sender.tab.incognito) {
      const error = String(message.error).slice(0, 500);
      if (sender.frameId === 0) await recordDiagnostic('capture-error', diagnosticScope(sender, {url: sender.url || '', error}));
      await reportError(new Error(error));
    }
    return true;
  }
  if (!trustedUI(sender)) throw new Error('Not available to web pages.');
  switch (type) {
    case 'seen:status': {
      let database, helperError;
      try { database = await nativeRequest('status'); } catch (error) { helperError = error.message; }
      const outbox = await pendingUploads();
      const local = await chrome.storage.local.get(['lastError', 'diagnostics']);
      return {preferences: await preferences(), origins: await origins(), database, helperError, extensionId: chrome.runtime.id,
        queued: outbox.filter(r => r.ready).length, incomplete: outbox.filter(r => !r.ready).length,
        lastError: local.lastError, diagnostics: Array.isArray(local.diagnostics) ? local.diagnostics : []};
    }
    case 'seen:pause': {
      const paused = !!message.paused;
      await chrome.storage.local.set({preferences: {paused}});
      await syncCapture();
      await attention.pauseAll();
      if (!paused) {
        const tab = await activeForegroundTab();
        if (tab) await attention.activate(tab);
      }
      await syncItemAttentionState();
      return true;
    }
    case 'seen:sync': await syncCapture(); await flushOutbox(); return true;
    case 'seen:diagnostics-clear':
      await chrome.storage.local.remove('diagnostics');
      syncDiagnosticFile([]);
      return true;
    case 'seen:days': return nativeRequest('days');
    case 'seen:list': return nativeRequest('list', {query: String(message.query || ''), day: String(message.day || ''), offset: message.offset || 0});
    case 'seen:get': return nativeRequest('get', {snapshotId: message.id});
    case 'seen:read': return nativeRequest('read', {snapshotId: message.id, offset: message.offset || 0});
    case 'seen:delete': return nativeRequest('delete', {snapshotId: message.id});
    case 'seen:clear': await nativeRequest('clear'); await clearQueue(); await attention.clear(); return true;
    case 'seen:legacy': return legacyPages();
    default: throw new Error('Unknown request.');
  }
}
chrome.runtime.onMessage.addListener((message, sender, respond) => {
  const task = READ_ONLY_UI.has(message?.type) && trustedUI(sender) ? handle(message, sender) : serial(() => handle(message, sender));
  task.then(value => respond({ok: true, value}), error => respond({ok: false, error: error.message}));
  return true;
});
chrome.runtime.onInstalled.addListener(() => serial(async () => {
  await chrome.storage.local.setAccessLevel({accessLevel: 'TRUSTED_CONTEXTS'});
  await chrome.alarms.create('seen-delivery', {periodInMinutes: 1});
  await chrome.alarms.clear('seen-retention');
  await syncCapture(); await flushOutbox(); await restartAttention();
}).catch(reportError));
chrome.runtime.onStartup.addListener(() => serial(async () => {
  await chrome.alarms.create('seen-delivery', {periodInMinutes: 1});
  await syncCapture(); await flushOutbox(); await restartAttention();
}).catch(reportError));
async function permissionsChanged() {
  await syncCapture(); await attention.pauseAll();
  const tab = await activeForegroundTab();
  if (tab) await attention.activate(tab);
  await syncItemAttentionState();
}
chrome.permissions.onAdded.addListener(() => serial(permissionsChanged).catch(reportError));
chrome.permissions.onRemoved.addListener(() => serial(permissionsChanged).catch(reportError));
chrome.alarms.onAlarm.addListener(alarm => {
  if (alarm.name === 'seen-delivery') serial(async () => { await flushOutbox(); await attention.checkpoint(); await syncItemAttentionState(); }).catch(reportError);
});
chrome.tabs.onUpdated.addListener((tabId, change, tab) => {
  const url = webUrl(change.url || tab.url);
  if ((change.url || change.status === 'loading') && tab.active && !tab.incognito && url) {
    recordDiagnostic('navigation-observed', {url, tabId, frameId: 0, stage: change.url ? 'route' : 'loading'}, true);
  }
  if (change.url || change.status === 'loading') {
    serial(async () => {
      if (!await trackable(tab)) { await attention.remove(tabId); return; }
      const window = await chrome.windows.get(tab.windowId).catch(() => null);
      await attention.navigate(tab, idleState === 'active' && tab.active && window?.focused === true);
      await syncItemAttentionState();
    }).catch(reportError);
  } else if (change.title) serial(async () => {
    if (await trackable(tab)) await attention.update(tab);
  }).catch(reportError);
  if (change.url && !tab.incognito) chrome.tabs.sendMessage(tabId, {type: 'seen:route'}).catch(() => {});
});
async function foreground() {
  for (const tab of await chrome.tabs.query({active: true})) if (!tab.incognito) chrome.tabs.sendMessage(tab.id, {type: 'seen:start'}).catch(() => {});
}
chrome.tabs.onActivated.addListener(({tabId}) => {
  foreground().catch(() => {});
  serial(async () => {
    const tab = await chrome.tabs.get(tabId).catch(() => null);
    const window = tab ? await chrome.windows.get(tab.windowId).catch(() => null) : null;
    if (await trackable(tab) && idleState === 'active' && window?.focused) await attention.activate(tab);
    else await attention.pauseAll();
    await syncItemAttentionState();
  }).catch(reportError);
});
chrome.tabs.onRemoved.addListener(tabId => serial(() => attention.remove(tabId)).catch(reportError));
chrome.windows.onFocusChanged.addListener(windowId => {
  foreground().catch(() => {});
  serial(async () => {
    await attention.pauseAll();
    if (windowId === chrome.windows.WINDOW_ID_NONE || idleState !== 'active') {
      await syncItemAttentionState();
      return;
    }
    const tab = await activeForegroundTab();
    if (tab) await attention.activate(tab);
    await syncItemAttentionState();
  }).catch(reportError);
});
chrome.idle.onStateChanged.addListener(state => {
  idleState = state;
  serial(async () => {
    await attention.pauseAll();
    if (state === 'active') {
      const tab = await activeForegroundTab();
      if (tab) await attention.activate(tab);
    }
    await syncItemAttentionState();
  }).catch(reportError);
});

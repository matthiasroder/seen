import {CHUNK_BYTES, validChunk, senderKey, webUrl} from './core.js';
import {beginUpload, uploadInfo, appendChunk, finishUpload, pendingUploads, readChunk, removeUpload} from './store.js';

const api = globalThis.browser || globalThis.chrome;
let serial = Promise.resolve();
const ordered = fn => { const next = serial.then(fn); serial = next.catch(() => {}); return next; };
const native = async (op, rest = {}) => {
  const reply = await api.runtime.sendNativeMessage('com.matthiasroder.SeenMobile', {op, ...rest});
  if (!reply?.ok) throw new Error(reply?.error || 'Seen companion unavailable; capture stays queued.');
  return reply.value;
};
const source = sender => sender.id === api.runtime.id && sender.tab?.incognito === false && Number.isInteger(sender.frameId);
const ui = sender => sender.id === api.runtime.id && sender.url?.startsWith(api.runtime.getURL('')) && sender.tab?.incognito !== true;
const scheduleDelivery = () => setTimeout(() => ordered(deliver).catch(error => api.storage.local.set({lastError: error.message}).catch(() => {})), 0);
async function allowed(sender) {
  if (!source(sender) || sender.frameId !== 0 || (await native('status')).paused) return false;
  const tab = await api.tabs.get(sender.tab.id).catch(() => null);
  return !!tab && tab.incognito === false && tab.active === true && !!webUrl(tab.url) && !!webUrl(sender.url);
}
async function deliver() {
  for (const item of (await pendingUploads()).filter(item => item.ready)) {
    let result = await native('begin', {snapshot: item});
    if (!result.saved) {
      const next = result.nextIndex ?? 0;
      if (!Number.isSafeInteger(next) || next < 0 || next > item.chunks) throw new Error('Invalid companion resume offset.');
      for (let index = next; index < item.chunks; index++) {
        await native('chunk', {snapshotId: item.id, index, data: await readChunk(item.id, index)});
      }
      result = await native('commit', {snapshotId: item.id});
    }
    if (result.saved !== true || result.snapshotId !== item.id || result.hash !== item.hash) throw new Error('Companion did not acknowledge this complete capture.');
    await removeUpload(item.id); // Only after the app-group queue has committed.
  }
  await api.storage.local.set({lastError: ''});
  const last = (await api.storage.local.get('lastTransferAttempt')).lastTransferAttempt || 0;
  if (Date.now() - last > 60000) {
    await api.storage.local.set({lastTransferAttempt: Date.now()});
    // Best effort while Safari is awake. The native durable queue survives suspension.
    native('sync').catch(() => {});
  }
}
async function handle(message, sender) {
  if (!message || typeof message.type !== 'string') throw new Error('Invalid request.');
  if (message.type === 'seen:allowed') {
    const permission = await allowed(sender);
    if (permission) scheduleDelivery();
    return {allowed: permission};
  }
  if (message.type === 'seen:begin') {
    if (!await allowed(sender) || message.url !== sender.url) return {allowed: false};
    if (!Number.isSafeInteger(message.bytes) || message.bytes <= 0 || message.chunks !== Math.ceil(message.bytes / CHUNK_BYTES)) throw new Error('Invalid capture size.');
    const id = crypto.randomUUID(), top = await api.tabs.get(sender.tab.id);
    await beginUpload({id, owner: senderKey(sender), url: message.url, topUrl: top.url, title: String(message.title || ''),
      capturedAt: Date.now(), frameId: sender.frameId, tabId: sender.tab.id, documentId: sender.documentId || '', bytes: message.bytes, chunks: message.chunks});
    return {allowed: true, id};
  }
  if (['seen:chunk', 'seen:finish'].includes(message.type)) {
    if (!source(sender) || (await native('status')).paused) throw new Error('Capture not allowed.');
    const item = await uploadInfo(message.uploadId);
    if (!item || item.owner !== senderKey(sender)) throw new Error('Capture owner mismatch.');
    if (message.type === 'seen:chunk') {
      if (!Number.isSafeInteger(message.index) || !validChunk(message.data)) throw new Error('Invalid chunk.');
      await appendChunk(item.id, message.index, message.data);
    } else {
      const bytes = new Uint8Array(item.bytes);
      let offset = 0;
      for (let index = 0; index < item.chunks; index++) {
        const chunk = await readChunk(item.id, index);
        if (!chunk) throw new Error('Incomplete capture.');
        const part = atob(chunk);
        if (part.length !== Math.min(CHUNK_BYTES, item.bytes - offset)) throw new Error('Invalid chunk length.');
        for (let i = 0; i < part.length; i++) bytes[offset++] = part.charCodeAt(i);
      }
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
      await finishUpload(item.id, hash);
      scheduleDelivery();
    }
    return {queued: true};
  }
  if (message.type === 'seen:capture-error') {
    if (source(sender)) await api.storage.local.set({lastError: String(message.error).slice(0, 200)});
    return true;
  }
  if (!ui(sender)) throw new Error('Not available to web pages.');
  if (message.type === 'seen:status') {
    const queue = await pendingUploads();
    return {...await native('status'), browserQueued: queue.filter(r => r.ready).length,
      browserIncomplete: queue.filter(r => !r.ready).length, browserError: (await api.storage.local.get('lastError')).lastError};
  }
  if (message.type === 'seen:pause') return native('pause', {paused: !!message.paused});
  if (message.type === 'seen:sync') { await deliver(); return native('sync'); }
  throw new Error('Unknown request.');
}
api.runtime.onMessage.addListener((message, sender, reply) => {
  ordered(() => handle(message, sender)).then(value => reply({ok: true, value}), error => reply({ok: false, error: error.message}));
  return true;
});
api.tabs.onActivated.addListener(({tabId}) => api.tabs.sendMessage(tabId, {type: 'seen:start'}).catch(() => {}));
api.tabs.onUpdated.addListener((id, change, tab) => {
  if (change.url && tab.incognito === false) api.tabs.sendMessage(id, {type: 'seen:route'}).catch(() => {});
});

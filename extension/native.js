import {NATIVE_HOST} from './core.js';
let port, idleTimer;
const requests = new Map();
export function nativeRequest(op, payload = {}) {
  clearTimeout(idleTimer);
  if (!port) {
    port = chrome.runtime.connectNative(NATIVE_HOST);
    const ownPort = port;
    ownPort.onMessage.addListener(message => {
      const pending = requests.get(message.id);
      if (!pending) return;
      requests.delete(message.id); clearTimeout(pending.timer);
      if (message.ok) pending.resolve(message.value);
      else pending.reject(new Error(message.error || 'SQLite helper failed.'));
      if (!requests.size) idleTimer = setTimeout(() => { if (port === ownPort) { port = undefined; ownPort.disconnect(); } }, 10000);
    });
    ownPort.onDisconnect.addListener(() => {
      const detail = chrome.runtime.lastError?.message;
      if (port !== ownPort) return;
      port = undefined;
      for (const pending of requests.values()) { clearTimeout(pending.timer); pending.reject(new Error(detail || 'Local helper disconnected.')); }
      requests.clear();
    });
  }
  return new Promise((resolve, reject) => {
    const id = crypto.randomUUID();
    const timer = setTimeout(() => {
      requests.delete(id); reject(new Error('Local helper did not respond. Snapshot remains queued.'));
      if (!requests.size && port) { const old = port; port = undefined; old.disconnect(); }
    }, 30000);
    requests.set(id, {resolve, reject, timer});
    try { port.postMessage({id, op, ...payload}); }
    catch (error) { requests.delete(id); clearTimeout(timer); reject(error); }
  });
}

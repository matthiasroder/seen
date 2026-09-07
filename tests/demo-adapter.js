/* Synthetic Chrome/native adapter. NEVER packaged. Actual SQLite has independent tests. */
(() => {
  const event = () => ({listeners: [], addListener(fn) { this.listeners.push(fn); }, emit(...args) { for (const fn of this.listeners) fn(...args); }});
  const messages = event(), values = {}, settings = {paused: false}, saved = new Map(), visits = new Map(), uploads = new Map(), closedRoots = new WeakMap();
  const allowedOrigins = ['http://*/*', 'https://*/*'];
  let ready, registered = [];
  const control = {helperOnline: true, active: true, focused: true};
  const currentTab = () => ({id: 1, url: location.href, incognito: false, active: control.active, windowId: 1});
  const sender = () => ({id: 'seen-test', url: location.href, origin: location.origin, documentId: 'fixture-document', frameId: 0, tab: currentTab()});
  async function native(message) {
    const {op} = message;
    if (op === 'status') return {path: '/path/to/seen/data/seen.sqlite (synthetic preview)', diagnosticLog: '/path/to/seen/logs/capture.jsonl (synthetic preview)', snapshots: saved.size, visits: visits.size};
    if (op === 'diagnostics') return {path: '/path/to/seen/logs/capture.jsonl (synthetic preview)', records: message.records.length};
    if (op === 'attention') { visits.set(message.visit.id, message.visit); return {saved:true}; }
    if (op === 'days') {
      const counts = new Map();
      for (const record of saved.values()) {
        const date = new Date(record.meta.capturedAt), day = [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
        const current = counts.get(day) || {day, snapshots: 0, lastSeen: 0}; current.snapshots++; current.lastSeen = Math.max(current.lastSeen, record.meta.capturedAt); counts.set(day, current);
      }
      return {items: [...counts.values()].sort((a, b) => b.day.localeCompare(a.day))};
    }
    if (op === 'begin') {
      if (saved.has(message.snapshot.id)) return {saved: true};
      uploads.set(message.snapshot.id, {meta: message.snapshot, chunks: []}); return {saved: false};
    }
    if (op === 'chunk') { uploads.get(message.snapshotId).chunks[message.index] = message.data; return true; }
    if (op === 'commit') {
      const upload = uploads.get(message.snapshotId), bytes = new Uint8Array(upload.meta.bytes);
      let at = 0;
      for (const chunk of upload.chunks) for (const char of atob(chunk)) bytes[at++] = char.charCodeAt(0);
      const hash = Array.from(new Uint8Array(await crypto.subtle.digest('SHA-256', bytes)), b => b.toString(16).padStart(2, '0')).join('');
      if (at !== bytes.length || hash !== upload.meta.hash) throw new Error('Bad upload.');
      saved.set(message.snapshotId, {meta: upload.meta, bytes, dom: JSON.parse(new TextDecoder().decode(bytes))});
      uploads.delete(message.snapshotId); return {saved: true};
    }
    if (op === 'list') {
      const all = [...saved.values()].filter(r => {
        const date = new Date(r.meta.capturedAt), day = [date.getFullYear(), String(date.getMonth() + 1).padStart(2, '0'), String(date.getDate()).padStart(2, '0')].join('-');
        return (!message.day || message.day === day) && (r.meta.url + r.meta.title + JSON.stringify(r.dom)).toLowerCase().includes((message.query || '').toLowerCase());
      }).sort((a,b) => b.meta.capturedAt-a.meta.capturedAt);
      const offset = message.offset || 0;
      return {items: all.slice(offset, offset + 100).map(r => ({...r.meta, lastSeen: r.meta.capturedAt})), more: all.length > offset + 100};
    }
    if (op === 'get') { const value = saved.get(message.snapshotId); if (!value) throw new Error('Missing snapshot'); return {...value.meta, lastSeen: value.meta.capturedAt}; }
    if (op === 'read') {
      const value = saved.get(message.snapshotId), offset = message.offset || 0;
      return {data: btoa(Array.from(value.bytes.subarray(offset, offset + 65536), b => String.fromCharCode(b)).join('')), bytes: value.bytes.length};
    }
    if (op === 'delete') { saved.delete(message.snapshotId); return true; }
    if (op === 'clear') { saved.clear(); uploads.clear(); return true; }
    throw new Error('Unknown mock native operation.');
  }
  const runtime = {
    id: 'seen-test', getURL: path => location.origin + '/extension/' + path, onMessage: messages, onInstalled: event(), onStartup: event(),
    async sendMessage(message) {
      await ready;
      const isContent = ['seen:allowed','seen:begin','seen:chunk','seen:finish','seen:capture-error'].includes(message.type);
      return runtime.dispatch(message, isContent ? sender() : {id:'seen-test',url:runtime.getURL('archive.html')});
    },
    dispatch(message, source) {
      return new Promise(resolve => { for (const fn of messages.listeners) fn(message, source, resolve); });
    },
    connectNative() {
      if (!control.helperOnline) throw new Error('Synthetic helper offline.');
      const port = {onMessage: event(), onDisconnect: event(), disconnect() { port.onDisconnect.emit(); },
        postMessage(message) {
          Promise.resolve().then(() => {
            if (!control.helperOnline) throw new Error('Synthetic helper offline.');
            return native(message);
          }).then(value => port.onMessage.emit({id:message.id,ok:true,value}), error => port.onMessage.emit({id:message.id,ok:false,error:error.message}));
        }};
      return port;
    }
  };
  window.chrome = {
    runtime,
    dom: {openOrClosedShadowRoot: element => closedRoots.get(element) || element.shadowRoot},
    storage: {local: {async get(key) {
      if (Array.isArray(key)) return Object.fromEntries(key.map(name => [name, values[name]]));
      return key === 'preferences' ? {preferences:{...settings}} : {[key]:values[key]}; },
      async set(data) { if (data.preferences) Object.assign(settings,data.preferences); Object.assign(values,data); },
      async remove(key) { delete values[key]; }, async setAccessLevel() {}}},
    permissions: {async getAll() { return {origins:[...allowedOrigins]}; }, onAdded:event(), onRemoved:event()},
    scripting: {async getRegisteredContentScripts(){return registered;}, async unregisterContentScripts(){registered=[];}, async registerContentScripts(scripts){registered=scripts;},async executeScript(){}},
    tabs: {async get() {return currentTab();},async query(query) {return query.active ? [currentTab()] : [];},async sendMessage(){},onUpdated:event(),onActivated:event(),onRemoved:event()},
    windows: {WINDOW_ID_NONE:-1,async get(){return {focused:control.focused};},onFocusChanged:event()},
    idle: {async queryState(){return 'active';},onStateChanged:event()},
    action: {async setBadgeText(){},async setBadgeBackgroundColor(){}},
    alarms: {async create(){},async clear(){},onAlarm:event()}
  };
  ready = import('../extension/background.js').then(async () => {
    if (!location.pathname.startsWith('/extension/')) return;
    for (let index = 0; index < 3; index++) {
      const id = crypto.randomUUID(), dom = {html: '<!DOCTYPE html><html><head><title>Synthetic DOM ' + index + '</title></head><body><h1>A page, remembered.</h1><p hidden>Hidden content is preserved.</p><script>/* Stored as inert source. */</script></body></html>', shadowRoots: [], formState: []};
      const bytes = new TextEncoder().encode(JSON.stringify(dom));
      saved.set(id, {meta: {id,url:'https://example.test/article/' + index,title:'Synthetic DOM snapshot ' + index,capturedAt:Date.now()-index*60000,frameId:0,bytes:bytes.length}, bytes, dom});
    }
  });
  window.seenTest = {ready: () => ready, sender, settings, control, saved, visits, closedRoots, registered: () => registered};
})();

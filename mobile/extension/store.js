let connection;
function database() {
  if (!connection) connection = new Promise((resolve, reject) => {
    const request = indexedDB.open('seen', 2);
    request.onupgradeneeded = () => {
      const db = request.result;
      if (!db.objectStoreNames.contains('outbox')) db.createObjectStore('outbox', {keyPath: 'id'});
      if (!db.objectStoreNames.contains('parts')) db.createObjectStore('parts', {keyPath: ['id', 'index']});
      // Existing v1 pages/meta are deliberately left untouched for legacy export.
    };
    request.onsuccess = () => {
      const db = request.result;
      db.onversionchange = () => { db.close(); connection = undefined; };
      resolve(db);
    };
    request.onerror = () => { connection = undefined; reject(request.error); };
  });
  return connection;
}
async function transaction(stores, mode, work) {
  const db = await database();
  return new Promise((resolve, reject) => {
    const tx = db.transaction(stores, mode);
    let result;
    tx.oncomplete = () => resolve(result);
    tx.onabort = tx.onerror = () => reject(tx.error || new Error('Local queue write failed.'));
    try { work(tx, value => { result = value; }); } catch (error) { tx.abort(); reject(error); }
  });
}
export function beginUpload(record) {
  return transaction(['outbox'], 'readwrite', tx => tx.objectStore('outbox').add({...record, received: 0, ready: false}));
}
export function uploadInfo(id) {
  return transaction(['outbox'], 'readonly', (tx, done) => {
    const req = tx.objectStore('outbox').get(id); req.onsuccess = () => done(req.result);
  });
}
export function appendChunk(id, index, data) {
  return transaction(['outbox', 'parts'], 'readwrite', (tx, done) => {
    const records = tx.objectStore('outbox'), req = records.get(id);
    req.onsuccess = () => {
      const record = req.result;
      if (!record || record.ready || record.received !== index || index >= record.chunks) { tx.abort(); return; }
      tx.objectStore('parts').add({id, index, data});
      record.received++; records.put(record); done(true);
    };
  });
}
export function finishUpload(id, hash) {
  return transaction(['outbox'], 'readwrite', (tx, done) => {
    const records = tx.objectStore('outbox'), req = records.get(id);
    req.onsuccess = () => {
      const record = req.result;
      if (!record || record.received !== record.chunks) { tx.abort(); return; }
      record.ready = true; record.hash = hash; records.put(record); done(true);
    };
  });
}
export function pendingUploads() {
  return transaction(['outbox'], 'readonly', (tx, done) => {
    const req = tx.objectStore('outbox').getAll(); req.onsuccess = () => done(req.result);
  });
}
export function readChunk(id, index) {
  return transaction(['parts'], 'readonly', (tx, done) => {
    const req = tx.objectStore('parts').get([id, index]); req.onsuccess = () => done(req.result?.data);
  });
}
export function removeUpload(id) {
  return transaction(['outbox', 'parts'], 'readwrite', tx => {
    tx.objectStore('outbox').delete(id);
    tx.objectStore('parts').delete(IDBKeyRange.bound([id, 0], [id, Number.MAX_SAFE_INTEGER]));
  });
}
export function clearQueue() {
  return transaction(['outbox', 'parts'], 'readwrite', tx => { tx.objectStore('outbox').clear(); tx.objectStore('parts').clear(); });
}
export async function legacyPages() {
  const db = await database();
  if (!db.objectStoreNames.contains('pages')) return [];
  return transaction(['pages'], 'readonly', (tx, done) => {
    const req = tx.objectStore('pages').getAll(); req.onsuccess = () => done(req.result);
  });
}

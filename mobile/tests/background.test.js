import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import vm from 'node:vm';
import {webcrypto} from 'node:crypto';
import * as core from '../../extension/core.js';

const source = await readFile(new URL('../background-source.mjs', import.meta.url), 'utf8');
const sender = {id: 'seen-test', url: 'https://example.invalid/feed', tab: {id: 7, incognito: false}, frameId: 0, documentId: 'document'};
const popup = {id: 'seen-test', url: 'safari-web-extension://seen-test/popup.html'};
function harness() {
  const records = new Map(), parts = new Map(), settings = {}, calls = [], scheduled = [];
  const state = {paused: false, tab: {...sender.tab, active: true, url: sender.url}, nativeReply: null};
  const store = {
    beginUpload: async r => records.set(r.id, {...r, received: 0, ready: false}),
    uploadInfo: async id => records.get(id),
    appendChunk: async (id, index, data) => {
      const r = records.get(id);
      if (r.ready || r.received !== index || index >= r.chunks) throw new Error('Order');
      parts.set(`${id}:${index}`, data); r.received++;
    },
    readChunk: async (id,index) => parts.get(`${id}:${index}`),
    finishUpload: async (id,hash) => { const r = records.get(id); if (r.received !== r.chunks) throw new Error('Incomplete'); r.ready = true; r.hash = hash; },
    pendingUploads: async () => [...records.values()],
    removeUpload: async id => records.delete(id),
  };
  const api = {
    runtime: {id: sender.id, getURL: path => 'safari-web-extension://seen-test/' + path,
      onMessage: {addListener: () => {}},
      sendNativeMessage: async (_app,message) => {
        calls.push(message);
        if (state.nativeReply) return state.nativeReply(message);
        if (message.op === 'status') return {ok: true, value: {paused: state.paused, queued: 0, paired: false}};
        if (message.op === 'begin') return {ok: true, value: {saved: false, nextIndex: 0}};
        if (message.op === 'commit') return {ok: true, value: {saved: true, snapshotId: message.snapshotId, hash: records.get(message.snapshotId).hash}};
        return {ok: true, value: {}};
      }},
    tabs: {get: async () => state.tab, sendMessage: async () => {}, onActivated: {addListener: () => {}}, onUpdated: {addListener: () => {}}},
    storage: {local: {get: async () => settings, set: async values => Object.assign(settings, values)}},
  };
  const context = vm.createContext({...core, ...store, browser: api, crypto: webcrypto, Uint8Array, atob, URL, setTimeout: fn => scheduled.push(fn)});
  vm.runInContext(source.replace(/^import .*;\n/gm,'') + '\nglobalThis.handlers = {handle, deliver};',context);
  return {...context.handlers, records, parts, state, calls, settings};
}
async function capture(h, text = '<html><input value="full DOM">ä🐘</html>') {
  const bytes = Buffer.from(JSON.stringify({html: text,shadowRoots: [],formState: []}));
  const begun = await h.handle({type: 'seen:begin',url: sender.url,title: 'Synthetic',bytes: bytes.length,chunks: Math.ceil(bytes.length/core.CHUNK_BYTES)},sender);
  for (let offset = 0, index = 0; offset < bytes.length; offset += core.CHUNK_BYTES,index++) {
    await h.handle({type: 'seen:chunk',uploadId: begun.id,index,data: bytes.subarray(offset,offset+core.CHUNK_BYTES).toString('base64')},sender);
  }
  await h.handle({type: 'seen:finish',uploadId: begun.id},sender);
  return begun.id;
}
test('private or unknown-private senders fail closed before native calls', async () => {
  for (const incognito of [true, undefined]) {
    const h = harness();
    assert.equal((await h.handle({type: 'seen:allowed'},{...sender,tab: {id: 7,incognito}})).allowed,false);
    assert.equal(h.calls.length,0);
  }
});
test('rechecks tab privacy, activity, pause, and web scope', async () => {
  for (const change of [{incognito: true},{incognito: undefined},{active: false},{url: 'about:blank'}]) {
    const h = harness(); Object.assign(h.state.tab,change);
    assert.equal((await h.handle({type: 'seen:allowed'},sender)).allowed,false);
  }
  const h = harness(); h.state.paused = true;
  assert.equal((await h.handle({type: 'seen:allowed'},sender)).allowed,false);
});
test('regular pages on arbitrary domains use the same capture rules', async () => {
  const h = harness();
  for (const url of ['https://one.invalid/','http://two.invalid/private-looking-path','https://three.invalid/feed?q=value']) {
    h.state.tab.url = url;
    assert.equal((await h.handle({type: 'seen:allowed'},{...sender,url})).allowed,true);
  }
});
test('subframes are rejected before capture', async () => {
  const h = harness();
  assert.equal((await h.handle({type: 'seen:allowed'},{...sender,frameId: 2})).allowed,false);
});
test('web pages cannot request settings, sync or pause; extension popup can', async () => {
  const h = harness();
  for (const type of ['seen:status','seen:sync','seen:pause']) await assert.rejects(h.handle({type},sender),/web pages/);
  assert.equal((await h.handle({type: 'seen:status'},popup)).paused,false);
  await assert.rejects(h.handle({type: 'seen:status'},{...popup,id: 'another-extension'}),/web pages/);
});
test('spoofed URL and invalid byte counts cannot begin captures', async () => {
  const h = harness();
  assert.equal((await h.handle({type: 'seen:begin',url: 'https://spoof.invalid/'},sender)).allowed,false);
  await assert.rejects(h.handle({type: 'seen:begin',url: sender.url,bytes: -1,chunks: 1},sender),/size/);
  assert.equal(h.records.size,0);
});
test('capture bytes, chunk order, hash and commit acknowledgements', async () => {
  const h = harness(); const id = await capture(h,'<html>' + 'ä🐘'.repeat(25000) + '</html>');
  assert.equal(h.records.get(id).ready,true);
  assert.equal(h.records.get(id).hash.length,64);
  await h.deliver();
  assert.equal(h.records.size,0);
  assert.ok(h.calls.some(c => c.op === 'commit' && c.snapshotId === id));
});
test('another document or private context cannot finish an upload', async () => {
  const h = harness(); const id = await capture(h);
  await assert.rejects(h.handle({type: 'seen:finish',uploadId: id},{...sender,documentId: 'other'}),/owner/);
  await assert.rejects(h.handle({type: 'seen:finish',uploadId: id},{...sender,tab: {...sender.tab,incognito: true}}),/not allowed/);
});
test('partial and wrong-size chunks cannot become ready records', async () => {
  const h = harness(); const start = await h.handle({type: 'seen:begin',url: sender.url,bytes: 100,chunks: 1},sender);
  await assert.rejects(h.handle({type: 'seen:finish',uploadId: start.id},sender),/Incomplete/);
  await h.handle({type: 'seen:chunk',uploadId: start.id,index: 0,data: Buffer.from('short').toString('base64')},sender);
  await assert.rejects(h.handle({type: 'seen:finish',uploadId: start.id},sender),/length/);
  assert.equal(h.records.get(start.id).ready,false);
});
test('missing, wrong or invalid-resume acknowledgements retain browser queue', async () => {
  for (const value of [{saved: true,snapshotId: 'wrong',hash: 'wrong'},{saved: false,nextIndex: -1},{saved: false,nextIndex: 50}]) {
    const h = harness(); const id = await capture(h);
    h.state.nativeReply = async () => ({ok: true,value});
    await assert.rejects(h.deliver());
    assert.equal(h.records.has(id),true);
  }
  const h = harness(); const id = await capture(h);
  h.state.nativeReply = async () => { throw new Error('Companion stopped'); };
  await assert.rejects(h.deliver(),/stopped/); assert.equal(h.records.has(id),true);
});
test('same collector, offline-only JS, all-web scope and private intent', async () => {
  const desktop = await readFile(new URL('../../extension/capture.js',import.meta.url),'utf8');
  assert.equal(await readFile(new URL('../extension/capture.js',import.meta.url),'utf8'),desktop);
  const manifest = JSON.parse(await readFile(new URL('../extension/manifest.json',import.meta.url),'utf8'));
  assert.deepEqual(manifest.host_permissions,['http://*/*','https://*/*']);
  assert.equal(manifest.incognito,'not_allowed');
  assert.equal(manifest.content_scripts[0].all_frames,false);
  assert.match(manifest.content_security_policy.extension_pages,/connect-src 'none'/);
  for (const code of [source,desktop]) assert.doesNotMatch(code,/\b(fetch|XMLHttpRequest|WebSocket|sendBeacon)\s*\(/);
});

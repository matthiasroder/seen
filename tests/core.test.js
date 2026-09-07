import test from 'node:test';
import assert from 'node:assert/strict';
import {readFile} from 'node:fs/promises';
import {runInNewContext} from 'node:vm';
import {webUrl, originAllowed, validChunk, senderKey} from '../extension/core.js';
import {DIAGNOSTIC_LIMIT, diagnosticRecord, appendDiagnostic} from '../extension/diagnostics.js';
test('URLs retain queries, fragments, and credentials without redaction', () => {
  assert.equal(webUrl('https://name:pass@example.test/login?session=value#part'), 'https://name:pass@example.test/login?session=value#part');
  for (const url of ['chrome://extensions','file:///tmp/a','data:text/html,a','invalid']) assert.equal(webUrl(url), null);
});
test('all HTTP and HTTPS websites and routes use the same rule', () => {
  const origins = ['http://*/*','https://*/*'];
  for (const url of ['https://one.test/feed','http://two.test/login','https://three.test/billing','https://four.test/inbox','https://five.test/settings']) assert.equal(originAllowed(url, origins), true);
  assert.equal(originAllowed('https://one.test/',[]), false);
});
test('exact grants never match suffix attackers or other schemes', () => {
  assert.equal(originAllowed('https://example.test/', ['https://example.test/*']), true);
  assert.equal(originAllowed('https://example.test.evil/', ['https://example.test/*']), false);
  assert.equal(originAllowed('http://example.test/', ['https://example.test/*']), false);
});
test('chunks are bounded and valid base64', () => {
  assert.equal(validChunk('aGVsbG8='),true);
  for (const input of ['', 'abc', '!abc', 'a'.repeat(90000), null]) assert.equal(validChunk(input),false);
});
test('queue owners bind to tab, frame, and document', () => {
  const sender={tab:{id:1},frameId:0,documentId:'doc'};
  assert.notEqual(senderKey(sender),senderKey({...sender,frameId:1}));
  assert.notEqual(senderKey(sender),senderKey({...sender,documentId:'next'}));
});
test('diagnostics are bounded, structured, and coalesce repeated document success', () => {
  let records = [];
  for (let i = 0; i < DIAGNOSTIC_LIMIT + 5; i++) records = appendDiagnostic(records,
    diagnosticRecord('capture-rejected', {reason: 'tab-inactive', tabId: i, frameId: 0}, i));
  assert.equal(records.length, DIAGNOSTIC_LIMIT);
  assert.equal(records[0].tabId, 5);
  records = appendDiagnostic(records, diagnosticRecord('capture-saved',
    {url: 'https://example.test/one', tabId: 1, frameId: 0, documentId: 'doc', bytes: 10}, 100), true);
  records = appendDiagnostic(records, diagnosticRecord('capture-saved',
    {url: 'https://example.test/two', tabId: 1, frameId: 0, documentId: 'doc', bytes: 20}, 101), true);
  const saved = records.filter(item => item.event === 'capture-saved' && item.documentId === 'doc');
  assert.equal(saved.length, 1);
  assert.equal(saved[0].url, 'https://example.test/two');
  assert.equal(saved[0].bytes, 20);
  records = appendDiagnostic(records, diagnosticRecord('capture-rejected',
    {reason: 'tab-inactive', stage: 'start', tabId: 2, frameId: 0, documentId: 'other'}, 102), true);
  records = appendDiagnostic(records, diagnosticRecord('capture-rejected',
    {reason: 'url-mismatch', stage: 'begin', tabId: 2, frameId: 0, documentId: 'other'}, 103), true);
  assert.equal(records.filter(item => item.event === 'capture-rejected' && item.documentId === 'other').length, 2);
});
test('capture exits quietly after Chrome invalidates an old extension context', async () => {
  const source = await readFile(new URL('../extension/capture.js', import.meta.url), 'utf8');
  assert.doesNotThrow(() => runInNewContext(source, {chrome: {}}));
});
test('visible-item tracker stays generic and exits without a valid extension context', async () => {
  const source = await readFile(new URL('../extension/item-attention.js', import.meta.url), 'utf8');
  assert.match(source, /article,\[role=/);
  assert.match(source, /MIN_VISIBLE = 0\.5/);
  assert.doesNotMatch(source, /linkedin|twitter|facebook/i);
  assert.doesNotThrow(() => runInNewContext(source, {chrome: {}, window: {}}));
});
test('manifest excludes Incognito and enables local helper with no domain rules', async () => {
  const manifest=JSON.parse(await readFile(new URL('../extension/manifest.json',import.meta.url)));
  assert.equal(manifest.incognito,'not_allowed');
  assert.deepEqual(manifest.host_permissions.sort(),['http://*/*','https://*/*']);
  assert.ok(manifest.permissions.includes('nativeMessaging'));
  assert.ok(manifest.permissions.includes('idle'));
  assert.ok(manifest.content_security_policy.extension_pages.includes("connect-src 'none'"));
});

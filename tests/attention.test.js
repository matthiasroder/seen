import test from 'node:test';
import assert from 'node:assert/strict';
import {createAttentionTracker} from '../extension/attention.js';

test('foreground attention follows activation, pauses, routes, and checkpoints without creating snapshots', async () => {
  let now = 1000, nextId = 0;
  const values = {}, stored = new Map();
  const storage = {
    async get(key) { return {[key]: values[key]}; },
    async set(update) { Object.assign(values, structuredClone(update)); },
    async remove(key) { delete values[key]; }
  };
  const tracker = createAttentionTracker(storage, async visit => { stored.set(visit.id, structuredClone(visit)); },
    () => now, () => `00000000-0000-0000-0000-${String(++nextId).padStart(12, '0')}`);
  const first = {id: 7, url: 'https://example.test/one', title: 'One'};
  await tracker.activate(first);
  now = 2500; await tracker.pauseAll();
  assert.equal([...stored.values()][0].focusedMs, 1500);
  now = 3000; await tracker.activate(first);
  now = 4000; await tracker.navigate({...first, url: 'https://example.test/two', title: 'Two'}, true);
  const visits = [...stored.values()].sort((a, b) => a.startedAt - b.startedAt);
  assert.equal(visits[0].focusedMs, 2500);
  assert.equal(visits[0].endedAt, 4000);
  now = 5000; await tracker.checkpoint();
  assert.equal([...stored.values()].find(visit => visit.url.endsWith('/two')).focusedMs, 1000);
  await tracker.clear();
  assert.equal(values.attentionState, undefined);
});

test('foreground attention queues locally while the helper is unavailable', async () => {
  let now = 1000, online = false;
  const values = {}, stored = new Map();
  const storage = {
    async get(key) { return {[key]: values[key]}; },
    async set(update) { Object.assign(values, structuredClone(update)); },
    async remove(key) { delete values[key]; }
  };
  const tracker = createAttentionTracker(storage, async visit => {
    if (!online) throw new Error('offline');
    stored.set(visit.id, structuredClone(visit));
  }, () => now, () => '00000000-0000-0000-0000-000000000001');
  await tracker.activate({id: 7, url: 'https://example.test/', title: 'Example'});
  now = 2000; await tracker.pauseAll();
  assert.equal(stored.size, 0);
  assert.ok(values.attentionState.current['7']);
  online = true; await tracker.checkpoint();
  assert.equal([...stored.values()][0].focusedMs, 1000);
});

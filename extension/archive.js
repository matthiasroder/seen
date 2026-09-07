import {api, element, dateLabel, showError, paintPause, safeSourceLink} from './ui.js';

let state, selected, selectedDay = '', activeQuery = '', mode = 'days', statusPromise, revision = 0, offset = 0, currentPayload;
const search = document.querySelector('#search');
const dayFormat = new Intl.DateTimeFormat(undefined, {weekday: 'long', month: 'long', day: 'numeric', year: 'numeric'});
const weekdayFormat = new Intl.DateTimeFormat(undefined, {weekday: 'long'});
const monthFormat = new Intl.DateTimeFormat(undefined, {month: 'long', year: 'numeric'});
const localDay = value => new Date(value + 'T12:00:00');

function diagnosticText(records) {
  if (!records?.length) return 'No capture events recorded.';
  return records.slice(-30).reverse().map(item => {
    const parts = [new Date(item.at).toLocaleTimeString(), item.event];
    if (item.stage) parts.push('stage=' + item.stage);
    if (item.reason) parts.push('reason=' + item.reason);
    if (item.frameId !== undefined) parts.push('frame=' + item.frameId);
    if (item.bytes !== undefined) parts.push('bytes=' + item.bytes);
    const lines = [parts.join(' · ')];
    if (item.url) lines.push(item.url);
    if (item.senderUrl && item.senderUrl !== item.url) lines.push('Chrome sender: ' + item.senderUrl);
    if (item.error) lines.push(item.error);
    return lines.join('\n');
  }).join('\n\n');
}

function download(value, name) {
  const url = URL.createObjectURL(new Blob([JSON.stringify(value)], {type: 'application/json'}));
  const link = element('a'); link.href = url; link.download = name; link.click();
  setTimeout(() => URL.revokeObjectURL(url), 1000);
}
async function readSnapshot(id) {
  const meta = await api('get', {id});
  const bytes = new Uint8Array(meta.bytes);
  for (let at = 0; at < bytes.length;) {
    const chunk = await api('read', {id, offset: at});
    const binary = atob(chunk.data);
    if (!binary.length || at + binary.length > bytes.length) throw new Error('Incomplete database read.');
    for (let i = 0; i < binary.length; i++) bytes[at++] = binary.charCodeAt(i);
  }
  return {meta, dom: JSON.parse(new TextDecoder('utf-8', {fatal: true}).decode(bytes))};
}
async function select(page) {
  selected = page.id;
  history.replaceState(null, '', '#' + encodeURIComponent(page.id));
  const reader = document.querySelector('#reader'); reader.replaceChildren(element('p', 'muted', 'Reading DOM from SQLite…'));
  const capture = await readSnapshot(page.id);
  if (selected !== page.id) return;
  currentPayload = capture;
  for (const button of document.querySelectorAll('.page-item')) button.setAttribute('aria-pressed', String(button.dataset.id === selected));
  const meta = capture.meta, header = element('header', 'reader-header'), details = element('div', 'reader-meta');
  details.append(element('span', 'eyebrow', new URL(meta.url).hostname || meta.url), element('span', 'muted', dateLabel(meta.lastSeen)));
  const actions = element('div', 'reader-actions');
  const sourceUrl = safeSourceLink(meta.url);
  if (sourceUrl) { const source = element('a', 'secondary', 'Open original ↗'); source.href = sourceUrl; source.target = '_blank'; source.rel = 'noopener noreferrer'; actions.append(source); }
  const save = element('button', 'secondary', 'Export DOM'); save.addEventListener('click', () => download(capture, 'seen-' + page.id + '.json')); actions.append(save);
  const remove = element('button', 'quiet', 'Delete'); remove.addEventListener('click', async () => {
    try { await api('delete', {id: page.id}); selected = undefined; currentPayload = undefined; reader.replaceChildren(element('p', 'reader-notice', 'Snapshot removed.')); await refreshLibrary(); await status(); } catch (error) { showError(error); }
  }); actions.append(remove);
  header.append(details, element('h2', '', meta.title || meta.url), actions);
  const code = element('pre', 'dom-source', capture.dom.html.slice(0, 200000));
  const supplementary = element('details', 'dom-details');
  supplementary.append(element('summary', '', 'Shadow DOM and live form state'), element('pre', 'dom-source', JSON.stringify({shadowRoots: capture.dom.shadowRoots, formState: capture.dom.formState}, null, 2).slice(0, 200000)));
  const note = element('p', 'capture-note', 'Complete snapshot stored. Source preview limited to 200,000 characters; export includes everything. Scripts stay inert and referenced resources are never loaded here.');
  reader.replaceChildren(header, code, supplementary, note); reader.scrollTop = 0;
}

function libraryHeading(title, back, context = 'LAST SEEN') {
  document.querySelector('#library-title').textContent = title;
  document.querySelector('#all-days').hidden = !back;
  document.querySelector('#list-context').textContent = context;
}

async function showDays() {
  const current = ++revision;
  mode = 'days'; selectedDay = ''; activeQuery = ''; offset = 0; search.value = '';
  libraryHeading('Open a day.', false, 'CAPTURES');
  document.querySelector('#count').textContent = 'Loading days…';
  document.querySelector('#more').hidden = true;
  const result = await api('days');
  if (current !== revision) return;
  const list = document.querySelector('#page-list'); list.replaceChildren();
  if (!result.items.length) {
    const empty = element('div', 'empty-state');
    empty.append(element('h2', '', 'No days yet.'), element('p', '', 'Browse normally. Seen will place captured pages here by day.'));
    list.append(empty);
  }
  for (const item of result.items) {
    const date = localDay(item.day), button = element('button', 'day-item'); button.dataset.day = item.day;
    const calendar = element('span', 'day-date', String(date.getDate()));
    const copy = element('span', 'day-copy');
    copy.append(element('span', 'eyebrow', weekdayFormat.format(date)), element('strong', '', monthFormat.format(date)));
    const count = element('span', 'day-count', item.snapshots + (item.snapshots === 1 ? ' capture' : ' captures'));
    button.append(calendar, copy, count);
    button.addEventListener('click', () => openDay(item.day).catch(showError)); list.append(button);
  }
  document.querySelector('#count').textContent = result.items.length + (result.items.length === 1 ? ' day' : ' days');
}

async function openDay(day) {
  mode = 'day'; selectedDay = day; activeQuery = ''; search.value = '';
  libraryHeading(dayFormat.format(localDay(day)), true);
  await loadSnapshots();
}

async function searchArchive() {
  const query = search.value.trim();
  if (!query) return showDays();
  mode = 'search'; selectedDay = ''; activeQuery = query;
  libraryHeading('Search results.', true);
  await loadSnapshots();
}

async function loadSnapshots(append = false) {
  const current = ++revision;
  if (!append) offset = 0;
  if (!append) document.querySelector('#count').textContent = 'Loading snapshots…';
  const result = await api('list', {query: activeQuery, day: selectedDay, offset});
  if (current !== revision) return;
  const list = document.querySelector('#page-list');
  if (!append) list.replaceChildren();
  if (!result.items.length && !append) {
    const empty = element('div', 'empty-state');
    empty.append(element('h2', '', 'Nothing here.'), element('p', '', activeQuery ? 'No matching DOM snapshot.' : 'No captures were stored on this day.'));
    list.append(empty);
  }
  for (const page of result.items) {
    const button = element('button', 'page-item'); button.dataset.id = page.id;
    const meta = element('div', 'item-meta');
    let host; try { host = new URL(page.url).hostname || page.url; } catch { host = page.url; }
    meta.append(element('span', '', host), element('time', '', dateLabel(page.lastSeen)));
    button.append(meta, element('h2', '', page.title || page.url), element('p', 'excerpt', (page.frameId ? 'Frame ' + page.frameId : 'Main document') + ' · ' + Math.ceil(page.bytes / 1024) + ' KB'));
    button.addEventListener('click', () => select(page).catch(showError)); list.append(button);
  }
  offset += result.items.length;
  document.querySelector('#count').textContent = offset + ' snapshots shown';
  document.querySelector('#more').hidden = !result.more;
}

function refreshLibrary() { return mode === 'days' ? showDays() : loadSnapshots(); }

async function refreshStatus() {
  state = await api('status'); paintPause(state.preferences.paused);
  const node = document.querySelector('#database-status');
  node.textContent = state.database ? state.database.path + ' · ' + state.database.snapshots + ' snapshots · ' + state.queued + ' queued' :
    'Local helper is not connected. Complete snapshots stay queued in Chrome until it is installed.';
  document.querySelector('#setup-command').textContent = 'python3 native/install.py ' + state.extensionId;
  document.querySelector('#queue-status').textContent = state.queued + ' awaiting SQLite · ' + state.incomplete + ' incomplete transfers';
  document.querySelector('#diagnostic-file').textContent = state.database?.diagnosticLog ? 'File: ' + state.database.diagnosticLog : '';
  document.querySelector('#diagnostic-log').textContent = diagnosticText(state.diagnostics);
  if (state.lastError) showError(new Error(state.lastError));
}
function status() {
  if (!statusPromise) statusPromise = refreshStatus().finally(() => { statusPromise = undefined; });
  return statusPromise;
}
function setSettings(open) {
  document.querySelector('#settings').hidden = !open;
  document.querySelector('#settings-toggle').setAttribute('aria-expanded', String(open));
}
document.querySelector('#settings-toggle').addEventListener('click', () => setSettings(document.querySelector('#settings').hidden));
document.querySelector('#settings-close').addEventListener('click', () => setSettings(false));
document.querySelector('#pause').addEventListener('click', async () => { try { await api('pause', {paused: !state.preferences.paused}); await status(); } catch (error) { showError(error); } });
document.querySelector('#more').addEventListener('click', () => loadSnapshots(true).catch(showError));
document.querySelector('#all-days').addEventListener('click', () => showDays().catch(showError));
document.querySelector('#retry').addEventListener('click', async () => { try { await api('sync'); await status(); await refreshLibrary(); } catch (error) { showError(error); } });
document.querySelector('#clear-diagnostics').addEventListener('click', async () => { try { await api('diagnostics-clear'); await status(); } catch (error) { showError(error); } });
document.querySelector('#search-form').addEventListener('submit', event => { event.preventDefault(); searchArchive().catch(showError); });
document.querySelector('#export').addEventListener('click', () => {
  if (currentPayload) download(currentPayload, 'seen-' + selected + '.json');
  else showError(new Error('Select a snapshot first. Other applications can read the complete SQLite database directly.'));
});
document.querySelector('#legacy').addEventListener('click', async () => { try { download(await api('legacy'), 'seen-legacy-text.json'); } catch (error) { showError(error); } });
document.querySelector('#clear').addEventListener('click', () => document.querySelector('#confirm-clear').showModal());
document.querySelector('#confirm-clear').addEventListener('close', async event => {
  if (event.target.returnValue !== 'clear') return;
  try { await api('pause', {paused: true}); await api('clear'); selected = undefined; currentPayload = undefined;
    document.querySelector('#reader').replaceChildren(element('p', 'reader-notice', 'DOM archive cleared. Capture is paused.'));
    await status(); await showDays();
  } catch (error) { showError(error); }
});
document.addEventListener('keydown', event => {
  if (event.key === '/' && !['INPUT', 'TEXTAREA'].includes(document.activeElement.tagName)) { event.preventDefault(); search.focus(); }
  if (event.key === 'Escape') setSettings(false);
});
window.addEventListener('focus', () => status().catch(showError));
async function init() {
  let loadError;
  const loading = showDays().catch(error => { loadError = error; });
  await status();
  if (state.database) {
    await loading;
    if (loadError) throw loadError;
    let id; try { id = decodeURIComponent(location.hash.slice(1)); } catch {}
    if (id) await select({id});
  } else { await loading; setSettings(true); document.querySelector('#count').textContent = 'Connect local database'; }
}
init().catch(showError);

import {api, element, dateLabel, showError, paintPause} from './ui.js';
import {originAllowed} from './core.js';
let state;
async function render() {
  state = await api('status'); paintPause(state.preferences.paused);
  const [tab] = await chrome.tabs.query({active: true, currentWindow: true});
  let site; try { site = new URL(tab.url).hostname; } catch { site = ''; }
  document.querySelector('#hostname').textContent = site || 'Not a web page';
  const latest = [...(state.diagnostics || [])].reverse().find(item => item.tabId === tab?.id && item.frameId === 0 &&
    (item.url === tab?.url || item.senderUrl === tab?.url));
  const note = tab?.incognito ? 'Incognito is never recorded.' : state.preferences.paused ? 'Capture is paused everywhere.' :
    !originAllowed(tab?.url, state.origins) ? 'Chrome does not allow capture here.' :
    !state.database ? 'DOM snapshots queue locally until the SQLite helper is connected. Open archive for setup.' :
    latest?.event === 'capture-rejected' ? 'Capture rejected: ' + latest.reason + '. Open archive Settings for details.' :
    ['capture-error', 'capture-start-error', 'delivery-error'].includes(latest?.event) ? 'Capture failed. Open archive Settings for details.' :
    latest?.event === 'capture-saved' ? 'A DOM checkpoint for this URL reached SQLite.' :
    latest ? 'Capture script reported from this URL; no saved checkpoint yet.' :
    'Capture is permitted here. Open archive Settings for the local event log.';
  document.querySelector('#site-note').textContent = note;
  const list = document.querySelector('#recent-list'); list.replaceChildren();
  if (state.database) {
    const records = (await api('list')).items.slice(0, 3);
    if (!records.length) list.append(element('p', 'muted empty-small', 'Nothing saved yet. Browse as usual.'));
    for (const page of records) {
      const link = element('a', 'recent-item'); link.href = 'archive.html#' + encodeURIComponent(page.id); link.target = '_blank';
      link.append(element('strong', '', page.title || page.url), element('span', 'muted', dateLabel(page.lastSeen))); list.append(link);
    }
  } else list.append(element('p', 'muted empty-small', state.queued + ' snapshots awaiting the local helper.'));
  if (state.lastError) showError(new Error(state.lastError));
}
document.querySelector('#pause').addEventListener('click', async () => {
  try { await api('pause', {paused: !state.preferences.paused}); await render(); } catch (error) { showError(error); }
});
render().catch(showError);

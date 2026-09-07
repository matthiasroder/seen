export async function api(type, payload = {}) {
  const response = await chrome.runtime.sendMessage({type: `seen:${type}`, ...payload});
  if (!response?.ok) throw new Error(response?.error || 'Could not reach Seen. Try reopening this window.');
  return response.value;
}

const dateFormatter = new Intl.DateTimeFormat(undefined, {month: 'short', day: 'numeric', hour: '2-digit', minute: '2-digit'});

export function element(tag, className, text) {
  const node = document.createElement(tag);
  if (className) node.className = className;
  if (text !== undefined) node.textContent = text;
  return node;
}

export function dateLabel(time) {
  return dateFormatter.format(time);
}

export function showError(error) {
  const node = document.querySelector('#error');
  node.textContent = error.message || String(error); node.hidden = false;
}

export function paintPause(paused) {
  const node = document.querySelector('#pause');
  node.textContent = paused ? 'Ⅱ Paused' : '● Ready';
  node.setAttribute('aria-pressed', String(paused));
  node.title = paused ? 'Resume on regular websites' : 'Pause capture on all sites';
}

export function safeSourceLink(url) {
  try { const u = new URL(url); return ['https:', 'http:'].includes(u.protocol) && !u.username && !u.password ? u.href : null; } catch { return null; }
}

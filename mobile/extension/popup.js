const api = globalThis.browser || globalThis.chrome;
let paused;
async function request(type, rest = {}) {
  const result = await api.runtime.sendMessage({type, ...rest});
  if (!result.ok) throw new Error(result.error);
  return result.value;
}
async function refresh() {
  const data = await request('seen:status'); paused = data.paused;
  document.querySelector('#status').textContent = `${paused ? 'Paused' : 'Capture enabled'} · ${data.queued} on iPhone · ${data.browserQueued} awaiting handoff`;
  document.querySelector('#detail').textContent = data.browserError || data.lastError || (data.paired ? 'Mac paired. Open Seen to sync the full queue.' : 'Open Seen on your iPhone to pair your Mac.');
  const pause = document.querySelector('#pause'); pause.disabled = false; pause.textContent = paused ? 'Resume capture' : 'Pause capture';
  document.querySelector('#sync').disabled = !data.paired;
}
function failed(error) { document.querySelector('#status').textContent = error.message; }
document.querySelector('#pause').onclick = () => request('seen:pause', {paused: !paused}).then(refresh).catch(failed);
document.querySelector('#sync').onclick = () => request('seen:sync').then(refresh).catch(failed);
refresh().catch(failed);

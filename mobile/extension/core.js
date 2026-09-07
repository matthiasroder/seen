export const CHUNK_BYTES = 65536;
export const NATIVE_HOST = 'com.seen.archive';

// Scope only. No hostname, route, content, or sensitivity rules.
export function webUrl(value) {
  try { const url = new URL(value); return ['http:', 'https:'].includes(url.protocol) ? url.href : null; }
  catch { return null; }
}
export function originAllowed(value, origins) {
  const valueUrl = webUrl(value);
  if (!valueUrl) return false;
  const url = new URL(valueUrl);
  return origins.includes(url.protocol + '//*/*') || origins.includes(url.origin + '/*');
}
export function validChunk(data) {
  return typeof data === 'string' && data.length > 0 && data.length <= 4 * Math.ceil(CHUNK_BYTES / 3) &&
    /^(?:[A-Za-z0-9+/]{4})*(?:[A-Za-z0-9+/]{2}==|[A-Za-z0-9+/]{3}=)?$/.test(data);
}
export function senderKey(sender) {
  return sender.tab.id + ':' + sender.frameId + ':' + (sender.documentId || sender.url);
}

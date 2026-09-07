export const DIAGNOSTIC_LIMIT = 60;

const STRING_LIMITS = {
  event: 64, stage: 32, reason: 80, error: 500,
  url: 2048, senderUrl: 2048, documentId: 128, visibility: 16
};

export function diagnosticRecord(event, values = {}, at = Date.now()) {
  const record = {at: Number.isFinite(at) ? at : Date.now(), event: String(event).slice(0, STRING_LIMITS.event)};
  for (const [key, limit] of Object.entries(STRING_LIMITS)) {
    if (key === 'event' || typeof values[key] !== 'string' || !values[key]) continue;
    record[key] = values[key].slice(0, limit);
  }
  for (const key of ['tabId', 'frameId', 'bytes']) {
    if (Number.isSafeInteger(values[key]) && values[key] >= 0) record[key] = values[key];
  }
  return record;
}

export function appendDiagnostic(records, entry, coalesce = false) {
  let next = Array.isArray(records) ? records.filter(item => item && typeof item === 'object') : [];
  if (coalesce) next = next.filter(item => !(item.event === entry.event && item.tabId === entry.tabId &&
    item.frameId === entry.frameId && item.documentId === entry.documentId && item.stage === entry.stage &&
    item.reason === entry.reason));
  next.push(entry);
  return next.slice(-DIAGNOSTIC_LIMIT);
}

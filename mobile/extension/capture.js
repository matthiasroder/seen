(() => {
  'use strict';
  const runtime = globalThis.chrome?.runtime;
  // Chrome can leave page worlds alive after an unpacked-extension reload while
  // removing their extension APIs. Do nothing until a refresh creates a valid context.
  if (!runtime?.sendMessage || !runtime?.onMessage) return;
  if (window.top !== window) return;
  if (globalThis.__seenDOMCapture) { globalThis.__seenDOMCapture.start(); return; }
  const CHUNK_BYTES = 65536;
  const QUIET_MS = 2500, MAX_WAIT_MS = 10000;
  const NOISE_TOKEN = /(^|[\s_-])(ad|ads|advert|advertisement|sponsored|cookie|consent|toast)(?=$|[\s_-])/i;
  let running = false, dirty = true, sending = false, timer, observer, lastSnapshot = '', lastMeaning = '', lastUrl = '', routeUrl = location.href;
  let retryNeeded = false, starting = false, generation = 0, dirtySince = 0, observedRoots = new WeakSet();
  const request = async message => {
    const result = await runtime.sendMessage(message);
    if (!result?.ok) throw new Error(result?.error || 'Seen is unavailable.');
    return result.value;
  };
  const diagnostic = (event, values = {}) => request({type: 'seen:diagnostic', event, url: location.href,
    visibility: document.visibilityState, ...values}).catch(() => false);
  function snapshotDOM() {
    const serializer = new XMLSerializer();
    const htmlFor = node => node.nodeType === Node.ELEMENT_NODE ? node.outerHTML : serializer.serializeToString(node);
    const shadowRoots = [], formState = [];
    const stack = [{node: document, path: []}];
    while (stack.length) {
      const {node, path} = stack.pop();
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (node instanceof HTMLInputElement) formState.push({path, value: node.value, checked: node.checked, indeterminate: node.indeterminate});
        else if (node instanceof HTMLTextAreaElement) formState.push({path, value: node.value});
        else if (node instanceof HTMLSelectElement) formState.push({path, selected: Array.from(node.options, (option, i) => option.selected ? i : -1).filter(i => i >= 0)});
        const shadow = node instanceof HTMLElement ? (chrome.dom?.openOrClosedShadowRoot(node) || node.shadowRoot) : node.shadowRoot;
        if (shadow) {
          shadowRoots.push({path, mode: shadow.mode, html: Array.from(shadow.childNodes, htmlFor).join('')});
          if (!observedRoots.has(shadow)) { observer?.observe(shadow, {subtree: true, childList: true, attributes: true, characterData: true}); observedRoots.add(shadow); }
          stack.push({node: shadow, path: [...path, 'shadow']});
        }
        if (node instanceof HTMLTemplateElement) stack.push({node: node.content, path: [...path, 'template']});
      }
      for (let i = node.childNodes.length - 1; i >= 0; i--) stack.push({node: node.childNodes[i], path: [...path, i]});
    }
    // Read the live tree; never clone/insert nodes or resolve resource URLs.
    return {html: Array.from(document.childNodes, htmlFor).join(''), shadowRoots, formState};
  }
  function ignoredForMeaning(element) {
    if (['SCRIPT', 'STYLE', 'NOSCRIPT', 'TEMPLATE', 'IFRAME', 'SVG', 'CANVAS'].includes(element.tagName)) return true;
    if (element.hidden || element.getAttribute('aria-hidden') === 'true') return true;
    const role = element.getAttribute('role');
    if (['status', 'alert', 'timer', 'marquee'].includes(role)) return true;
    const live = element.getAttribute('aria-live');
    if (live && live !== 'off' && (element.textContent || '').length < 256) return true;
    if (element.hasAttribute('data-ad') || element.hasAttribute('data-ad-slot') || element.hasAttribute('data-ad-unit')) return true;
    const labels = [element.id, typeof element.className === 'string' ? element.className : '', element.getAttribute('aria-label') || ''].join(' ');
    return NOISE_TOKEN.test(labels);
  }
  function meaningfulState() {
    const parts = ['url', location.href, 'title', document.title];
    const stack = [document.documentElement];
    while (stack.length) {
      const node = stack.pop();
      if (!node) continue;
      if (node.nodeType === Node.TEXT_NODE) {
        const value = (node.nodeValue || '').replace(/\s+/g, ' ').trim();
        if (value) parts.push('text', value);
        continue;
      }
      if (node.nodeType !== Node.ELEMENT_NODE && node.nodeType !== Node.DOCUMENT_FRAGMENT_NODE) continue;
      if (node.nodeType === Node.ELEMENT_NODE) {
        if (ignoredForMeaning(node)) continue;
        if (node instanceof HTMLAnchorElement) parts.push('link', node.href);
        else if (node instanceof HTMLInputElement) {
          if (!['password', 'file', 'hidden'].includes(node.type)) parts.push('input', node.type, node.value, node.checked ? '1' : '0');
        } else if (node instanceof HTMLTextAreaElement) parts.push('textarea', node.value);
        else if (node instanceof HTMLSelectElement) parts.push('select', String(node.selectedIndex));
        const shadow = node instanceof HTMLElement ? (chrome.dom?.openOrClosedShadowRoot(node) || node.shadowRoot) : node.shadowRoot;
        if (shadow) stack.push(shadow);
      }
      for (let i = node.childNodes.length - 1; i >= 0; i--) stack.push(node.childNodes[i]);
    }
    return parts.join('\u001f');
  }
  function schedule(delay = QUIET_MS) {
    const now = Date.now();
    if (!dirty || !dirtySince) dirtySince = now;
    dirty = true;
    if (!running || sending || document.visibilityState !== 'visible') return;
    clearTimeout(timer);
    const due = Math.min(now + delay, dirtySince + MAX_WAIT_MS);
    timer = setTimeout(() => { timer = undefined; capture(); }, Math.max(0, due - now));
  }
  async function capture(departing = false) {
    if (!running || sending || !dirty) return;
    if (!departing && document.visibilityState !== 'visible') return;
    sending = true; dirty = false;
    try {
      const url = location.href;
      const meaning = meaningfulState();
      if (url === lastUrl && meaning === lastMeaning && !retryNeeded) return;
      const serialized = JSON.stringify(snapshotDOM());
      if (url === lastUrl && serialized === lastSnapshot && !retryNeeded) { lastMeaning = meaning; return; }
      const bytes = new TextEncoder().encode(serialized);
      const begin = await request({type: 'seen:begin', url, title: document.title, bytes: bytes.length, chunks: Math.ceil(bytes.length / CHUNK_BYTES), capturedAt: Date.now()});
      if (!begin.allowed) { dirty = true; retryNeeded = true; return; }
      for (let start = 0, index = 0; start < bytes.length; start += CHUNK_BYTES, index++) {
        const part = bytes.subarray(start, start + CHUNK_BYTES);
        let binary = ''; for (const byte of part) binary += String.fromCharCode(byte);
        await request({type: 'seen:chunk', uploadId: begin.id, index, data: btoa(binary)});
      }
      await request({type: 'seen:finish', uploadId: begin.id});
      lastSnapshot = serialized; lastMeaning = meaning; lastUrl = url; retryNeeded = false;
    } catch (error) {
      dirty = true; retryNeeded = true;
      request({type: 'seen:capture-error', error: String(error.message || error)}).catch(() => {});
    } finally {
      sending = false;
      if (dirty && !retryNeeded && running && document.visibilityState === 'visible') schedule();
    }
  }
  async function start() {
    if (running) { schedule(0); return; }
    if (starting) return;
    starting = true;
    const ownGeneration = generation;
    let permission;
    try { permission = await request({type: 'seen:allowed'}); }
    catch (error) { starting = false; diagnostic('capture-start-error', {stage: 'start', error: String(error.message || error)}); return; }
    starting = false;
    if (ownGeneration !== generation || !permission.allowed) return;
    running = true; observedRoots = new WeakSet();
    diagnostic('capture-started');
    observer = new MutationObserver(() => schedule());
    observer.observe(document, {subtree: true, childList: true, attributes: true, characterData: true});
    schedule();
  }
  function stop() { generation++; starting = false; running = false; observer?.disconnect(); clearTimeout(timer); timer = undefined; }
  function event() {
    if (location.href !== routeUrl) { routeUrl = location.href; lastSnapshot = ''; lastMeaning = ''; lastUrl = ''; }
    if (!running) start(); else schedule();
  }
  runtime.onMessage.addListener(message => {
    if (message.type === 'seen:stop') stop();
    if (message.type === 'seen:start' || message.type === 'seen:route') event();
  });
  document.addEventListener('input', event, true);
  document.addEventListener('change', event, true);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') event(); });
  window.addEventListener('pagehide', () => {
    dirty = true;
    if (!dirtySince) dirtySince = Date.now();
    capture(true);
  });
  for (const name of ['focus', 'pageshow', 'popstate', 'hashchange']) window.addEventListener(name, event);
  globalThis.__seenDOMCapture = {start, stop};
  diagnostic('script-loaded').finally(start);
})();

import http from 'node:http';
import {readFile} from 'node:fs/promises';
import {resolve, extname} from 'node:path';
const root = new URL('../', import.meta.url).pathname;
const mime = {'.html':'text/html', '.js':'text/javascript', '.css':'text/css', '.json':'application/json'};
http.createServer(async (req, res) => {
  const path = new URL(req.url, 'http://localhost').pathname;
  const file = resolve(root, '.' + path);
  if (!file.startsWith(root) || !['/extension/', '/tests/'].some(prefix => path.startsWith(prefix))) { res.writeHead(404).end(); return; }
  try {
    let data = await readFile(file);
    // The demo adapter exists only on this local test server, never in the packaged extension.
    if (path === '/extension/archive.html' || path === '/extension/popup.html') data = Buffer.from(data.toString().replace('<head>', '<head><script src="/tests/demo-adapter.js"></script>'));
    res.writeHead(200, {'Content-Type': mime[extname(file)] || 'application/octet-stream', 'Cache-Control':'no-store'}).end(data);
  } catch { res.writeHead(404).end(); }
}).listen(4318, '127.0.0.1', () => console.log('Seen test preview: http://127.0.0.1:4318/extension/archive.html'));

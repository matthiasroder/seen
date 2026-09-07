import {readdir, readFile, stat} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const root = new URL('../extension/', import.meta.url);
let total = 0;
for (const name of await readdir(root)) {
  const file = new URL(name, root);
  total += (await stat(file)).size;
  if (name.endsWith('.js')) {
    execFileSync(process.execPath, ['--check', file.pathname]);
    const source = await readFile(file, 'utf8');
    if (/\b(fetch|XMLHttpRequest|WebSocket|EventSource|sendBeacon)\s*\(/.test(source)) throw new Error(`Network API found in ${name}`);
    if (/\.innerHTML\s*=|\.outerHTML\s*=|document\.write\s*\(/.test(source)) throw new Error(`Unsafe HTML rendering found in ${name}`);
    if (/isSensitiveRoute|linkedin\.com|facebook\.com|twitter\.com/.test(source)) throw new Error(`Site/content-specific capture rule found in ${name}`);
  }
}
const manifest = JSON.parse(await readFile(new URL('manifest.json', root)));
if (JSON.stringify([...(manifest.host_permissions || [])].sort()) !== JSON.stringify(['http://*/*', 'https://*/*'])) throw new Error('Capture must cover HTTP and HTTPS websites.');
if (manifest.incognito !== 'not_allowed') throw new Error('Incognito must be disabled at the manifest level.');
if (!manifest.permissions.includes('nativeMessaging')) throw new Error('Local database helper permission missing.');
if (manifest.optional_host_permissions?.length || manifest.content_scripts?.length) throw new Error('Use granted all-web access and dynamic capture registration.');
if (!manifest.content_security_policy.extension_pages.includes("connect-src 'none'")) throw new Error('Missing network-denying CSP.');
for (const [kind, sizes] of [['icons', [16, 32, 48, 128]], ['action', [16, 24, 32]]]) {
  const icons = kind === 'action' ? manifest.action?.default_icon : manifest.icons;
  for (const size of sizes) {
    const path = icons?.[size];
    if (!path || !/^icon-\d+\.png$/.test(path)) throw new Error(`Missing ${kind} icon: ${size}`);
    const png = await readFile(new URL(path, root));
    if (png.length < 24 || png.subarray(0, 8).toString('hex') !== '89504e470d0a1a0a' ||
        png.toString('ascii', 12, 16) !== 'IHDR' || png.readUInt32BE(16) !== size || png.readUInt32BE(20) !== size) {
      throw new Error(`Invalid PNG or dimensions: ${path}`);
    }
  }
}
if (total > 100000) throw new Error(`Runtime exceeds 100 KB budget: ${total}`);
const host = await readFile(new URL('../native/host.py', root), 'utf8');
if (/import\s+(?:socket|requests|http|urllib)|urlopen\s*\(/.test(host)) throw new Error('Network library in local helper.');
console.log(`OK: syntax, equal-site capture, Incognito exclusion, icons, network API audit, inert DOM viewer. Extension runtime: ${total.toLocaleString()} bytes. No third-party packages.`);

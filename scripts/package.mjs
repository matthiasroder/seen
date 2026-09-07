import {mkdir, readdir, readFile} from 'node:fs/promises';
import {execFileSync} from 'node:child_process';
const root = new URL('../', import.meta.url).pathname;
await mkdir(root + 'dist', {recursive: true});
const {version} = JSON.parse(await readFile(root + 'extension/manifest.json', 'utf8'));
const output = root + `dist/seen-${version}.zip`;
execFileSync('zip', ['-q', '-j', output, ...(await readdir(root + 'extension')).map(name => root + 'extension/' + name)]);
console.log(output);
const bundle = root + `dist/seen-${version}-with-helper.zip`;
execFileSync('zip', ['-q', bundle, ...(await readdir(root + 'extension')).map(name => 'extension/' + name),
  'native/host.py', 'native/install.py', 'scripts/items.py', 'scripts/embed.swift',
  'README.md', 'docs/database.md', 'docs/analysis.md'], {cwd: root});
console.log(bundle);

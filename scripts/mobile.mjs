import {mkdir} from 'node:fs/promises';
import {spawnSync} from 'node:child_process';
import {fileURLToPath} from 'node:url';
const root = fileURLToPath(new URL('../',import.meta.url));
const mode = process.argv[2];
if (!['test','receiver','simulator'].includes(mode)) throw new Error('Use test, receiver, or simulator.');
await mkdir(new URL('../dist/',import.meta.url),{recursive: true});
function run(command,args) {
  const result = spawnSync(command,args,{cwd: root,stdio: 'inherit'});
  if (result.error) throw result.error;
  if (result.status !== 0) process.exit(result.status || 1);
}
run(process.execPath,['scripts/mobile-resources.mjs']);
const shared = ['mobile/shared/SeenCore.swift','mobile/shared/SeenTransport.swift'];
if (mode === 'test') {
  run(process.execPath,['--test','mobile/tests/background.test.js']);
  run('xcrun',['swiftc',...shared,'mobile/tests/CoreTests.swift','-o','dist/mobile-tests']);
  run(root + 'dist/mobile-tests',[]);
} else if (mode === 'receiver') {
  run('xcrun',['swiftc','-O',...shared,'mobile/mac/ReceiverMain.swift','-o','dist/SeenReceiver']);
} else {
  run('swift',['scripts/icons.swift','--mobile']);
  run('xcodebuild',['-project','mobile/apple/SeenMobile/SeenMobile.xcodeproj','-scheme','SeenMobile',
    '-destination','generic/platform=iOS Simulator','-configuration','Debug','-derivedDataPath','dist/ios-build',
    'ARCHS=' + (process.arch === 'arm64' ? 'arm64' : 'x86_64'),'CODE_SIGN_IDENTITY=-','CODE_SIGNING_ALLOWED=YES','-quiet','build']);
}

import { existsSync } from 'node:fs';
import { resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const command = process.argv[2] ?? 'dev';
if (!['dev', 'build'].includes(command)) {
  console.error(`Unsupported Tauri command: ${command}`);
  process.exit(2);
}

const candidates = [
  process.env.NOTESLYT_VSDEVCMD,
  process.env.VSINSTALLDIR && resolve(process.env.VSINSTALLDIR, 'Common7/Tools/VsDevCmd.bat'),
  'C:/Program Files/Microsoft Visual Studio/2022/BuildTools/Common7/Tools/VsDevCmd.bat',
  'C:/Program Files (x86)/Microsoft Visual Studio/2022/BuildTools/Common7/Tools/VsDevCmd.bat',
  'D:/Visual Studio build tools/Common7/Tools/VsDevCmd.bat',
].filter(Boolean);
const vsDevCmd = candidates.find(existsSync);

if (!vsDevCmd) {
  console.error('Visual Studio Build Tools with the C++ workload are required. Set NOTESLYT_VSDEVCMD to the path of VsDevCmd.bat.');
  process.exit(1);
}

// Run a checked-in batch file so cmd.exe handles the VS path with native batch
// quoting. VsDevCmd initializes the SDK include/library paths for Cargo.
const result = spawnSync('cmd.exe', ['/d', '/c', 'scripts\\tauri-windows.cmd', command], {
  cwd: process.cwd(),
  stdio: 'inherit',
  env: { ...process.env, NOTESLYT_VSDEVCMD: vsDevCmd },
});

if (result.error) {
  console.error(result.error.message);
  process.exit(1);
}
process.exit(result.status ?? 1);

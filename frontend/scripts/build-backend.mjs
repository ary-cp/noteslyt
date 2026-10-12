import { mkdirSync, rmSync } from 'node:fs';
import { resolve, sep } from 'node:path';
import { spawnSync } from 'node:child_process';

const repoRoot = resolve(process.cwd(), '..');
const tauriRoot = resolve(repoRoot, 'frontend/src-tauri');
const outputDir = resolve(tauriRoot, 'backend-runtime');
if (!outputDir.startsWith(`${tauriRoot}${sep}`)) {
  throw new Error(`Refusing to write outside the Tauri project: ${outputDir}`);
}

rmSync(outputDir, { recursive: true, force: true });
mkdirSync(outputDir, { recursive: true });

const args = [
  'run', '--group', 'dev', '--', 'pyinstaller',
  '--noconfirm', '--clean', '--onedir',
  '--noconsole',
  '--name', 'noteslyt-api',
  '--paths', repoRoot,
  '--distpath', outputDir,
  '--workpath', resolve(tauriRoot, 'target/pyinstaller'),
  '--specpath', resolve(tauriRoot, 'target/pyinstaller'),
  '--collect-all', 'chromadb',
  '--collect-all', 'pdf_inspector',
  '--collect-all', 'anydoc',
  '--hidden-import', 'uvicorn.logging',
  '--hidden-import', 'uvicorn.loops.auto',
  '--hidden-import', 'uvicorn.protocols.http.auto',
  '--hidden-import', 'uvicorn.protocols.websockets.auto',
  '--hidden-import', 'uvicorn.lifespan.on',
  resolve(repoRoot, 'frontend/scripts/backend_entry.py'),
];
const result = spawnSync('uv', args, { cwd: repoRoot, stdio: 'inherit' });
if (result.error) {
  console.error(`Could not run PyInstaller: ${result.error.message}`);
  process.exit(1);
}
process.exit(result.status ?? 1);

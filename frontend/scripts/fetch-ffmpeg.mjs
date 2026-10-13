// Fetch prebuilt ffmpeg/ffprobe binaries into frontend/src-tauri/binaries-ffmpeg/.
//
// Layout produced (per OS subdir):
//   binaries-ffmpeg/windows-x64/ffmpeg.exe + ffprobe.exe
//   binaries-ffmpeg/linux-x64/ffmpeg + ffprobe
//   binaries-ffmpeg/macos-arm64/ffmpeg + ffprobe
//   binaries-ffmpeg/macos-x64/ffmpeg + ffprobe
//
// Usage:
//   node scripts/fetch-ffmpeg.mjs            # current OS only
//   node scripts/fetch-ffmpeg.mjs --all      # every OS (for release machines / CI matrix prep)
//   node scripts/fetch-ffmpeg.mjs --force    # re-download even when binaries exist
//
// Sources are version-pinned below (override via env). Windows uses Gyan's
// release-essentials zip, Linux uses johnvansickle static, macOS uses
// evermeet.cx builds (ffmpeg + ffprobe zips). All are redistributable
// GPL/LGPL builds — see THIRD-PARTY-NOTICES below and credit ffmpeg.org.
import { createWriteStream, existsSync, mkdirSync, rmSync, copyFileSync, chmodSync, readdirSync, statSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const SOURCES = {
  windows: {
    subdir: 'windows-x64',
    files: ['ffmpeg.exe', 'ffprobe.exe'],
    url: process.env.FFMPEG_WIN_URL ?? 'https://www.gyan.dev/ffmpeg/builds/ffmpeg-release-essentials.zip',
    kind: 'gyan-zip',
  },
  linux: {
    subdir: 'linux-x64',
    files: ['ffmpeg', 'ffprobe'],
    url: process.env.FFMPEG_LINUX_URL ?? 'https://johnvansickle.com/ffmpeg/releases/ffmpeg-release-amd64-static.tar.xz',
    kind: 'jv-tarxz',
  },
  'macos-arm64': {
    subdir: 'macos-arm64',
    files: ['ffmpeg', 'ffprobe'],
    url: process.env.FFMPEG_MAC_URL ?? 'https://evermeet.cx/ffmpeg/ffmpeg-7.1.zip',
    probeUrl: process.env.FFMPEG_MAC_PROBE_URL ?? 'https://evermeet.cx/ffmpeg/ffprobe-7.1.zip',
    kind: 'evermeet-zip',
  },
  'macos-x64': {
    subdir: 'macos-x64',
    files: ['ffmpeg', 'ffprobe'],
    url: process.env.FFMPEG_MAC_URL ?? 'https://evermeet.cx/ffmpeg/ffmpeg-7.1.zip',
    probeUrl: process.env.FFMPEG_MAC_PROBE_URL ?? 'https://evermeet.cx/ffmpeg/ffprobe-7.1.zip',
    kind: 'evermeet-zip',
  },
};

const args = new Set(process.argv.slice(2));
const FORCE = args.has('--force');
const ALL = args.has('--all');

function currentKey() {
  if (process.platform === 'win32') return 'windows';
  if (process.platform === 'linux') return 'linux';
  if (process.platform === 'darwin') return process.arch === 'arm64' ? 'macos-arm64' : 'macos-x64';
  throw new Error(`Unsupported platform: ${process.platform}/${process.arch}`);
}

async function download(url, dest) {
  console.log(`  GET ${url}`);
  const res = await fetch(url, { redirect: 'follow' });
  if (!res.ok) throw new Error(`Download failed (${res.status}): ${url}`);
  const total = Number(res.headers.get('content-length') || 0);
  const file = createWriteStream(dest);
  const reader = res.body.getReader();
  let done = 0;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    done += value.length;
    file.write(value);
    if (total && done % (20 * 1024 * 1024) < value.length) {
      console.log(`  ... ${(done / 1024 / 1024).toFixed(0)} / ${(total / 1024 / 1024).toFixed(0)} MB`);
    }
  }
  file.close();
}

function run(cmd, argv, opts = {}) {
  const r = spawnSync(cmd, argv, { stdio: 'inherit', ...opts });
  if (r.error) throw new Error(`Could not run ${cmd}: ${r.error.message}`);
  if (r.status !== 0) throw new Error(`${cmd} exited with ${r.status}`);
}

function extract(archive, destDir, kind) {
  mkdirSync(destDir, { recursive: true });
  if (kind === 'gyan-zip') {
    if (process.platform === 'win32') {
      run('powershell', ['-NoProfile', '-Command', `Expand-Archive -Force '${archive}' '${destDir}'`]);
    } else {
      run('unzip', ['-o', '-q', archive, '-d', destDir]);
    }
  } else if (kind === 'jv-tarxz') {
    run('tar', ['-xf', archive, '-C', destDir]);
  } else {
    // evermeet single-binary zips
    if (process.platform === 'win32') {
      run('powershell', ['-NoProfile', '-Command', `Expand-Archive -Force '${archive}' '${destDir}'`]);
    } else {
      run('unzip', ['-o', archive, '-d', destDir]);
    }
  }
}

function findRecursive(root, name) {
  const stack = [root];
  while (stack.length) {
    const dir = stack.pop();
    for (const entry of readdirSync(dir)) {
      const p = join(dir, entry);
      const st = statSync(p);
      if (st.isDirectory()) stack.push(p);
      else if (entry.toLowerCase() === name.toLowerCase()) return p;
    }
  }
  return null;
}

async function fetchOne(key, outRoot) {
  const src = SOURCES[key];
  const outDir = join(outRoot, src.subdir);
  if (!FORCE && src.files.every((f) => existsSync(join(outDir, f)))) {
    console.log(`[ffmpeg] ${src.subdir}: already present, skipping (use --force to refresh)`);
    return;
  }
  const tmp = join(tmpdir(), `noteslyt-ffmpeg-${src.subdir}`);
  rmSync(tmp, { recursive: true, force: true });
  const stage = join(tmp, 'stage');
  mkdirSync(stage, { recursive: true });

  const archives = [src.url];
  if (src.probeUrl) archives.push(src.probeUrl);
  for (const [i, url] of archives.entries()) {
    const dest = join(tmp, `dl-${i}${url.endsWith('.tar.xz') ? '.tar.xz' : '.zip'}`);
    await download(url, dest);
    extract(dest, join(stage, `a${i}`), src.kind);
  }

  mkdirSync(outDir, { recursive: true });
  // gyan zip nests under ffmpeg-*-essentials_build/bin/; jv tar under ffmpeg-*-static/.
  for (const name of src.files) {
    let found = null;
    for (const root of [stage]) {
      found = findRecursive(root, name);
      if (found) break;
    }
    if (!found) throw new Error(`Archive did not contain ${name} (source: ${src.url})`);
    copyFileSync(found, join(outDir, name));
    if (!name.endsWith('.exe')) {
      try { chmodSync(join(outDir, name), 0o755); } catch { /* non-posix */ }
    }
    console.log(`[ffmpeg] ${src.subdir}/${name}: ok`);
  }
  rmSync(tmp, { recursive: true, force: true });
}

const outRoot = resolve(process.cwd(), 'src-tauri/binaries-ffmpeg');
const keys = ALL ? Object.keys(SOURCES) : [currentKey()];
console.log(`[ffmpeg] target dir: ${outRoot}`);
for (const key of keys) {
  console.log(`[ffmpeg] fetching ${key} ...`);
  await fetchOne(key, outRoot);
}
console.log('[ffmpeg] done. Binaries are gitignored; Tauri bundles them from binaries-ffmpeg/.');

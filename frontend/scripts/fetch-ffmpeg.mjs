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
import { existsSync, mkdirSync, rmSync, copyFileSync, chmodSync, readdirSync, statSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join, resolve } from 'node:path';
import { spawnSync } from 'node:child_process';

const SOURCES = {
  windows: {
    subdir: 'windows-x64',
    files: ['ffmpeg.exe', 'ffprobe.exe'],
    // Pinned override, else the newest BtbN win64-gpl zip via the GitHub
    // API (fast CDN; gpl flavor required — slice_clip encodes libx264,
    // which lgpl builds omit).
    url: process.env.FFMPEG_WIN_URL ?? null,
    kind: 'btbn-zip',
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
  const reader = res.body.getReader();
  const chunks = [];
  let done = 0;
  let lastMark = 0;
  for (;;) {
    const { value, done: end } = await reader.read();
    if (end) break;
    chunks.push(Buffer.from(value));
    done += value.length;
    if (total && done - lastMark >= 20 * 1024 * 1024) {
      lastMark = done;
      console.log(`  ... ${(done / 1024 / 1024).toFixed(0)} / ${(total / 1024 / 1024).toFixed(0)} MB`);
    }
  }
  // Single synchronous write: the file handle is closed before extraction.
  writeFileSync(dest, Buffer.concat(chunks));
}

function run(cmd, argv, opts = {}) {
  const r = spawnSync(cmd, argv, { stdio: 'inherit', ...opts });
  if (r.error) throw new Error(`Could not run ${cmd}: ${r.error.message}`);
  if (r.status !== 0) throw new Error(`${cmd} exited with ${r.status}`);
}

function extract(archive, destDir, kind) {
  mkdirSync(destDir, { recursive: true });
  if (kind === 'jv-tarxz') {
    run('tar', ['-xf', archive, '-C', destDir]);
  } else {
    // gyan/btbn/evermeet single-or-tree zips
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

async function resolveBtbnUrl() {
  if (process.env.FFMPEG_WIN_URL) return process.env.FFMPEG_WIN_URL;
  const api = 'https://api.github.com/repos/BtbN/FFmpeg-Builds/releases/latest';
  const res = await fetch(api, {
    headers: { 'User-Agent': 'noteslyt-fetch-ffmpeg', Accept: 'application/vnd.github+json' },
  });
  if (!res.ok) {
    throw new Error(
      `BtbN release lookup failed (HTTP ${res.status}). Pin a zip via FFMPEG_WIN_URL instead.`,
    );
  }
  const data = await res.json();
  const asset = (data.assets ?? []).find(
    (a) =>
      a.name.startsWith('ffmpeg-') &&
      a.name.includes('win64-gpl') &&
      a.name.endsWith('.zip') &&
      !a.name.includes('shared'),
  );
  if (!asset) {
    throw new Error('No win64-gpl zip in the latest BtbN release. Pin one via FFMPEG_WIN_URL.');
  }
  console.log(`  BtbN ${data.tag_name}: ${asset.name}`);
  return asset.browser_download_url;
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

  const archives = src.kind === 'btbn-zip' ? [await resolveBtbnUrl()] : [src.url];
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
    if (!found) throw new Error(`Archive did not contain ${name} (source: ${archives.join(', ')})`);
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

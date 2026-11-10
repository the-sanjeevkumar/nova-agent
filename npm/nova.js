#!/usr/bin/env node
/**
 * nova - Universal launcher for Nova Agent.
 *
 * On first run (or after an upgrade) it provisions a Python virtual environment
 * under $NOVA_HOME (default ~/.nova), installs the bundled Nova Agent source
 * into it, and then execs the `nova` console script. Everything else (config,
 * sessions, memory, skills) lives in $NOVA_HOME, separate from the code.
 */

import { spawnSync } from 'node:child_process';
import { existsSync, mkdirSync, writeFileSync, readFileSync, rmSync } from 'node:fs';
import { homedir, platform } from 'node:os';
import { join, resolve, delimiter } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = fileURLToPath(new URL('.', import.meta.url));
const APP_ROOT = resolve(__dirname, '..');
const NOVA_HOME = process.env.NOVA_HOME || join(homedir(), '.nova');
const VENV_DIR = join(NOVA_HOME, 'venv');
const IS_WIN = platform() === 'win32';
// Termux reports process.platform === 'android'; $PREFIX distinguishes it from
// other Android Pythons. The Python floor below must match pyproject.toml:
// every runtime dependency is pinned with `python_version >= '3.14'`, so a
// venv built on older Python installs the package with no dependencies and
// `nova` dies on the first import (seen on Termux, which ships 3.12/3.13).
const IS_TERMUX = platform() === 'android' || (process.env.PREFIX || '').includes('com.termux');
const MIN_PY_MAJOR = 3;
const MIN_PY_MINOR = 14;

const VERSION = readVersion();

function readVersion() {
  try {
    return JSON.parse(readFileSync(join(APP_ROOT, 'package.json'), 'utf8')).version;
  } catch {
    return '1.0.0';
  }
}

function pythonTooOldError() {
  console.error(`Nova Agent requires Python ${MIN_PY_MAJOR}.${MIN_PY_MINOR} or newer.`);
  if (IS_TERMUX) {
    console.error('Termux: run `pkg update && pkg install python`, then re-run `nova`.');
  } else {
    console.error('Install Python from https://www.python.org/downloads/ and re-run `nova`.');
  }
  process.exit(1);
}

function bionicStop(py) {
  // The Python pyproject resolves against is a Bionic (Android libc) build:
  // SOABI embeds the androideabi triple. PyPI publishes no Bionic wheels for
  // Nova's native dependencies, so pip/uv can only fall back to sdist builds
  // (Rust via maturin: "Target triple not supported by rustup:
  // arm-unknown-linux-androideabi") that cannot run on the phone. It is not a
  // per-package pin issue; stop before the install cascades pkg by pkg.
  // Run from the launcher, not from env vars: inside proot-distro the Termux
  // env (TERMUX_VERSION, $PREFIX) leaks in, but the glibc interpreter there
  // has a clean SOABI and must be allowed to proceed. 24.04 ships 3.12 where
  // the launcher floor refuses with pythonTooOldError; the deadsnakes PPA
  // carries 3.14.
  console.error('');
  console.error(`🌌 Detected Android/Bionic Python ${py.major}.${py.minor} (SOABI ${py.soabi || 'androideabi'}).`);
  console.error('');
  console.error("PyPI has no Bionic wheels for Nova's native dependencies, so the npm/pip");
  console.error('path cannot complete on-device: every Rust/C package (firecrawl-anydoc,');
  console.error('cryptography, ...) fails its sdist build one after another.');
  console.error('');
  console.error('Install Nova on the phone one of two ways:');
  console.error('');
  console.error('  1. Inside a glibc Linux environment via proot-distro (works today):');
  console.error('     pkg install proot-distro && proot-distro install ubuntu && proot-distro login ubuntu');
  console.error('     then inside it: `add-apt-repository ppa:deadsnakes/ppa` (need');
  console.error('     `apt install -y software-properties-common`), `apt update && apt install');
  console.error('     -y python3.14 python3.14-venv nodejs npm curl` and re-run the npm');
  console.error('     installer and `nova`. The glibc Python installs the same wheels as a PC.');
  console.error('  2. The native Termux payload built from a PC (needs Docker; requires the');
  console.error('     arm64-v8a Termux build): scripts/termux/termux_build.sh --repo <checkout>');
  console.error('     --tag <a release tag> --out <dir>, as described in');
  console.error('     website/docs/getting-started/termux.md.');
  console.error('');
  console.error('Whatever the route, the phone must run the arm64-v8a (64-bit) Termux');
  console.error('application; the 32-bit (arm) Termux build cannot run Nova at all.');
  process.exit(1);
}

function probeBionic(py) {
  const r = spawnSync(py.cmd, [...py.prefix, '-c',
    "import sysconfig; print(sysconfig.get_config_var('SOABI'))"], { encoding: 'utf8' });
  if (r.status !== 0) return;
  const soabi = (r.stdout || '').trim();
  if (soabi.includes('androideabi')) {
    py.soabi = soabi;
    bionicStop(py);
  }
}

function findPython() {
  const candidates = IS_WIN ? ['python', 'python3', 'py'] : ['python3', 'python'];
  for (const c of candidates) {
    const args = c === 'py' ? ['-3', '--version'] : ['--version'];
    const r = spawnSync(c, args, { encoding: 'utf8' });
    if (r.status !== 0) continue;
    const m = (r.stdout + r.stderr).match(/Python (\d+)\.(\d+)\.(\d+)/);
    if (!m) continue;
    const [major, minor] = [parseInt(m[1]), parseInt(m[2])];
    if (major > MIN_PY_MAJOR || (major === MIN_PY_MAJOR && minor >= MIN_PY_MINOR)) {
      return c === 'py' ? { cmd: 'py', prefix: ['-3'], major, minor } : { cmd: c, prefix: [], major, minor };
    }
  }
  pythonTooOldError();
}

function venvPython() {
  return IS_WIN
    ? join(VENV_DIR, 'Scripts', 'python.exe')
    : join(VENV_DIR, 'bin', 'python');
}

function venvNova() {
  return IS_WIN
    ? join(VENV_DIR, 'Scripts', 'nova.exe')
    : join(VENV_DIR, 'bin', 'nova');
}

function runSync(cmd, args, opts = {}) {
  const r = spawnSync(cmd, args, { stdio: 'inherit', ...opts });
  return r.status === null ? 1 : r.status;
}

function markerPath() {
  return join(VENV_DIR, '.nova-version');
}

function provisionMarker(py) {
  // Two lines: launcher VERSION, then the venv's Python major.minor. A marker
  // without the Python line (pre-1.0.2 installs) forces re-provision, so venvs
  // built under older Python are not silently reused.
  return `${VERSION}\n${py ? `${py.major}.${py.minor}` : 'unknown'}\n`;
}

function needsProvision(py) {
  if (!existsSync(venvPython()) || !existsSync(venvNova())) return true;
  try {
    return readFileSync(markerPath(), 'utf8') !== provisionMarker(py);
  } catch {
    return true;
  }
}

function provision() {
  const py = findPython();
  probeBionic(py);

  mkdirSync(NOVA_HOME, { recursive: true });

  if (!existsSync(venvPython())) {
    console.log('🌌 Nova Agent — first run: creating Python environment...');
    const status = runSync(py.cmd, [...py.prefix, '-m', 'venv', VENV_DIR]);
    if (status !== 0) {
      console.error('Failed to create virtual environment.');
      process.exit(status);
    }
  }

  console.log('📦 Installing Nova Agent dependencies (this may take a few minutes)...');
  // uv, not pip: the dependency graph is only resolvable with the repo's
  // [tool.uv] override-dependencies (e.g. Pillow 13 over browser-harness's
  // pillow==12.3.0; see tests/pm/test_pip_check.py). pip cannot be told about
  // these overrides, so a plain `pip install <repo>` dies with
  // ResolutionImpossible even on a healthy PC. `uv pip install` honors them
  // when it runs from the checkout (the cwd supplies pyproject.toml).
  // --editable, not a wheel: setup.py raises on bdist_wheel/sdist outside the
  // Nix build (class: the npm payload has no bundled assets on its own; they
  // resolve through the source tree the editable install leaves in place),
  // and an editable install keeps that layout: APP_ROOT stays the runtime
  // asset root, venv/bin/nova comes from the entry point.
  const pip = runSync(venvPython(), ['-m', 'pip', 'install', '--upgrade', 'pip', 'uv', '--quiet']);
  if (pip !== 0) {
    console.error('uv bootstrap failed (pip could not install uv into the venv).');
    process.exit(pip);
  }
  const uvExe = IS_WIN
    ? join(VENV_DIR, 'Scripts', 'uv.exe')
    : join(VENV_DIR, 'bin', 'uv');
  const install = runSync(uvExe, ['pip', 'install', '--python', venvPython(), '--editable', APP_ROOT, '--quiet'],
                          { cwd: APP_ROOT });
  if (install !== 0) {
    console.error('Nova Agent installation failed.');
    console.error('uv printed the failure above. If you are on Android/Termux, PyPI has no');
    console.error('Bionic wheels for the native dependencies; use the guided stop in the');
    console.error('launcher or the Termux payload (website/docs/getting-started/termux.md)');
    console.error('instead of the npm launcher.');
    process.exit(install);
  }

  writeFileSync(markerPath(), provisionMarker(py));
  console.log('✅ Nova Agent ready.\n');
}

function main() {
  if (needsProvision(findPython())) {
    provision();
  }

  const env = { ...process.env, NOVA_NPM_MANAGED: '1' };
  const status = runSync(venvNova(), process.argv.slice(2), { env });
  process.exit(status);
}

main();

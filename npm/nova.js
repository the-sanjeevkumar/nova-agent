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
      return c === 'py' ? { cmd: 'py', prefix: ['-3'] } : { cmd: c, prefix: [] };
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
  const pip = runSync(venvPython(), ['-m', 'pip', 'install', '--upgrade', 'pip', '--quiet']);
  if (pip !== 0) {
    console.error('pip upgrade failed.');
    process.exit(pip);
  }
  const install = runSync(venvPython(), ['-m', 'pip', 'install', '--quiet', APP_ROOT]);
  if (install !== 0) {
    console.error('Nova Agent installation failed.');
    console.error('pip printed the failure above. If you are on Termux, PyPI wheels for');
    console.error('pydantic-core/psutil do not target Android/Bionic libc; use the Nova');
    console.error('Termux deb package (or a PM wheelhouse) instead of the npm launcher.');
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

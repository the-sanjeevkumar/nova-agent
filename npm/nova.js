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

const VERSION = readVersion();

function readVersion() {
  try {
    return JSON.parse(readFileSync(join(APP_ROOT, 'package.json'), 'utf8')).version;
  } catch {
    return '1.0.0';
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
    if (major > 3 || (major === 3 && minor >= 11)) {
      return c === 'py' ? { cmd: 'py', prefix: ['-3'] } : { cmd: c, prefix: [] };
    }
  }
  return null;
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

function needsProvision() {
  if (!existsSync(venvPython()) || !existsSync(venvNova())) return true;
  try {
    const installed = readFileSync(markerPath(), 'utf8').trim();
    return installed !== VERSION;
  } catch {
    return true;
  }
}

function provision() {
  const py = findPython();
  if (!py) {
    console.error('Nova Agent requires Python 3.11 or newer.');
    console.error('Install Python from https://www.python.org/downloads/ and re-run `nova`.');
    process.exit(1);
  }

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
    process.exit(install);
  }

  writeFileSync(markerPath(), VERSION + '\n');
  console.log('✅ Nova Agent ready.\n');
}

function main() {
  if (needsProvision()) {
    provision();
  }

  const env = { ...process.env, NOVA_NPM_MANAGED: '1' };
  const status = runSync(venvNova(), process.argv.slice(2), { env });
  process.exit(status);
}

main();

import test from 'node:test';
import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { mkdirSync, mkdtempSync, chmodSync, rmSync, writeFileSync } from 'node:fs';
import { tmpdir } from 'node:os';
import { join } from 'node:path';

// Behavior contracts for the npm launcher's decision points, run offline
// around a stub `python3` on PATH:
//   1. A Bionic (androideabi SOABI) Python stops BEFORE any dependency
//      install, with the guided routes (PyPI has no Bionic wheels).
//   2. A glibc Python must NOT trip on Termux env vars alone: inside
//      proot-distro, TERMUX_VERSION/$PREFIX leak in but the interpreter is
//      a normal linux-gnu build and the launcher has to proceed to the
//      Python floor check instead of stopping.

const __dirname = new URL('.', import.meta.url).pathname;

function makeShim(dir, version, soabi) {
  mkdirSync(dir, { recursive: true });
  for (const name of ['python3', 'python']) {
    const path = join(dir, name);
    writeFileSync(path, [
      '#!/bin/sh',
      'if [ "$1" = "--version" ]; then',
      `  echo "Python ${version}"`,
      '  exit 0',
      'fi',
      'if printf \'%s\' "$2" | grep -q SOABI; then',
      `  echo "${soabi}"`,
      '  exit 0',
      'fi',
      'exit 3',
    ].join('\n'), 'utf8');
    chmodSync(path, 0o755);
  }
}

function runLauncher(env) {
  return spawnSync(process.execPath, [join(__dirname, 'nova.js'), '--version'], {
    encoding: 'utf8',
    env: { ...process.env, ...env },
  });
}

test('bionic Python stops before the dependency install with guided routes', () => {
  const home = mkdtempSync(join(tmpdir(), 'nova-test-'));
  const shim = mkdtempSync(join(tmpdir(), 'nova-shim-'));
  try {
    makeShim(shim, '3.14.2', 'cpython-314-arm-linux-androideabi');
    const r = runLauncher({ PATH: `${shim}:${process.env.PATH}`, NOVA_HOME: join(home, '.nova') });
    assert.equal(r.status, 1);
    assert.match(r.stderr, /Android\/Bionic Python 3\.14/);
    assert.match(r.stderr, /proot-distro/);
    assert.match(r.stderr, /termux_build\.sh/);
    // The venv must not even be attempted: earlier failures created half a
    // python environment before failing.
    rmSync(join(home, '.nova'), { recursive: true, force: true });
  } finally {
    rmSync(home, { recursive: true, force: true });
    rmSync(shim, { recursive: true, force: true });
  }
});

test('a glibc Python does not stop on Termux env vars; the floor check runs', () => {
  const home = mkdtempSync(join(tmpdir(), 'nova-test-'));
  const shim = mkdtempSync(join(tmpdir(), 'nova-shim-'));
  try {
    makeShim(shim, '3.12.4', 'cpython-312-aarch64-linux-gnu');
    const r = runLauncher({
      PATH: `${shim}:${process.env.PATH}`,
      NOVA_HOME: join(home, '.nova'),
      TERMUX_VERSION: '0.119.0',
      PREFIX: '/data/data/com.termux/files/usr',
    });
    assert.equal(r.status, 1);
    assert.ok(!r.stderr.includes('Bionic'));
    assert.match(r.stderr, /requires Python 3\.14 or newer/);
  } finally {
    rmSync(home, { recursive: true, force: true });
    rmSync(shim, { recursive: true, force: true });
  }
});

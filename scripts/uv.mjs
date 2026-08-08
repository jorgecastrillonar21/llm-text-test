#!/usr/bin/env node
// Runs `uv` from the repo root regardless of whether uv is on PATH.
// Falls back to `python -m uv`, which works after `pip install uv`.
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { dirname, resolve } from 'node:path';

const repoRoot = resolve(dirname(fileURLToPath(import.meta.url)), '..');
const apiDir = resolve(repoRoot, 'apps/api');
const args = process.argv.slice(2);

const candidates = [
  { cmd: 'uv', args },
  { cmd: 'python', args: ['-m', 'uv', ...args] },
  { cmd: 'python3', args: ['-m', 'uv', ...args] },
];

for (const candidate of candidates) {
  const probe = spawnSync(candidate.cmd, ['--version'], { stdio: 'ignore', shell: true });
  if (probe.status !== 0) continue;
  const run = spawnSync(candidate.cmd, candidate.args, {
    stdio: 'inherit',
    shell: true,
    cwd: apiDir,
  });
  process.exit(run.status ?? 1);
}

console.error(
  'uv not found. Install it with:  pip install uv\n' +
    'or see https://docs.astral.sh/uv/getting-started/installation/',
);
process.exit(1);

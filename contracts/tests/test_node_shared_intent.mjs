/**
 * Synthetic Node -> Python -> existing SQLite journal integration.
 * No real host, account, network client, or enrollment write.
 */
import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawn, spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { nodeScopeFromWindow, reserveNodeSharedIntent } from '../node_shared_intent.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const PYTHON = process.env.KINGO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3');
const SCOPE = Object.freeze({
  school: 'https://synthetic.invalid/hsjw', account: 'fictional-student',
  term: '2026-2027/1', courseId: 'COURSE-A', classId: 'CLASS-01',
});

function fixture(t) {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), 'node-intent-'));
  t.after(() => fs.rmSync(dir, { recursive: true, force: true }));
  return { ...process.env, KINGO_INTENT_DB: path.join(dir, 'shared.sqlite3') };
}

function pythonBridge(scope, env) {
  return spawnSync(PYTHON, ['-m', 'contracts.intent_bridge_cli'], {
    cwd: ROOT, env, encoding: 'utf8',
    input: JSON.stringify({
      school: scope.school, account: scope.account, term: scope.term,
      course_id: scope.courseId, class_id: scope.classId,
    }),
    timeout: 20000,
  });
}

function pythonAsync(scope, env) {
  return new Promise((resolve, reject) => {
    const child = spawn(PYTHON, ['-m', 'contracts.intent_bridge_cli'], {
      cwd: ROOT, env, stdio: ['pipe', 'pipe', 'pipe'],
    });
    let output = '';
    child.on('error', reject);
    child.stdout.on('data', (data) => { output += data.toString(); });
    child.on('close', (code) => resolve({ code, status: JSON.parse(output.trim()).status }));
    child.stdin.end(JSON.stringify({
      school: scope.school, account: scope.account, term: scope.term,
      course_id: scope.courseId, class_id: scope.classId,
    }));
  });
}

test('fresh selection-window scope requires exact account and explicit raw term mapping', () => {
  const params = {
    school: SCOPE.school, status: '200',
    response: { xh: SCOPE.account, xn: '2026', xqM: '0', isValidTimerange: '1' },
    expectedAccount: SCOPE.account, canonicalTerm: SCOPE.term,
    observedTerm: '2026,0', courseId: SCOPE.courseId, classId: SCOPE.classId,
  };
  assert.equal(nodeScopeFromWindow(params).term, '2026-2027/1');
  assert.throws(() => nodeScopeFromWindow({ ...params, observedTerm: '2026,1' }));
  assert.throws(() => nodeScopeFromWindow({ ...params, expectedAccount: 'other' }));
  assert.throws(() => nodeScopeFromWindow({ ...params, canonicalTerm: '2026,0' }));
  assert.throws(() => nodeScopeFromWindow({ ...params, status: '400' }));
  assert.throws(() => nodeScopeFromWindow({
    ...params, response: { ...params.response, isValidTimerange: '0' },
  }));
});

test('Node reserves the same file and historical term spelling as Python', (t) => {
  const env = fixture(t);
  assert.equal(reserveNodeSharedIntent(SCOPE, { python: PYTHON, env }), true);
  assert.equal(reserveNodeSharedIntent({ ...SCOPE, classId: 'CLASS-02' }, { python: PYTHON, env }), false);
  const old = pythonBridge({ ...SCOPE, term: '2026-2027-1' }, env);
  assert.equal(old.status, 0);
  assert.equal(JSON.parse(old.stdout).status, 'duplicate');
});

test('Python reservation made first blocks Node and survives restart', (t) => {
  const env = fixture(t);
  const old = pythonBridge(SCOPE, env);
  assert.equal(JSON.parse(old.stdout).status, 'reserved');
  assert.equal(reserveNodeSharedIntent(SCOPE, { python: PYTHON, env }), false);
  assert.equal(reserveNodeSharedIntent(SCOPE, { python: PYTHON, env }), false);
});

test('school/account/term scope isolated; exact same course is not', (t) => {
  const env = fixture(t);
  assert.equal(reserveNodeSharedIntent(SCOPE, { python: PYTHON, env }), true);
  for (const patch of [
    { account: 'fictional-other' }, { school: 'https://different.synthetic.invalid' },
    { term: '2026-2027/2' }, { courseId: 'COURSE-B' },
  ]) {
    assert.equal(reserveNodeSharedIntent({ ...SCOPE, ...patch }, { python: PYTHON, env }), true);
  }
});

test('simultaneous independent Python processes get exactly one reservation', async (t) => {
  const env = fixture(t);
  const verdicts = await Promise.all(Array.from({ length: 6 }, () => pythonAsync(SCOPE, env)));
  assert.equal(verdicts.filter((r) => r.code === 0 && r.status === 'reserved').length, 1);
  assert.equal(verdicts.filter((r) => r.code === 0 && r.status === 'duplicate').length, 5);
});

test('missing Python, invalid identity and inaccessible database fail closed', (t) => {
  const env = fixture(t);
  assert.throws(() => reserveNodeSharedIntent(SCOPE, { python: 'unavailable-python-xyz', env }));
  assert.throws(() => reserveNodeSharedIntent({ ...SCOPE, classId: '' }, { python: PYTHON, env }));
  assert.throws(() => reserveNodeSharedIntent(SCOPE, {
    python: PYTHON, env: { ...env, KINGO_INTENT_DB: path.dirname(env.KINGO_INTENT_DB) },
  }));
  assert.equal(fs.existsSync(env.KINGO_INTENT_DB), false);
});

test('Python bridge gives redacted fail-closed error on malformed input', (t) => {
  const env = fixture(t);
  const x = spawnSync(PYTHON, ['-m', 'contracts.intent_bridge_cli'], {
    cwd: ROOT, env, input: '{"account":"SECRET_SENTINEL"}',
    encoding: 'utf8',
  });
  assert.notEqual(x.status, 0);
  assert.equal(JSON.parse(x.stdout).status, 'blocked');
  assert.ok(!x.stdout.includes('SECRET_SENTINEL'));
});

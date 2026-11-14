/**
 * Existing SQLite reservation shared with Python, ops and Windows.
 *
 * Runs the existing Python IntentJournal from a fresh child process using
 * JSON over stdin. The same salt, schema, defaults and historical keys are
 * preserved. No database implementation, HTTP executor or retry is added.
 */
import { spawnSync } from 'node:child_process';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { normalizeNodeIdentity } from './node_receipt_contract.mjs';

const ROOT = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');

function fail(message) {
  throw new Error(message);
}

export function nodeScopeFromWindow({
  school, response, status, expectedAccount, canonicalTerm,
  observedTerm, courseId, classId,
}) {
  // The current authenticated official window response must be checked
  // immediately before selecting a target. This is a *shape* gate, not
  // independent proof of the endpoint's official provenance.
  if (!response || typeof response !== 'object' || Array.isArray(response)
      || String(status) !== '200' || String(response.isValidTimerange) !== '1') {
    return fail('官方选课窗口或登录身份未经核验，禁止提交');
  }
  const actualAccount = response.xh;
  const actualTerm = typeof response.xn === 'string' || typeof response.xn === 'number'
    ? String(response.xn) + ',' + String(response.xqM ?? '') : '';
  if (typeof actualAccount !== 'string' || !actualAccount.trim()
      || actualAccount !== expectedAccount || actualTerm !== observedTerm) {
    return fail('官方账号或原始学期与已确认范围不一致');
  }
  // Never silently transform NCWU xqM=0 into an academic-term 1.
  // A separately reviewed, explicit mapping must be configured by the user.
  if (typeof canonicalTerm !== 'string'
      || !/^\d{4}-\d{4}[-/,][1-3]$/.test(canonicalTerm.trim())) {
    return fail('缺少已确认的跨客户端统一学期标识');
  }
  const scope = normalizeNodeIdentity({
    school, account: actualAccount, term: canonicalTerm,
    courseId, classId,
  });
  if (Object.values(scope).some((value) => value.length > 256
      || /[\x00-\x1f\x7f]/.test(value))) {
    return fail('提交意图身份格式不可信');
  }
  return scope;
}

export function reserveNodeSharedIntent(scope, {
  python = process.env.KINGO_PYTHON || (process.platform === 'win32' ? 'python' : 'python3'),
  env = process.env,
} = {}) {
  const id = normalizeNodeIdentity(scope);
  if (Object.values(id).some((value) => value.length > 256
      || /[\x00-\x1f\x7f]/.test(value))) {
    return fail('提交意图身份格式不可信');
  }
  // All identifiers are sent on stdin, never command-line arguments.
  // Subprocess failures must NEVER fall back to an independent Node journal.
  const result = spawnSync(python, ['-m', 'contracts.intent_bridge_cli'], {
    cwd: ROOT, encoding: 'utf8', windowsHide: true, timeout: 20000,
    maxBuffer: 4096, env,
    input: JSON.stringify({
      school: id.school, account: id.account, term: id.term,
      course_id: id.courseId, class_id: id.classId,
    }),
  });
  if (result.error || result.signal || result.status !== 0) {
    return fail('共享 SQLite 意图记录不可用，已阻止提交');
  }
  let answer;
  try {
    answer = JSON.parse(result.stdout.trim());
  } catch {
    return fail('共享意图记录响应无效，已阻止提交');
  }
  if (answer && answer.status === 'reserved') return true;
  if (answer && answer.status === 'duplicate') return false;
  return fail('共享意图记录未确认，已阻止提交');
}

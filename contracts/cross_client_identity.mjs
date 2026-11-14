/**
 * Offline, read-only identity and selected-course evidence reconciliation.
 * This does NOT establish the authenticity of the supplied records: callers
 * must independently authenticate official readback provenance.
 * Never enables writes, retries, or reserves a persistent intent.
 */
import { normalizeNodeIdentity } from './node_receipt_contract.mjs';

function exact(v, field) {
  if (typeof v !== 'string' || !v.trim() || ['unknown', 'none', 'null', '-'].includes(v.trim().toLowerCase())) {
    throw new TypeError(`${field} must be an exact non-placeholder string`);
  }
  return v.trim();
}

export function reconcileIdentity(nodeScope, windowsScope) {
  const node = normalizeNodeIdentity(nodeScope);
  const win = normalizeNodeIdentity(windowsScope);
  const fields = ['school', 'account', 'term', 'courseId', 'classId'];
  const mismatches = fields.filter(k => node[k] !== win[k]);
  return Object.freeze({ matches: mismatches.length === 0, mismatches: Object.freeze(mismatches) });
}

/**
 * Require explicit authenticated source scope and a fresh exact selected row.
 * A transport receipt, a course count, or a display-name match is insufficient.
 */
export function inspectOfficialSelectedReadback(target, evidence) {
  const scope = normalizeNodeIdentity(target);
  if (!evidence || typeof evidence !== 'object' || Array.isArray(evidence)) {
    return Object.freeze({ confirmed: false, reason: 'missing_evidence' });
  }
  if (evidence.authenticated !== true || evidence.official !== true || evidence.fresh !== true) {
    return Object.freeze({ confirmed: false, reason: 'unverified_provenance' });
  }
  const source = evidence.scope;
  if (!source || typeof source !== 'object') {
    return Object.freeze({ confirmed: false, reason: 'missing_scope' });
  }
  let sourceIdentity;
  try {
    sourceIdentity = {
      school: exact(source.school, 'school'),
      account: exact(source.account, 'account'),
      term: exact(source.term, 'term'),
    };
  } catch {
    return Object.freeze({ confirmed: false, reason: 'invalid_scope' });
  }
  if (['school', 'account', 'term'].some(k => sourceIdentity[k] !== scope[k])) {
    return Object.freeze({ confirmed: false, reason: 'scope_mismatch' });
  }
  if (!Array.isArray(evidence.selectedRows)) {
    return Object.freeze({ confirmed: false, reason: 'missing_exact_rows' });
  }
  const matched = evidence.selectedRows.some(row =>
    row && typeof row.courseId === 'string' && typeof row.classId === 'string' &&
    row.courseId === scope.courseId && row.classId === scope.classId);
  return Object.freeze({ confirmed: matched, reason: matched ? 'exact_row_present' : 'exact_row_absent' });
}

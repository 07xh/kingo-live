import test from 'node:test';
import assert from 'node:assert/strict';
import { reconcileIdentity, inspectOfficialSelectedReadback } from '../cross_client_identity.mjs';

const scope = Object.freeze({
  school: 'https://synthetic.invalid',
  account: '000123',
  term: '2026-2027-1',
  courseId: '0011',
  classId: '0009',
});
const evidence = () => ({
  authenticated: true, official: true, fresh: true,
  scope: { school: scope.school, account: scope.account, term: scope.term },
  selectedRows: [{ courseId: scope.courseId, classId: scope.classId }],
});

test('same exact account, term, course and class are compatible', () => {
  assert.deepEqual(reconcileIdentity(scope, { ...scope }), { matches: true, mismatches: [] });
});
test('account, term, course and class mismatches are reported without guessing', () => {
  const x = reconcileIdentity(scope, { ...scope, account: '123', term: '2026-2027-2', classId: '0010' });
  assert.deepEqual(x.mismatches, ['account', 'term', 'classId']);
});
test('matching evidence needs all official provenance flags and exact rows', () => {
  assert.deepEqual(inspectOfficialSelectedReadback(scope, evidence()), { confirmed: true, reason: 'exact_row_present' });
  for (const flag of ['authenticated', 'official', 'fresh']) {
    assert.equal(inspectOfficialSelectedReadback(scope, { ...evidence(), [flag]: false }).confirmed, false);
  }
});
test('same course but wrong class never confirms', () => {
  const e = evidence();
  e.selectedRows = [{ courseId: scope.courseId, classId: '0010' }];
  assert.deepEqual(inspectOfficialSelectedReadback(scope, e), { confirmed: false, reason: 'exact_row_absent' });
});
test('wrong account or term never confirms', () => {
  for (const field of ['account', 'term', 'school']) {
    const e = evidence(); e.scope[field] = 'different';
    assert.equal(inspectOfficialSelectedReadback(scope, e).reason, 'scope_mismatch');
  }
});
test('counts, messages and missing rows are never official proof', () => {
  const e = evidence(); delete e.selectedRows; e.count = 1; e.message = 'success';
  assert.equal(inspectOfficialSelectedReadback(scope, e).reason, 'missing_exact_rows');
});
test('missing or placeholder identity cannot pass comparison', () => {
  assert.throws(() => reconcileIdentity(scope, { ...scope, account: 'unknown' }));
});

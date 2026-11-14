import test from 'node:test';
import assert from 'node:assert/strict';
import { normalizeNodeIdentity, projectNodeTransportReceipt } from '../node_receipt_contract.mjs';

const scope = Object.freeze({
  school: 'https://synthetic.invalid/hsjw',
  account: '00001234',
  term: '2026-2027-1',
  courseId: '0012',
  classId: '0003',
});

test('node identity contract preserves exact leading zeroes but does not claim provenance', () => {
  const normalized = normalizeNodeIdentity(scope);
  assert.deepEqual(normalized, scope);
  assert.equal(Object.isFrozen(normalized), true);
  assert.equal('identityVerified' in normalized, false);
});

test('missing/ambiguous identifiers never pass shape validation', () => {
  for (const [key, value] of [
    ['account', ''], ['account', 'unknown'],
    ['account', 1234], ['term', null],
    ['courseId', 'none'], ['classId', ''],
  ]) {
    assert.throws(() => normalizeNodeIdentity({ ...scope, [key]: value }));
  }
});

test('every transport response remains unverified despite success or rejection messages', () => {
  for (const code of ['OK', 'enrolled', 'FULL', 'CONFLICT', 'AUTH', 'OTHER']) {
    const result = projectNodeTransportReceipt({ code, writeAttempted: true });
    assert.equal(result.delivery, 'unknown');
    assert.equal(result.officialPresence, null);
    assert.equal(result.officiallyConfirmed, false);
    assert.equal(result.requiresOfficialReadback, true);
  }
});

test('write exceptions, timeouts and absent receipts remain result-unknown', () => {
  for (const code of ['ERR', 'TIMEOUT', 'unknown']) {
    const result = projectNodeTransportReceipt({ code, writeAttempted: null });
    assert.equal(result.delivery, 'unknown');
    assert.equal(result.requiresOfficialReadback, true);
  }
});

test('only a confirmed no-write event is unsent', () => {
  const result = projectNodeTransportReceipt({ code: 'ERR', writeAttempted: false });
  assert.deepEqual(result, {
    delivery: 'unsent',
    officialPresence: null,
    officiallyConfirmed: false,
    requiresOfficialReadback: false,
  });
});

test('unknown writeAttempted type is not silently interpreted', () => {
  for (const invalid of [undefined, 'false', 0, 1, {}, []]) {
    assert.throws(() => projectNodeTransportReceipt({ code: 'ERR', writeAttempted: invalid }));
  }
});

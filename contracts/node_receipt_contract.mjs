/**
 * Node transport evidence projection for OFFLINE compatibility reviews.
 *
 * This module makes no network requests, does not reserve write intents,
 * and does not certify an authenticated school identity.
 * The live Node client is intentionally NOT wired to this module yet.
 */
const invalid = new Set(['', 'none', 'null', 'unknown', '-']);

function exactString(value, field) {
  if (typeof value !== 'string' || invalid.has(value.trim().toLowerCase())) {
    throw new TypeError(`${field} must be a non-empty, non-placeholder string`);
  }
  return value.trim();
}

/** Validate identity *shape* only, not its authenticity or provenance. */
export function normalizeNodeIdentity(scope) {
  if (!scope || typeof scope !== 'object' || Array.isArray(scope)) {
    throw new TypeError('scope must be a mapping');
  }
  return Object.freeze({
    school: exactString(scope.school, 'school'),
    account: exactString(scope.account, 'account'),
    term: exactString(scope.term, 'term'),
    courseId: exactString(scope.courseId, 'courseId'),
    classId: exactString(scope.classId, 'classId'),
  });
}

/**
 * A transport receipt can NEVER prove presence in the official selected list.
 * Even code=OK/enrolled, or code=full/error, has no official readback authority.
 * Only explicit writeAttempted=false is sufficiently known to be unsent.
 */
export function projectNodeTransportReceipt(receipt) {
  if (!receipt || typeof receipt !== 'object' || Array.isArray(receipt)) {
    throw new TypeError('receipt must be a mapping');
  }
  const { writeAttempted } = receipt;
  if (writeAttempted !== true && writeAttempted !== false && writeAttempted !== null) {
    throw new TypeError('writeAttempted must be true, false, or null');
  }
  return Object.freeze({
    delivery: writeAttempted === false ? 'unsent' : 'unknown',
    officialPresence: null,
    officiallyConfirmed: false,
    requiresOfficialReadback: writeAttempted !== false,
  });
}

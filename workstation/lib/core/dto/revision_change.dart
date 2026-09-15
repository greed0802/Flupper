/// One classified footprint from a revision comparison.
///
/// Mirrors `qsagent.api.contracts.RevisionChangeDTO`. Two fields carry the
/// meaning a reader has to act on:
///
/// * `status` says what happened to the footprint. `ambiguous`, `unchanged`
///   and `unresolved` are all "not a change" in different ways, and none of
///   them is a finding;
/// * `affected_claim_ids` names the claims whose stored evidence cites the
///   base-side row. It is an association, not a verdict: nothing in this
///   response asserts a claim is still correct.
library;

import '../dto_limits.dart';
import 'revision_evidence.dart';
import 'strict_json.dart';

class RevisionChange {
  const RevisionChange({
    required this.identityId,
    required this.status,
    required this.groupSize,
    required this.base,
    required this.target,
    required this.changedFields,
    required this.affectedClaimIds,
    required this.affectedClaimsOmitted,
    required this.reason,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'identity_id',
    'status',
    'group_size',
    'base',
    'target',
    'changed_fields',
    'affected_claim_ids',
    'affected_claims_omitted',
    'reason',
  };

  /// Every comparison status, mirrored from `qsagent.api.contracts.DiffStatus`
  /// and asserted equal to it by the contract mirror test.
  static const Set<String> statuses = <String>{
    'added',
    'removed',
    'changed',
    'unchanged',
    'ambiguous',
    'unresolved',
  };

  /// Statuses that mean the evidence itself could not be compared. A renderer
  /// should treat these as "needs a person", never as "no change".
  static const Set<String> unresolvedStatuses = <String>{
    'ambiguous',
    'unresolved',
  };

  final String identityId;
  final String status;

  /// How many rows shared this footprint, on both sides together. Greater than
  /// one with an `ambiguous` status is the store saying it cannot tell which
  /// row is which.
  final int groupSize;

  /// The row in the earlier revision, or `null` when the footprint is new.
  final RevisionEvidence? base;

  /// The row in the later revision, or `null` when the footprint is gone.
  final RevisionEvidence? target;

  /// Names of the compared fields that differ, at most [maxDiffFields].
  final List<String> changedFields;

  /// Claims whose stored evidence cites the base-side row, at most
  /// [maxDiffAffectedClaims].
  final List<String> affectedClaimIds;

  /// How many further claims were omitted from [affectedClaimIds].
  final int affectedClaimsOmitted;

  /// Why the row is unresolved or ambiguous, at most [maxDiffReasonChars].
  final String? reason;

  bool get isChange => status == 'changed';

  bool get needsReview => unresolvedStatuses.contains(status);

  factory RevisionChange.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RevisionChange(
      identityId: requireBoundedString(
        map,
        'identity_id',
        maxLength: maxDiffHashChars,
      ),
      status: requireOneOf(map, 'status', allowed: statuses),
      groupSize: requireCount(map, 'group_size', maxValue: maxDiffCount),
      base: _evidence(map, 'base'),
      target: _evidence(map, 'target'),
      changedFields: requireBoundedStringList(
        map,
        'changed_fields',
        maxLength: maxDiffFields,
        maxItemLength: maxDiffTextChars,
      ),
      affectedClaimIds: requireBoundedStringList(
        map,
        'affected_claim_ids',
        maxLength: maxDiffAffectedClaims,
        maxItemLength: maxDiffClaimIdChars,
      ),
      affectedClaimsOmitted: requireCount(
        map,
        'affected_claims_omitted',
        maxValue: maxDiffScannedClaims,
      ),
      reason: requireOptionalBoundedString(
        map,
        'reason',
        maxLength: maxDiffReasonChars,
      ),
    );
  }

  static RevisionEvidence? _evidence(Map<String, Object?> map, String key) {
    final nested = requireOptionalNestedMap(map, key);
    return nested == null ? null : RevisionEvidence.fromJson(nested);
  }

  @override
  String toString() =>
      'RevisionChange(identityId: $identityId, status: $status, '
      'claims: ${affectedClaimIds.length})';
}

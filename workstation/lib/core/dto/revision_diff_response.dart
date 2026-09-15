/// `GET /api/v1/projects/{project_id}/revisions/diff/{base}/{target}` response.
///
/// Mirrors `qsagent.api.contracts.RevisionDiffResponse`. The whole point of
/// this DTO is what it does *not* contain: no quantity, no rate, no verdict.
/// It reports what the store holds about two revisions, and it is read-only, so
/// there is no counterpart request model in the client and no `POST` path.
///
/// Three fields tell the reader how much of the answer they are holding:
/// `relationship`, `itemsTruncated`/`itemsOmitted`, and `warnings`. A reader
/// that ignores all three would be reading a comparison that might not be one.
library;

import '../dto_limits.dart';
import 'revision_change.dart';
import 'revision_diff_counts.dart';
import 'revision_document.dart';
import 'strict_json.dart';

class RevisionDiffResponse {
  const RevisionDiffResponse({
    required this.projectId,
    required this.base,
    required this.target,
    required this.relationship,
    required this.counts,
    required this.items,
    required this.itemsTruncated,
    required this.itemsOmitted,
    required this.checkedClaims,
    required this.malformedClaims,
    required this.unverifiedClaimReferences,
    required this.unassociatedClaims,
    required this.warnings,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'project_id',
    'base',
    'target',
    'relationship',
    'counts',
    'items',
    'items_truncated',
    'items_omitted',
    'checked_claims',
    'malformed_claims',
    'unverified_claim_references',
    'unassociated_claims',
    'warnings',
  };

  /// Every relationship the server may declare, mirrored from
  /// `qsagent.api.contracts.DiffRelationship`.
  ///
  /// Only `sameDrawing` means the two sides were established as revisions of
  /// one drawing. `evidenceUnavailable` means one side had no evidence rows at
  /// all, so nothing was classified - most often because the earlier revision's
  /// rows were updated in place by a later ingest.
  static const Set<String> relationships = <String>{
    'same_drawing',
    'unverified_drawing',
    'evidence_unavailable',
  };

  static const String sameDrawing = 'same_drawing';
  static const String unverifiedDrawing = 'unverified_drawing';
  static const String evidenceUnavailable = 'evidence_unavailable';

  final int projectId;
  final RevisionDocument base;
  final RevisionDocument target;
  final String relationship;
  final RevisionDiffCounts counts;

  /// At most [maxDiffItems] classified footprints; see [itemsTruncated].
  final List<RevisionChange> items;

  /// True when [items] is shorter than [counts] says the comparison was.
  final bool itemsTruncated;

  /// How many classified footprints are missing from [items].
  final int itemsOmitted;

  /// Claims whose stored evidence was read and checked against the diff.
  final int checkedClaims;

  /// Claims whose stored evidence column is not a usable JSON array.
  final int malformedClaims;

  /// References whose file hash has no document row, so the filename lineage
  /// could not be verified and no match was attempted.
  final int unverifiedClaimReferences;

  /// Claims that cite the changed document but could not be tied to a specific
  /// evidence row, because no row carries a locator.
  final int unassociatedClaims;

  /// Why the comparison is partial or unverified, at most [maxDiffWarnings].
  final List<String> warnings;

  /// True when the two sides were established as revisions of one drawing.
  bool get relationshipVerified => relationship == sameDrawing;

  /// True when something about this comparison needs a person to look at it.
  bool get needsReview =>
      !relationshipVerified ||
      itemsTruncated ||
      warnings.isNotEmpty ||
      counts.notComparable > 0;

  factory RevisionDiffResponse.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RevisionDiffResponse(
      projectId: requirePositiveInt(map, 'project_id', maxValue: maxProjectId),
      base: RevisionDocument.fromJson(requireNestedMap(map, 'base')),
      target: RevisionDocument.fromJson(requireNestedMap(map, 'target')),
      relationship: requireOneOf(map, 'relationship', allowed: relationships),
      counts: RevisionDiffCounts.fromJson(requireNestedMap(map, 'counts')),
      items: _items(map),
      itemsTruncated: requireBool(map, 'items_truncated'),
      itemsOmitted: requireCount(map, 'items_omitted', maxValue: maxDiffCount),
      checkedClaims: requireCount(
        map,
        'checked_claims',
        maxValue: maxDiffScannedClaims,
      ),
      malformedClaims: requireCount(
        map,
        'malformed_claims',
        maxValue: maxDiffScannedClaims,
      ),
      unverifiedClaimReferences: requireCount(
        map,
        'unverified_claim_references',
        maxValue: maxDiffScannedClaims,
      ),
      unassociatedClaims: requireCount(
        map,
        'unassociated_claims',
        maxValue: maxDiffScannedClaims,
      ),
      warnings: requireBoundedStringList(
        map,
        'warnings',
        maxLength: maxDiffWarnings,
        maxItemLength: maxDiffTextChars,
      ),
    );
  }

  static List<RevisionChange> _items(Map<String, Object?> map) {
    final entries = requireBoundedList(map, 'items', maxLength: maxDiffItems);
    return entries.map(RevisionChange.fromJson).toList(growable: false);
  }

  @override
  String toString() =>
      'RevisionDiffResponse(projectId: $projectId, relationship: $relationship, '
      'items: ${items.length})';
}

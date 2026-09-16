/// `POST /api/v1/projects/{project_id}/rates/proposals/normalize` response.
///
/// Mirrors `qsagent.api.contracts.RateProposalResponse`. Read-only: the request
/// named rate rows the server already holds, and the server read their content
/// itself. Nothing in this response changes the store.
///
/// Three fields tell the reader how much of the answer they are holding:
///
/// * `referenceDate` is the day the ages were measured against. Age is the one
///   input that is not in the store, so it travels with the answer rather than
///   being inferred by whoever renders it;
/// * `normalized` and `unresolved` count the whole answer and always add up to
///   `proposals.length`. There is no truncation here, because the request bounds
///   the answer - one proposal per id, at most `maxRateNodeIds` ids - so a
///   mismatch between those counts and the list is the server contradicting
///   itself;
/// * `warnings` belongs to the request, not to a proposal. The one that matters
///   most is `source_documents_truncated`: it says a source could not be resolved
///   because it sits past a ceiling, not because it is missing, and a reader who
///   read the ceilings as findings would draw a false conclusion about a project.
library;

import '../api_error.dart';
import '../dto_limits.dart';
import 'rate_proposal.dart';
import 'strict_json.dart';

class RateProposalResponse {
  const RateProposalResponse({
    required this.projectId,
    required this.referenceDate,
    required this.proposals,
    required this.normalized,
    required this.unresolved,
    required this.warnings,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'project_id',
    'reference_date',
    'proposals',
    'normalized',
    'unresolved',
    'warnings',
  };

  /// Every request-level warning, mirrored from
  /// `qsagent.api.contracts.RateWarning`.
  ///
  /// Aliased to [RateProposal.warningVocabulary] rather than restated: the
  /// server uses one `Literal` set for a proposal's warnings and the request's,
  /// so two Dart lists here would be two chances to drift apart. Asserted equal
  /// to the server's vocabulary by the mirror suite.
  static const Set<String> warningVocabulary = RateProposal.warningVocabulary;

  /// The request-level warning that marks a ceiling rather than a finding.
  static const String documentsTruncated = 'source_documents_truncated';

  final int projectId;

  /// `YYYY-MM-DD`: the day `sourceAgeDays` was measured against.
  final String referenceDate;

  /// One proposal per id asked for, in the order asked, at most
  /// [maxRateNodeIds] entries.
  final List<RateProposal> proposals;

  /// How many proposals carry a number.
  final int normalized;

  /// How many proposals carry a reason instead.
  final int unresolved;

  /// Qualifications on the request as a whole, at most [maxRateWarnings].
  final List<String> warnings;

  /// True when the answer is internally consistent.
  ///
  /// A response whose counts do not reconcile with its list is one the server
  /// should never emit; refusing it is cheaper than rendering a summary that
  /// disagrees with the rows beneath it.
  bool get isConsistent =>
      normalized + unresolved == proposals.length &&
      normalized == proposals.where((p) => p.isNormalized).length;

  /// True when a source may have been missed because of a ceiling rather than
  /// because it does not exist.
  bool get mayHaveMissedSources => warnings.contains(documentsTruncated);

  /// True when any proposal or the request itself needs a person to look.
  bool get needsReview =>
      warnings.isNotEmpty || proposals.any((p) => p.needsReview);

  factory RateProposalResponse.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    final response = RateProposalResponse(
      projectId: requirePositiveInt(map, 'project_id', maxValue: maxProjectId),
      referenceDate: requireBoundedString(
        map,
        'reference_date',
        maxLength: maxRateDateChars,
      ),
      proposals: _proposals(map),
      normalized: requireCount(map, 'normalized', maxValue: maxRateNodeIds),
      unresolved: requireCount(map, 'unresolved', maxValue: maxRateNodeIds),
      warnings: requireBoundedOneOfList(
        map,
        'warnings',
        allowed: warningVocabulary,
        maxLength: maxRateWarnings,
      ),
    );
    if (!response.isConsistent) {
      throw const ApiFailure(ApiFailureKind.malformed);
    }
    return response;
  }

  static List<RateProposal> _proposals(Map<String, Object?> map) {
    final entries = requireBoundedList(map, 'proposals', maxLength: maxRateNodeIds);
    return entries.map(RateProposal.fromJson).toList(growable: false);
  }

  @override
  String toString() =>
      'RateProposalResponse(projectId: $projectId, '
      'proposals: ${proposals.length}, normalized: $normalized)';
}

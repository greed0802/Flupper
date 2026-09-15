/// How many footprints fell into each comparison status.
///
/// Mirrors `qsagent.api.contracts.RevisionDiffCountsDTO`. These count the whole
/// comparison, so they can exceed the number of items the response carries:
/// `items_truncated` and `items_omitted` on the response are what reconcile the
/// two.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class RevisionDiffCounts {
  const RevisionDiffCounts({
    required this.added,
    required this.removed,
    required this.changed,
    required this.unchanged,
    required this.ambiguous,
    required this.unresolved,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'added',
    'removed',
    'changed',
    'unchanged',
    'ambiguous',
    'unresolved',
  };

  final int added;
  final int removed;
  final int changed;
  final int unchanged;
  final int ambiguous;
  final int unresolved;

  /// Everything that is not a clean comparison, in either direction.
  int get notComparable => ambiguous + unresolved;

  int get total =>
      added + removed + changed + unchanged + ambiguous + unresolved;

  factory RevisionDiffCounts.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RevisionDiffCounts(
      added: requireCount(map, 'added', maxValue: maxDiffCount),
      removed: requireCount(map, 'removed', maxValue: maxDiffCount),
      changed: requireCount(map, 'changed', maxValue: maxDiffCount),
      unchanged: requireCount(map, 'unchanged', maxValue: maxDiffCount),
      ambiguous: requireCount(map, 'ambiguous', maxValue: maxDiffCount),
      unresolved: requireCount(map, 'unresolved', maxValue: maxDiffCount),
    );
  }

  @override
  String toString() =>
      'RevisionDiffCounts(changed: $changed, added: $added, '
      'removed: $removed)';
}

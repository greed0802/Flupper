/// One side of one compared evidence row.
///
/// Mirrors `qsagent.api.contracts.RevisionEvidenceDTO`. Only columns that
/// exist on `evidence_nodes` are described, and two of them carry a caveat the
/// reader has to know: the server reads `drawing_no`, `revision` and `sheet`
/// from the payload when the column is null, because no ingest path in the
/// repository populates those columns, and `raw_text` may arrive cut short -
/// which is what `raw_text_truncated` says.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class RevisionEvidence {
  const RevisionEvidence({
    required this.nodeId,
    required this.identityId,
    required this.nodeType,
    required this.label,
    required this.drawingNo,
    required this.revision,
    required this.sheet,
    required this.page,
    required this.zone,
    required this.fileHash,
    required this.rawText,
    required this.rawTextTruncated,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'node_id',
    'identity_id',
    'node_type',
    'label',
    'drawing_no',
    'revision',
    'sheet',
    'page',
    'zone',
    'file_hash',
    'raw_text',
    'raw_text_truncated',
  };

  final int nodeId;

  /// Canonical identity of the footprint this row belongs to.
  final String identityId;
  final String nodeType;

  /// Stored label. Not a file name: a quantity node's label is an operation.
  final String label;
  final String? drawingNo;
  final String? revision;
  final String? sheet;
  final int? page;
  final String? zone;

  /// The source file hash this row is recorded against, or `null` when the
  /// row carries none.
  final String? fileHash;
  final String? rawText;

  /// True when [rawText] was cut to the server's bound. A locator the reader
  /// cannot see is worse than one marked short.
  final bool rawTextTruncated;

  factory RevisionEvidence.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RevisionEvidence(
      nodeId: requirePositiveInt(map, 'node_id', maxValue: maxDocumentId),
      identityId: requireBoundedString(
        map,
        'identity_id',
        maxLength: maxDiffHashChars,
      ),
      nodeType: requireBoundedString(
        map,
        'node_type',
        maxLength: maxDiffTextChars,
      ),
      label: requireBoundedString(
        map,
        'label',
        minLength: 0,
        maxLength: maxDiffTextChars,
      ),
      drawingNo: requireOptionalBoundedString(
        map,
        'drawing_no',
        maxLength: maxDiffTextChars,
      ),
      revision: requireOptionalBoundedString(
        map,
        'revision',
        maxLength: maxDiffTextChars,
      ),
      sheet: requireOptionalBoundedString(
        map,
        'sheet',
        maxLength: maxDiffTextChars,
      ),
      page: requireOptionalPositiveInt(map, 'page', maxValue: maxPageNumber),
      zone: requireOptionalBoundedString(
        map,
        'zone',
        maxLength: maxDiffTextChars,
      ),
      fileHash: requireOptionalBoundedString(
        map,
        'file_hash',
        maxLength: maxDiffHashChars,
      ),
      rawText: requireOptionalBoundedString(
        map,
        'raw_text',
        maxLength: maxDiffTextChars,
      ),
      rawTextTruncated: requireBool(map, 'raw_text_truncated'),
    );
  }

  @override
  String toString() =>
      'RevisionEvidence(nodeId: $nodeId, identityId: $identityId, '
      'revision: $revision)';
}

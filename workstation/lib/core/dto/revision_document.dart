/// One side of a revision comparison, resolved server-side from its id.
///
/// Mirrors `qsagent.api.contracts.RevisionDocumentDTO`. The client sends two
/// document ids inside a project path and nothing else - no path, no hash, no
/// filename - so everything here was read out of the store by the server.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class RevisionDocument {
  const RevisionDocument({
    required this.documentId,
    required this.fileName,
    required this.fileHash,
    required this.drawingNo,
    required this.revision,
    required this.evidenceRows,
    required this.evidenceTruncated,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'document_id',
    'file_name',
    'file_hash',
    'drawing_no',
    'revision',
    'evidence_rows',
    'evidence_truncated',
  };

  /// Server-assigned identifier, positive and no larger than [maxDocumentId].
  final int documentId;

  /// The name the store records for this hash. This, not a node label, is the
  /// verified filename lineage.
  final String fileName;

  /// Lowercase SHA-256 of the source bytes, as stored.
  final String fileHash;
  final String? drawingNo;
  final String? revision;

  /// How many evidence rows the server found for this document.
  final int evidenceRows;

  /// True when that search hit its ceiling, so the comparison covered only the
  /// rows it read.
  final bool evidenceTruncated;

  factory RevisionDocument.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RevisionDocument(
      documentId: requirePositiveInt(map, 'document_id', maxValue: maxDocumentId),
      fileName: requireBoundedString(
        map,
        'file_name',
        minLength: 0,
        maxLength: maxDiffTextChars,
      ),
      fileHash: requireBoundedString(
        map,
        'file_hash',
        maxLength: maxDiffHashChars,
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
      evidenceRows: requireCount(
        map,
        'evidence_rows',
        maxValue: maxDiffScannedNodes,
      ),
      evidenceTruncated: requireBool(map, 'evidence_truncated'),
    );
  }

  @override
  String toString() =>
      'RevisionDocument(documentId: $documentId, revision: $revision)';
}

/// One rate row, as the gateway read and normalized it.
///
/// Mirrors `qsagent.api.contracts.RateProposalDTO`. The server splits every
/// proposal into exactly two shapes, and the split is the meaning of the row:
///
/// * `normalized` carries a number **and** its whole provenance - source
///   document, file name, file hash, original amount, original unit, normalized
///   unit, currency, category, effective date and source age. A rate without its
///   dimensions is not a rate, so the server refuses to emit half of one;
/// * `unresolved` carries no number and no provenance at all, and always
///   carries a `reason`. A half-populated proposal is exactly the thing a reader
///   would quote as though it were complete.
///
/// Both amounts are **strings**, not numbers. [originalAmount] is the row's own
/// text and [normalizedAmount] is what this platform would quote - the pair
/// exists because a decimal may round (`"1.005"` -> `"1.01"`), and a reader is
/// entitled to both the reading and the rendering. The client renders them and
/// never parses them: `Decimal` arithmetic belongs to the server, and a client
/// that recomputed a rate would be a second implementation of the one thing this
/// platform exists to make single.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class RateProposal {
  const RateProposal({
    required this.nodeId,
    required this.label,
    required this.status,
    required this.confidence,
    required this.reason,
    required this.sourceDocumentId,
    required this.sourceFileName,
    required this.sourceFileHash,
    required this.originalAmount,
    required this.originalUnit,
    required this.normalizedAmount,
    required this.normalizedUnit,
    required this.currency,
    required this.rateCategory,
    required this.effectiveDate,
    required this.sourceAgeDays,
    required this.locatorPresent,
    required this.duplicateQuoteCount,
    required this.warnings,
  });

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{
    'node_id',
    'label',
    'status',
    'confidence',
    'reason',
    'source_document_id',
    'source_file_name',
    'source_file_hash',
    'original_amount',
    'original_unit',
    'normalized_amount',
    'normalized_unit',
    'currency',
    'rate_category',
    'effective_date',
    'source_age_days',
    'locator_present',
    'duplicate_quote_count',
    'warnings',
  };

  /// Every status, mirrored from `qsagent.api.contracts.RateStatus`.
  static const Set<String> statuses = <String>{'normalized', 'unresolved'};

  /// Every confidence level, mirrored from
  /// `qsagent.api.contracts.RateConfidence`.
  ///
  /// Note the third value: `unresolved` is a confidence the server can send, so
  /// a renderer must not read this field as a plain two-value trust scale.
  static const Set<String> confidences = <String>{
    'exact',
    'inferred',
    'unresolved',
  };

  /// Every reason an unresolved proposal can carry, mirrored from
  /// `qsagent.api.contracts.RateUnresolvedReason`.
  ///
  /// The list is long because the server refuses to collapse distinct failures
  /// into one word. A renderer that printed "could not resolve" for all of them
  /// would be discarding the only actionable part of the answer.
  static const Set<String> reasons = <String>{
    'malformed_payload',
    'unknown_payload_keys',
    'missing_source',
    'invalid_document_id',
    'invalid_source_hash',
    'unverified_source',
    'evidence_unavailable',
    'source_mismatch',
    'missing_provenance',
    'unsupported_provenance',
    'missing_amount',
    'invalid_amount',
    'missing_unit',
    'unsupported_unit',
    'missing_category',
    'unsupported_category',
    'unit_category_mismatch',
    'missing_currency',
    'unsupported_currency',
    'missing_effective_date',
    'invalid_effective_date',
  };

  /// Every rate category, mirrored from
  /// `qsagent.api.contracts.RateCategory`.
  static const Set<String> categories = <String>{
    'plant_time',
    'labour_time',
    'material_unit',
    'volume_cart_away',
    'subcontract_lumpsum',
  };

  /// Every currency, mirrored from `qsagent.api.contracts.RateCurrency`.
  static const Set<String> currencies = <String>{'AUD'};

  /// Every unit a proposal may be quoted in, mirrored from
  /// `qsagent.api.contracts.Unit`.
  ///
  /// The server aliases this to the quantity-claim unit enum rather than
  /// restating it, so a unit a rate can be stored in is a unit a claim can be
  /// measured in. The mirror test asserts this set equals that enum's values, so
  /// neither list can grow alone.
  static const Set<String> units = <String>{
    'm',
    'm2',
    'm3',
    'mm',
    'kg',
    't',
    'ea',
    'item',
    'hr',
    'L',
  };

  /// Every warning a proposal can carry, mirrored from
  /// `qsagent.api.contracts.RateWarning`.
  ///
  /// Qualifications rather than failures: a row can be normalized and still
  /// carry `stale_source` or `duplicate_source_quote`. A renderer that dropped
  /// them would present a qualified rate as an unqualified one.
  static const Set<String> warningVocabulary = <String>{
    'locator_unavailable',
    'sheet_or_page_unavailable',
    'manual_provenance',
    'stale_source',
    'duplicate_source_quote',
    'source_documents_truncated',
  };

  /// The one status that means the row carries a number.
  static const String normalizedStatus = 'normalized';
  final int nodeId;

  /// The stored label. Not a file name: a quantity node's label is an operation.
  final String label;
  final String status;
  final String confidence;

  /// Why there is no number, present exactly when [status] is not `normalized`.
  final String? reason;

  /// Server id of the document the rate was read from. Present exactly when the
  /// proposal is normalized.
  final int? sourceDocumentId;
  final String? sourceFileName;

  /// Lowercase SHA-256 of the source bytes, as stored.
  final String? sourceFileHash;

  /// The amount as the store holds it, as text.
  final String? originalAmount;
  final String? originalUnit;

  /// The amount as this platform would quote it, as text. Never recomputed here.
  final String? normalizedAmount;

  /// The canonical unit. No conversion is applied, so this spells the same unit
  /// as [originalUnit] does; the pair is kept for the same reason the amount pair
  /// is.
  final String? normalizedUnit;
  final String? currency;
  final String? rateCategory;

  /// `YYYY-MM-DD`, as stored.
  final String? effectiveDate;

  /// Days between [effectiveDate] and the response's `referenceDate`.
  ///
  /// Age is the one input that is not in the store, so it travels with the
  /// answer: a reader can recompute it from the date and the reference date
  /// instead of trusting a figure that depended on when the request ran.
  final int? sourceAgeDays;

  /// Whether the row ties to a locator a person could go and check. A rate with
  /// no locator is a rate nobody can verify.
  final bool locatorPresent;

  /// How many other rows quote the same source. Non-zero means this rate is not
  /// unique in the project.
  final int duplicateQuoteCount;

  /// Qualifications on this proposal, at most [maxRateWarnings].
  final List<String> warnings;

  /// True when this proposal carries a number and its whole provenance.
  bool get isNormalized => status == normalizedStatus;

  /// True when this proposal is a reason rather than a rate.
  bool get isUnresolved => !isNormalized;

  /// True when something about this proposal needs a person to look at it: an
  /// unresolved row, or a normalized row the server qualified.
  bool get needsReview => isUnresolved || warnings.isNotEmpty;

  factory RateProposal.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return RateProposal(
      nodeId: requirePositiveInt(map, 'node_id', maxValue: maxRateExactInt),
      label: requireBoundedString(
        map,
        'label',
        minLength: 0,
        maxLength: maxRateTextChars,
      ),
      status: requireOneOf(map, 'status', allowed: statuses),
      confidence: requireOneOf(map, 'confidence', allowed: confidences),
      reason: requireOptionalOneOf(map, 'reason', allowed: reasons),
      sourceDocumentId: requireOptionalPositiveInt(
        map,
        'source_document_id',
        maxValue: maxRateExactInt,
      ),
      sourceFileName: requireOptionalBoundedString(
        map,
        'source_file_name',
        maxLength: maxRateTextChars,
      ),
      sourceFileHash: requireOptionalBoundedString(
        map,
        'source_file_hash',
        maxLength: maxRateHashChars,
      ),
      originalAmount: requireOptionalBoundedString(
        map,
        'original_amount',
        maxLength: maxRateAmountChars,
      ),
      originalUnit: requireOptionalBoundedString(
        map,
        'original_unit',
        maxLength: maxRateTextChars,
      ),
      normalizedAmount: requireOptionalBoundedString(
        map,
        'normalized_amount',
        maxLength: maxRateAmountChars,
      ),
      normalizedUnit: requireOptionalOneOf(
        map,
        'normalized_unit',
        allowed: units,
      ),
      currency: requireOptionalOneOf(map, 'currency', allowed: currencies),
      rateCategory: requireOptionalOneOf(
        map,
        'rate_category',
        allowed: categories,
      ),
      effectiveDate: requireOptionalBoundedString(
        map,
        'effective_date',
        maxLength: maxRateDateChars,
      ),
      sourceAgeDays: requireOptionalCount(
        map,
        'source_age_days',
        maxValue: maxRateExactInt,
      ),
      locatorPresent: requireBool(map, 'locator_present'),
      duplicateQuoteCount: requireCount(
        map,
        'duplicate_quote_count',
        maxValue: maxRateExactInt,
      ),
      warnings: requireBoundedOneOfList(
        map,
        'warnings',
        allowed: warningVocabulary,
        maxLength: maxRateWarnings,
      ),
    );
  }

  @override
  String toString() =>
      'RateProposal(nodeId: $nodeId, status: $status, '
      'category: $rateCategory)';
}


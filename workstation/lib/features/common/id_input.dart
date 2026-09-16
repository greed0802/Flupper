/// Turning what a person typed into an identifier the route can carry.
///
/// The 5A project pane has its own copy of this logic and keeps it: that pane
/// is verified and its tests pin its wording. These helpers exist so the two
/// tablet panes - which each collect *three* identifiers, and one of them a
/// list - do not end up with a third and fourth hand-written copy of the same
/// four refusals.
///
/// Every refusal is a fixed local sentence. Nothing here echoes the typed value
/// back: a mistyped identifier is the input most likely to be a mistyped secret,
/// and the same rule already governs [ApiConfigException] and [ApiFailure].
///
/// Why digits are checked by hand instead of by `int.tryParse`
/// ----------------------------------------------------------
/// `int.tryParse('１２')` succeeds: Dart parses every Unicode decimal digit, and
/// those are not the digits the route declares. `'1e3'`, `'1.0'`, `'+1'` and
/// `' 1 '` are likewise convertible or nearly so. A regex over ASCII digits is
/// the only check that describes the wire format the server actually accepts.
library;

/// A value that is either an accepted identifier or a reason it is not.
class ParsedId {
  const ParsedId._(this.value, this.error);

  /// An accepted identifier.
  const ParsedId.valid(int value) : this._(value, null);

  /// A refused input, with fixed local copy explaining the refusal.
  const ParsedId.invalid(String error) : this._(null, error);

  /// The parsed identifier, or `null` when the input was refused.
  final int? value;

  /// Fixed local copy, or `null` when the input was accepted.
  final String? error;

  /// True when [value] may be used.
  bool get isValid => error == null;
}

/// The one sentence used when a value is not a whole number greater than zero.
const String idNotAWholeNumber = 'Enter a whole number greater than zero.';

/// Parses one identifier.
///
/// [noun] names the field in the empty-input sentence, [ceiling] names the
/// ceiling in the too-large sentence. Both ceilings are the client's, and a
/// value past one is refused locally rather than sent and rounded on the way
/// into a URL: a request that names a different row than the one typed is worse
/// than a request that is not made.
ParsedId parseId(
  String raw, {
  required int maxValue,
  required String noun,
  required String ceiling,
}) {
  final text = raw.trim();
  if (text.isEmpty) {
    return ParsedId.invalid('Enter a $noun.');
  }
  if (!RegExp(r'^[0-9]+$').hasMatch(text)) {
    return ParsedId.invalid(idNotAWholeNumber);
  }
  final parsed = int.tryParse(text);
  if (parsed == null || parsed < 1) {
    // A digit string longer than any integer holds lands here: `tryParse`
    // answers `null` rather than clamping, so it is refused, not rounded.
    return ParsedId.invalid(idNotAWholeNumber);
  }
  if (parsed > maxValue) {
    return ParsedId.invalid(ceiling);
  }
  return ParsedId.valid(parsed);
}

/// A list of identifiers, as text, and what is wrong with it.
class ParsedIdList {
  const ParsedIdList._(this.values, this.error);

  /// An accepted list, in the order it was written.
  const ParsedIdList.valid(List<int> values) : this._(values, null);

  /// A refused list, with fixed local copy explaining the refusal.
  const ParsedIdList.invalid(String error) : this._(null, error);

  /// The parsed identifiers, or `null` when the input was refused.
  final List<int>? values;

  /// Fixed local copy, or `null` when the input was accepted.
  final String? error;

  /// True when [values] may be sent.
  bool get isValid => error == null;
}

/// Parses a bounded list of identifiers separated by commas or whitespace.
///
/// The list is sent as written, duplicates included. The server deduplicates
/// while keeping the caller's order, so removing a duplicate here would make the
/// client's count differ from the request the server answers about - and because
/// the bound is a bound on the *request*, a repeated id counts against it.
ParsedIdList parseIdList(
  String raw, {
  required int maxValue,
  required int maxItems,
  required String noun,
  required String ceiling,
}) {
  final pieces = raw
      .split(RegExp(r'[,\s]+'))
      .where((piece) => piece.isNotEmpty)
      .toList(growable: false);

  if (pieces.isEmpty) {
    return ParsedIdList.invalid('Enter at least one $noun.');
  }
  if (pieces.length > maxItems) {
    return ParsedIdList.invalid(
      'That is more than $maxItems ${noun}s, which is all one request may name.',
    );
  }

  final values = <int>[];
  for (final piece in pieces) {
    final parsed = parseId(
      piece,
      maxValue: maxValue,
      noun: noun,
      ceiling: ceiling,
    );
    if (!parsed.isValid) {
      // The first refused entry decides the sentence. Reporting every bad entry
      // at once would need a list the field cannot show; reporting the first is
      // enough for the person to fix it and see the next.
      //
      // `parsed.error` is guaranteed non-null here: `parsed.isValid` is false,
      // and the constructor that accepts sets no error while the one that refuses
      // sets no value. The `!` is a local shape assertion, not a dropped check.
      return ParsedIdList.invalid(parsed.error!);
    }
    values.add(parsed.value!);
  }
  return ParsedIdList.valid(values);
}
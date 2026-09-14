/// Strict readers for one decoded JSON object.
///
/// "Strict" means three things, and each is a test in
/// `test/core/dto_test.dart`:
///
/// * the value must be a JSON object, not a list or a scalar;
/// * the key set must be exactly the declared one - a missing key and an
///   unknown key are both refusals, so a response cannot quietly grow a field
///   the UI never agreed to render;
/// * every field is re-bounded locally.
///
/// Failures leave as [ApiFailure] with [ApiFailureKind.malformed] and carry no
/// fragment of the offending value, because the offending value is untrusted.
library;

import '../api_error.dart';

/// Thrown by the readers below; converted by each DTO's `fromJson`.
ApiFailure _malformed() => const ApiFailure(ApiFailureKind.malformed);

/// Requires [decoded] to be a JSON object with exactly [keys].
Map<String, Object?> requireStrictMap(
  Object? decoded, {
  required Set<String> keys,
}) {
  if (decoded is! Map) {
    throw _malformed();
  }
  final map = <String, Object?>{};
  for (final entry in decoded.entries) {
    final key = entry.key;
    if (key is! String) {
      throw _malformed();
    }
    map[key] = entry.value;
  }
  if (map.length != keys.length || !keys.every(map.containsKey)) {
    throw _malformed();
  }
  return map;
}

/// Requires a string field within `[minLength, maxLength]` characters.
String requireBoundedString(
  Map<String, Object?> map,
  String key, {
  int minLength = 1,
  required int maxLength,
}) {
  final value = map[key];
  if (value is! String) {
    throw _malformed();
  }
  if (value.length < minLength || value.length > maxLength) {
    throw _malformed();
  }
  return value;
}

/// Requires a boolean field.
bool requireBool(Map<String, Object?> map, String key) {
  final value = map[key];
  if (value is! bool) {
    throw _malformed();
  }
  return value;
}

/// Requires an integer field in `[1, maxValue]`.
///
/// Rejects a `double` even when it is integral: `1.0` is not the number the
/// server declared, and accepting it hides a serialisation drift.
int requirePositiveInt(
  Map<String, Object?> map,
  String key, {
  required int maxValue,
}) {
  final value = map[key];
  if (value is! int) {
    throw _malformed();
  }
  if (value < 1 || value > maxValue) {
    throw _malformed();
  }
  return value;
}

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

/// Requires an integer field in `[0, maxValue]` - a count, not an identifier.
int requireCount(Map<String, Object?> map, String key, {required int maxValue}) {
  final value = map[key];
  if (value is! int) {
    throw _malformed();
  }
  if (value < 0 || value > maxValue) {
    throw _malformed();
  }
  return value;
}

/// Requires a nullable integer field in `[1, maxValue]`, or `null`.
int? requireOptionalPositiveInt(
  Map<String, Object?> map,
  String key, {
  required int maxValue,
}) {
  if (map[key] == null) {
    return null;
  }
  return requirePositiveInt(map, key, maxValue: maxValue);
}

/// Requires a nullable string field within `[minLength, maxLength]`.
///
/// A JSON `null` reads as `null`; anything else must be a bounded string. An
/// empty string is a decision each field makes through [minLength], because
/// for a label `""` is a value the server can send.
String? requireOptionalBoundedString(
  Map<String, Object?> map,
  String key, {
  int minLength = 1,
  required int maxLength,
}) {
  if (map[key] == null) {
    return null;
  }
  return requireBoundedString(map, key, minLength: minLength, maxLength: maxLength);
}

/// Requires a list field with at most [maxLength] entries.
List<Object?> requireBoundedList(
  Map<String, Object?> map,
  String key, {
  required int maxLength,
}) {
  final value = map[key];
  if (value is! List) {
    throw _malformed();
  }
  if (value.length > maxLength) {
    throw _malformed();
  }
  return List<Object?>.from(value);
}

/// Requires a list of bounded strings, with at most [maxLength] entries.
List<String> requireBoundedStringList(
  Map<String, Object?> map,
  String key, {
  int minItemLength = 1,
  required int maxItemLength,
  required int maxLength,
}) {
  final values = requireBoundedList(map, key, maxLength: maxLength);
  final strings = <String>[];
  for (final value in values) {
    if (value is! String) {
      throw _malformed();
    }
    if (value.length < minItemLength || value.length > maxItemLength) {
      throw _malformed();
    }
    strings.add(value);
  }
  return strings;
}

/// Requires a string field whose value is one of a fixed vocabulary.
String requireOneOf(
  Map<String, Object?> map,
  String key, {
  required Set<String> allowed,
}) {
  final value = map[key];
  if (value is! String || !allowed.contains(value)) {
    throw _malformed();
  }
  return value;
}

/// Requires a nested JSON object, returned as a plain map.
Map<String, Object?> requireNestedMap(Map<String, Object?> map, String key) {
  final value = map[key];
  if (value is! Map) {
    throw _malformed();
  }
  return Map<String, Object?>.from(value);
}

/// Requires a nested JSON object or `null`.
Map<String, Object?>? requireOptionalNestedMap(
  Map<String, Object?> map,
  String key,
) {
  if (map[key] == null) {
    return null;
  }
  return requireNestedMap(map, key);
}


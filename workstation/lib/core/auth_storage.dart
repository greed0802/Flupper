/// The bearer token, held in memory for the lifetime of one run and nowhere
/// else.
///
/// Persistence is **off**. There is no preferences write, no secure-storage
/// write, no cookie and no browser-storage call anywhere in this file, and
/// [persistenceEnabled] is a compile-time `false` so that turning any of that
/// on is a deliberate, reviewed edit rather than a flag someone flips.
///
/// The token is also never a compile-time value on web.
/// [AuthStorage.maxTokenChars] bounds it, [validateToken] refuses any character
/// that could break out of an HTTP header, and [toString] is redacted so a token
/// cannot reach a log line through an object dump.
library;

import 'package:flutter/foundation.dart' show kIsWeb;

import 'platform/dev_env.dart';

/// Seeds a token from the desktop development environment, or `null`.
///
/// Returns `null` unconditionally on web, before any environment read happens:
/// a browser build has no legitimate environment-supplied token, so the guard
/// is a platform check rather than a convention.
///
/// [readEnvironment] is injectable so a test on the VM can prove the seeding
/// rule without a real environment variable.
String? seedTokenFromDevEnvironment({String? Function()? readEnvironment}) {
  if (kIsWeb) {
    return null;
  }
  final raw = (readEnvironment ?? readDevTokenEnvironment)();
  if (raw == null) {
    return null;
  }
  final trimmed = raw.trim();
  return trimmed.isEmpty ? null : trimmed;
}

class AuthStorage {
  AuthStorage();

  /// Longest token accepted. The gateway's tokens are far shorter; this is a
  /// ceiling, not a policy.
  static const int maxTokenChars = 512;

  /// Shortest token accepted. An empty token is not a credential.
  static const int minTokenChars = 1;

  /// Whether any token survives a restart.
  ///
  /// `false` is the whole design of 5A. A future phase that wants persistence
  /// has to change this constant and answer the keychain, logout, shared-device
  /// and rotation questions first; until then a test asserts it stays `false`.
  static const bool persistenceEnabled = false;

  String? _token;

  /// Whether a token is currently held.
  bool get hasToken => _token != null;

  /// The token, for building an `Authorization` header.
  ///
  /// Returning the value rather than the header keeps this class free of
  /// transport concerns; [ApiClient] is the only caller and the only place the
  /// header is assembled.
  String? get token => _token;

  /// Why [raw] is not an acceptable token, or `null` when it is.
  ///
  /// Reasons are fixed local copy. The offending value is never echoed.
  String? validateToken(String raw) {
    if (raw.length < minTokenChars) {
      return 'Enter a token.';
    }
    if (raw.length > maxTokenChars) {
      return 'That token is longer than $maxTokenChars characters.';
    }
    for (final unit in raw.codeUnits) {
      // Rejects every control character, space and non-ASCII byte. A token
      // carrying one of those could not be sent as a Bearer value anyway, and
      // a CR or LF would be a header-injection attempt.
      if (unit <= 0x20 || unit >= 0x7f) {
        return 'The token contains characters a bearer header cannot carry.';
      }
    }
    return null;
  }

  /// Stores [raw] when it validates. Returns `false` and stores nothing
  /// otherwise, so a rejected token can never become the active one.
  bool setToken(String raw) {
    if (validateToken(raw) != null) {
      return false;
    }
    _token = raw;
    return true;
  }

  /// Drops the token. Called on sign-out and on any 401.
  void clear() {
    _token = null;
  }

  /// Redacted, always. An object dump must not be a way to read the token.
  @override
  String toString() => 'AuthStorage(token: ${hasToken ? '<redacted>' : '<none>'})';
}

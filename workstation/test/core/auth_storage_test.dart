import 'package:flutter/foundation.dart' show kIsWeb;
import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/auth_storage.dart';

void main() {
  group('validateToken', () {
    test('refuses an empty token', () {
      final auth = AuthStorage();
      expect(auth.validateToken(''), isNotNull);
      expect(auth.setToken(''), isFalse);
      expect(auth.hasToken, isFalse);
    });

    test('refuses a token longer than the ceiling', () {
      final auth = AuthStorage();
      final tooLong = 'a' * (AuthStorage.maxTokenChars + 1);
      expect(auth.validateToken(tooLong), isNotNull);
      expect(auth.setToken(tooLong), isFalse);
      expect(auth.hasToken, isFalse);
    });

    test('accepts a token exactly at the ceiling', () {
      final auth = AuthStorage();
      final atLimit = 'a' * AuthStorage.maxTokenChars;
      expect(auth.validateToken(atLimit), isNull);
      expect(auth.setToken(atLimit), isTrue);
    });

    test('refuses characters a bearer header cannot carry', () {
      final auth = AuthStorage();
      for (final bad in <String>['has space', 'line\nfeed', 'carriage\rreturn', 'tab\there', 'café']) {
        expect(auth.validateToken(bad), isNotNull, reason: bad);
        expect(auth.setToken(bad), isFalse, reason: bad);
      }
    });

    test('refuses a deletion character', () {
      final auth = AuthStorage();
      expect(auth.validateToken('a\u007fb'), isNotNull);
    });

    test('accepts the base64url and base64 shapes a token actually uses', () {
      final auth = AuthStorage();
      for (final good in <String>[
        'abcDEF0123456789',
        'a-b_c.d~e',
        'AAAA+/==',
      ]) {
        expect(auth.validateToken(good), isNull, reason: good);
        expect(auth.setToken(good), isTrue, reason: good);
      }
    });
  });

  group('storage', () {
    test('clear drops the token and setToken cannot resurrect it', () {
      final auth = AuthStorage();
      expect(auth.setToken('token-value'), isTrue);
      expect(auth.hasToken, isTrue);
      auth.clear();
      expect(auth.hasToken, isFalse);
      expect(auth.token, isNull);
    });

    test('a refused token never becomes the active one', () {
      final auth = AuthStorage();
      expect(auth.setToken('good-token'), isTrue);
      expect(auth.setToken('bad token'), isFalse);
      expect(auth.token, 'good-token');
    });

    test('toString is redacted', () {
      final auth = AuthStorage();
      expect(auth.toString().contains('<redacted>'), isFalse);
      auth.setToken('super-secret-token');
      expect(auth.toString().contains('super-secret-token'), isFalse);
      expect(auth.toString(), contains('<redacted>'));
    });

    test('persistence is off, so nothing can be written anywhere', () {
      // The guarantee is the constant: enabling persistence has to be a
      // deliberate edit to this line, not a runtime flag.
      expect(AuthStorage.persistenceEnabled, isFalse);
    });
  });

  group('seedTokenFromDevEnvironment', () {
    test('is read on desktop and ignored on web', () {
      final seeded = seedTokenFromDevEnvironment(
        readEnvironment: () => '  dev-token  ',
      );
      if (kIsWeb) {
        expect(seeded, isNull, reason: 'a browser build must never seed a token');
      } else {
        expect(seeded, 'dev-token');
      }
    });

    test('a blank environment value is not a token', () {
      expect(
        seedTokenFromDevEnvironment(readEnvironment: () => '   '),
        isEmptyOrNull(),
      );
    });

    test('an absent environment value is not a token', () {
      expect(
        seedTokenFromDevEnvironment(readEnvironment: () => null),
        isNull,
      );
    });
  });
}

/// Matches `null` or the empty string, for tests that run on both platforms.
Matcher isEmptyOrNull() => anyOf(isNull, isEmpty);

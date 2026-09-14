import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/api_error.dart';
import 'package:flupper_workstation/core/auth_storage.dart';

import '../support/fake_transport.dart';

/// The status-to-kind table, and the two rules that matter more than the table:
/// the server's `message` is never retained, and a body over the ceiling never
/// masks the status it arrived with.
void main() {
  final config = ApiConfig.parse('http://127.0.0.1:8000');

  Future<ApiFailure> failureFrom(FakeTransport transport) async {
    final api = ApiClient(
      config: config,
      auth: AuthStorage()..setToken('test-token'),
    );
    try {
      await api.fetchHealth(client: transport);
    } on ApiFailure catch (failure) {
      return failure;
    }
    fail('expected an ApiFailure');
  }

  Future<ApiFailure> failureForStatus(int status) => failureFrom(
    FakeTransport((_) async => errorEnvelope(status, 'HTTPException')),
  );

  group('status mapping', () {
    test('401 is unauthorized and means sign out', () async {
      final failure = await failureForStatus(401);
      expect(failure.kind, ApiFailureKind.unauthorized);
      expect(failure.isAuthenticationFailure, isTrue);
      expect(failure.statusCode, 401);
    });

    test('403 is forbidden', () async {
      final failure = await failureForStatus(403);
      expect(failure.kind, ApiFailureKind.forbidden);
      expect(failure.isAuthenticationFailure, isFalse);
    });

    test('404 is not found', () async {
      expect((await failureForStatus(404)).kind, ApiFailureKind.notFound);
    });

    test('413 is payload too large', () async {
      expect((await failureForStatus(413)).kind, ApiFailureKind.payloadTooLarge);
    });

    test('422 is validation', () async {
      expect((await failureForStatus(422)).kind, ApiFailureKind.validation);
    });

    test('5xx is a server failure, whatever the code', () async {
      for (final status in <int>[500, 502, 503, 504]) {
        expect(
          (await failureForStatus(status)).kind,
          ApiFailureKind.server,
          reason: '$status',
        );
      }
    });

    test('an unmodelled 4xx is unexpected, not silent', () async {
      expect((await failureForStatus(418)).kind, ApiFailureKind.unexpected);
    });
  });

  group('authentication wins over a large body', () {
    test('an oversized 401 is still a sign-out', () async {
      final failure = await failureFrom(
        FakeTransport((_) async => oversizedResponse(401)),
      );
      expect(failure.kind, ApiFailureKind.unauthorized);
    });

    test('an oversized 403 is still a refusal of the action', () async {
      final failure = await failureFrom(
        FakeTransport((_) async => oversizedResponse(403)),
      );
      expect(failure.kind, ApiFailureKind.forbidden);
    });

    test('an oversized 500 is reported as an oversized body', () async {
      final failure = await failureFrom(
        FakeTransport((_) async => oversizedResponse(500)),
      );
      expect(failure.kind, ApiFailureKind.payloadTooLarge);
      expect(failure.statusCode, 500);
    });
  });

  group('the envelope is read for structure only', () {
    test('the server message is never retained or rendered', () async {
      final failure = await failureFrom(
        FakeTransport(
          (_) async => errorEnvelope(
            500,
            'SandboxError',
            message: 'a path fragment /srv/private that must not surface',
          ),
        ),
      );

      expect(failure.errorClass, 'SandboxError');
      expect(failure.userMessage, 'The gateway reported an internal error.');
      expect(failure.userMessage.contains('/srv/private'), isFalse);
      expect(failure.toString().contains('/srv/private'), isFalse);
    });

    test('a class name longer than the bound is dropped, not truncated', () async {
      final failure = await failureFrom(
        FakeTransport(
          (_) async => errorEnvelope(500, 'X' * (ApiFailure.maxErrorClassChars + 1)),
        ),
      );
      expect(failure.errorClass, isNull);
    });

    test('422 field locations are kept, bounded in count and width', () async {
      final failure = await failureFrom(
        FakeTransport(
          (_) async => errorEnvelope(
            422,
            'RequestValidationError',
            fields: <String>[
              for (var index = 0; index < 40; index++) 'body.field$index',
              'x' * (ApiFailure.maxFieldChars + 1),
            ],
          ),
        ),
      );

      expect(failure.fields, hasLength(ApiFailure.maxFields));
      for (final field in failure.fields) {
        expect(field.length <= ApiFailure.maxFieldChars, isTrue);
      }
      expect(failure.fields.every((field) => field.startsWith('body.')), isTrue);
    });

    test('an error body that is not JSON does not change the status mapping', () async {
      final failure = await failureFrom(
        FakeTransport((_) async => streamedResponse(404, '<html>not found</html>')),
      );
      expect(failure.kind, ApiFailureKind.notFound);
      expect(failure.errorClass, isNull);
      expect(failure.fields, isEmpty);
    });

    test('an error body that is a JSON list is ignored, not parsed', () async {
      final failure = await failureFrom(
        FakeTransport((_) async => streamedResponse(422, '[1, 2, 3]')),
      );
      expect(failure.kind, ApiFailureKind.validation);
      expect(failure.errorClass, isNull);
    });
  });
}

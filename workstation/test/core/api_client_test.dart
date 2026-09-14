import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/api_error.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/core/dto_limits.dart';

import '../support/fake_transport.dart';

void main() {
  final config = ApiConfig.parse('http://127.0.0.1:8000');

  ApiClient apiClient(FakeTransport transport, {AuthStorage? auth}) {
    final storage = auth ?? (AuthStorage()..setToken('test-token'));
    return ApiClient(config: config, auth: storage);
  }

  Matcher failureOf(ApiFailureKind kind) =>
      isA<ApiFailure>().having((failure) => failure.kind, 'kind', kind);

  group('request construction', () {
    test('GET /api/v1/health, with the prefix spelled out', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'status': 'ok',
          'sandbox_root_ready': true,
        }),
      );

      await apiClient(transport).fetchHealth(client: transport);

      expect(transport.requests, hasLength(1));
      final request = transport.requests.single;
      expect(request.method, 'GET');
      expect(request.url.toString(), 'http://127.0.0.1:8000/api/v1/health');
      expect(transport.acceptAt(0), 'application/json');
      expect(transport.authorizationAt(0), 'Bearer test-token');
    });

    test('GET /api/v1/projects/{id}, with the prefix spelled out', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'project_id': 42,
          'name': 'Example project',
        }),
      );

      await apiClient(transport).fetchProject(projectId: 42, client: transport);

      expect(
        transport.requests.single.url.toString(),
        'http://127.0.0.1:8000/api/v1/projects/42',
      );
      expect(transport.authorizationAt(0), 'Bearer test-token');
    });

    test('reads the token per call, so a sign-out takes effect at once', () async {
      final auth = AuthStorage()..setToken('first-token');
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'status': 'ok',
          'sandbox_root_ready': false,
        }),
      );
      final api = apiClient(transport, auth: auth);

      await api.fetchHealth(client: transport);
      auth.setToken('second-token');
      await api.fetchHealth(client: transport);

      expect(transport.authorizationAt(0), 'Bearer first-token');
      expect(transport.authorizationAt(1), 'Bearer second-token');
    });

    test('sends nothing at all when no token is held', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{}),
      );
      final api = apiClient(transport, auth: AuthStorage());

      await expectLater(
        api.fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.noToken)),
      );

      expect(transport.requests, isEmpty);
    });

    test('refuses a project id outside the declared range without sending', () {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{}),
      );
      final api = apiClient(transport);

      for (final bad in <int>[-1, 0, maxProjectId + 1]) {
        expect(
          () => api.fetchProject(projectId: bad, client: transport),
          throwsA(failureOf(ApiFailureKind.invalidRequest)),
          reason: '$bad',
        );
      }
      expect(transport.requests, isEmpty);
    });
  });

  group('successful reads', () {
    test('health parses into the DTO', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'status': 'ok',
          'sandbox_root_ready': true,
        }),
      );

      final health = await apiClient(transport).fetchHealth(client: transport);
      expect(health.status, 'ok');
      expect(health.sandboxRootReady, isTrue);
    });

    test('project parses into the DTO', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'project_id': 7,
          'name': 'Example project',
        }),
      );

      final project = await apiClient(
        transport,
      ).fetchProject(projectId: 7, client: transport);
      expect(project.projectId, 7);
      expect(project.name, 'Example project');
    });
  });

  group('bounded responses', () {
    test('a 200 whose body is not JSON is a malformed failure', () async {
      final transport = FakeTransport(
        (_) async => streamedResponse(200, 'not json'),
      );

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.malformed)),
      );
    });

    test('a 200 whose body is a JSON list is refused, not coerced', () async {
      final transport = FakeTransport(
        (_) async => streamedResponse(200, jsonEncode(<int>[1, 2, 3])),
      );

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.malformed)),
      );
    });

    test('a 200 whose body is not UTF-8 is refused, not crashed on', () async {
      final transport = FakeTransport(
        (_) async => rawResponse(200, <int>[0x7b, 0xff, 0xfe, 0x7d]),
      );

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.malformed)),
      );
    });

    test('a body over the ceiling is refused on a 200', () async {
      final transport = FakeTransport((_) async => oversizedResponse(200));

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.payloadTooLarge)),
      );
    });

    test('a body over the ceiling is refused on a 500', () async {
      final transport = FakeTransport((_) async => oversizedResponse(500));

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.payloadTooLarge)),
      );
    });

    test('an endless body stops the read instead of hanging', () async {
      final transport = FakeTransport((_) async => endlessResponse());

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.payloadTooLarge)),
      );
    });
  });

  group('transport failure', () {
    test('sending through a transport closed mid-flight fails, not hangs', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{}),
      );
      transport.close();

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.offline)),
      );
    });

    test('a request still open at the deadline is a timeout', () async {
      final transport = FakeTransport((_) => neverResponds());
      final api = ApiClient(
        config: config,
        auth: AuthStorage()..setToken('test-token'),
        timeout: const Duration(milliseconds: 20),
      );

      await expectLater(
        api.fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.timeout)),
      );
    });

    test('a refused connection is offline, not a server error', () async {
      final transport = FakeTransport(
        (request) => connectionRefused(request.url),
      );

      await expectLater(
        apiClient(transport).fetchHealth(client: transport),
        throwsA(failureOf(ApiFailureKind.offline)),
      );
    });
  });
}

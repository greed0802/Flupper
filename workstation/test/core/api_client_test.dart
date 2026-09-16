import 'dart:convert';

import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/api_error.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/core/dto_limits.dart';

import '../support/fake_transport.dart';

/// A minimal, valid revision diff body.
///
/// Shape only. The bounds are proven in `dto_test.dart`; here it exists so a
/// transport test can assert the URL, the method and the failure mapping.
Map<String, Object?> revisionDiffBody({
  String relationship = 'unverified_drawing',
  List<Object?>? items,
}) {
  String digest(String seed) => List<String>.filled(64, seed).join();

  Map<String, Object?> side(int id, String revision) => <String, Object?>{
    'document_id': id,
    'file_name': 'synthetic.pdf',
    'file_hash': digest('a'),
    'drawing_no': 'C-204',
    'revision': revision,
    'evidence_rows': 0,
    'evidence_truncated': false,
  };

  return <String, Object?>{
    'project_id': 42,
    'base': side(7, 'A'),
    'target': side(8, 'B'),
    'relationship': relationship,
    'counts': <String, Object?>{
      'added': 0,
      'removed': 0,
      'changed': 0,
      'unchanged': 0,
      'ambiguous': 0,
      'unresolved': 0,
    },
    'items': items ?? <Object?>[],
    'items_truncated': false,
    'items_omitted': 0,
    'checked_claims': 0,
    'malformed_claims': 0,
    'unverified_claim_references': 0,
    'unassociated_claims': 0,
    'warnings': <String>['base_document_has_no_evidence_rows'],
  };
}


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

    test('GET the revision diff, with both documents and no hash in sight',
        () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, revisionDiffBody()),
      );

      await apiClient(transport).fetchRevisionDiff(
        projectId: 42,
        baseDocumentId: 7,
        targetDocumentId: 8,
        client: transport,
      );

      final url = transport.requests.single.url.toString();
      expect(
        url,
        'http://127.0.0.1:8000/api/v1/projects/42/revisions/diff/7/8',
      );
      expect(transport.requests.single.method, 'GET');
      expect(transport.authorizationAt(0), 'Bearer test-token');
      // The caller names documents by id. There is no argument through which a
      // file name, a hash or a path could be nominated.
      expect(url.contains('hash'), isFalse);
      expect(url.contains('..'), isFalse);
    });

    test('refuses a document id outside the declared range without sending', () {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{}),
      );
      final api = apiClient(transport);

      for (final bad in <int>[-1, 0, maxDocumentId + 1]) {
        expect(
          () => api.fetchRevisionDiff(
            projectId: 42,
            baseDocumentId: bad,
            targetDocumentId: 8,
            client: transport,
          ),
          throwsA(failureOf(ApiFailureKind.invalidRequest)),
          reason: '$bad',
        );
      }
      expect(transport.requests, isEmpty);
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

  group('revision diff reads', () {
    test('an empty comparison parses, and still reports its warning', () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, revisionDiffBody()),
      );

      final diff = await apiClient(transport).fetchRevisionDiff(
        projectId: 42,
        baseDocumentId: 7,
        targetDocumentId: 8,
        client: transport,
      );

      expect(diff.base.documentId, 7);
      expect(diff.target.documentId, 8);
      expect(diff.items, isEmpty);
      expect(diff.relationshipVerified, isFalse);
      expect(diff.warnings, <String>['base_document_has_no_evidence_rows']);
      // A body with no items is not a body with no findings.
      expect(diff.needsReview, isTrue);
    });

    test('a 404 is a not-found failure, not an empty comparison', () async {
      final transport = FakeTransport(
        (_) async => streamedResponse(
          404,
          '{"error":"HTTPException","message":"unknown project"}',
        ),
      );

      await expectLater(
        apiClient(transport).fetchRevisionDiff(
          projectId: 42,
          baseDocumentId: 7,
          targetDocumentId: 8,
          client: transport,
        ),
        throwsA(failureOf(ApiFailureKind.notFound)),
      );
    });

    test('a 200 carrying an unknown status is malformed, not guessed at',
        () async {
      final transport = FakeTransport(
        (_) async => jsonResponse(
          200,
          revisionDiffBody(
            items: <Object?>[
              <String, Object?>{
                'identity_id': List<String>.filled(64, 'a').join(),
                'status': 'definitely-fine',
                'group_size': 1,
                'base': null,
                'target': null,
                'changed_fields': <String>[],
                'affected_claim_ids': <String>[],
                'affected_claims_omitted': 0,
                'reason': null,
              },
            ],
          ),
        ),
      );

      await expectLater(
        apiClient(transport).fetchRevisionDiff(
          projectId: 42,
          baseDocumentId: 7,
          targetDocumentId: 8,
          client: transport,
        ),
        throwsA(failureOf(ApiFailureKind.malformed)),
      );
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

  group('fetchRateProposals', () {
    test('POSTs exactly the named node ids', () async {
      final transport = FakeTransport((request) async {
        if (request.url.path != '/api/v1/projects/42/rates/proposals/normalize') {
          return jsonResponse(404, <String, Object>{'detail': 'not found'});
        }
        if (request.method != 'POST') {
          return jsonResponse(405, <String, Object>{});
        }
        final requestBody = await request.finalize().bytesToString();
        if (requestBody != '{"node_ids":[1,2,3]}') {
          return jsonResponse(400, <String, Object>{'detail': 'bad body'});
        }
        return jsonResponse(200, <String, Object?>{
          'project_id': 42,
          'reference_date': '2026-09-14',
          'normalized': 0,
          'unresolved': 0,
          'warnings': [],
          'proposals': [],
        });
      });
      final response = await apiClient(transport).fetchRateProposals(
        client: transport,
        projectId: 42,
        nodeIds: [1, 2, 3],
      );
      expect(response.projectId, 42);
      expect(response.proposals, isEmpty);
    });

    test('refuses to send more node ids than the declared bound', () async {
      final transport = FakeTransport((_) async => jsonResponse(200, {}));
      
      final tooMany = List<int>.generate(1001, (i) => i);
      expect(
        () => apiClient(transport).fetchRateProposals(
          client: transport,
          projectId: 42,
          nodeIds: tooMany,
        ),
        throwsA(failureOf(ApiFailureKind.invalidRequest)),
      );
    });
  });

}

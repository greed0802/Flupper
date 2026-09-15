import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_error.dart';
import 'package:flupper_workstation/core/dto/health_response.dart';
import 'package:flupper_workstation/core/dto/project_response.dart';
import 'package:flupper_workstation/core/dto/revision_change.dart';
import 'package:flupper_workstation/core/dto/revision_diff_response.dart';
import 'package:flupper_workstation/core/dto_limits.dart';

/// Runs [action] and returns the failure it raises, failing the test if none.
ApiFailure malformedFrom(void Function() action) {
  try {
    action();
  } on ApiFailure catch (failure) {
    return failure;
  }
  fail('expected an ApiFailure');
}

void main() {
  group('HealthResponse', () {
    Map<String, Object?> valid() => <String, Object?>{
      'status': 'ok',
      'sandbox_root_ready': true,
    };

    test('parses the declared shape', () {
      final health = HealthResponse.fromJson(valid());
      expect(health.status, 'ok');
      expect(health.sandboxRootReady, isTrue);
    });

    test('refuses a missing key', () {
      final partial = valid()..remove('status');
      expect(
        malformedFrom(() => HealthResponse.fromJson(partial)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses an unknown key', () {
      final extra = valid()..['sandbox_root'] = '/var/tmp/runs';
      expect(
        malformedFrom(() => HealthResponse.fromJson(extra)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a status longer than the client bound', () {
      final long = valid()..['status'] = 'a' * (maxHealthStatusChars + 1);
      expect(
        malformedFrom(() => HealthResponse.fromJson(long)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('accepts a status exactly at the client bound', () {
      final atLimit = valid()..['status'] = 'a' * maxHealthStatusChars;
      expect(
        HealthResponse.fromJson(atLimit).status.length,
        maxHealthStatusChars,
      );
    });

    test('refuses an empty status', () {
      final empty = valid()..['status'] = '';
      expect(
        malformedFrom(() => HealthResponse.fromJson(empty)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a mistyped field', () {
      final mistyped = valid()..['sandbox_root_ready'] = 'true';
      expect(
        malformedFrom(() => HealthResponse.fromJson(mistyped)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a body that is not an object', () {
      for (final notAnObject in <Object?>[null, 'ok', 1, <Object?>[]]) {
        expect(
          malformedFrom(() => HealthResponse.fromJson(notAnObject)).kind,
          ApiFailureKind.malformed,
          reason: '$notAnObject',
        );
      }
    });
  });

  group('ProjectResponse', () {
    Map<String, Object?> valid() => <String, Object?>{
      'project_id': 7,
      'name': 'Example project',
    };

    test('parses the declared shape', () {
      final project = ProjectResponse.fromJson(valid());
      expect(project.projectId, 7);
      expect(project.name, 'Example project');
    });

    test('refuses an id that is not a positive integer', () {
      // `1.5` and not `1.0`: on the web an integral double and an int are the
      // same JS number, so `1.0 is int` is true there. `1.5` is non-integral on
      // every platform, which is the shape the validator must actually refuse.
      for (final bad in <Object?>[0, -1, 1.5, '1', null, true]) {
        final body = valid()..['project_id'] = bad;
        expect(
          malformedFrom(() => ProjectResponse.fromJson(body)).kind,
          ApiFailureKind.malformed,
          reason: '$bad',
        );
      }
    });

    test('accepts the largest id a web integer holds exactly', () {
      final body = valid()..['project_id'] = maxProjectId;
      expect(ProjectResponse.fromJson(body).projectId, maxProjectId);
    });

    test('refuses an id above that ceiling', () {
      final body = valid()..['project_id'] = maxProjectId + 1;
      expect(
        malformedFrom(() => ProjectResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a name over the server bound', () {
      final body = valid()..['name'] = 'a' * (maxProjectNameChars + 1);
      expect(
        malformedFrom(() => ProjectResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('accepts a name exactly at the server bound', () {
      final body = valid()..['name'] = 'a' * maxProjectNameChars;
      expect(ProjectResponse.fromJson(body).name.length, maxProjectNameChars);
    });

    test('refuses an empty name', () {
      final body = valid()..['name'] = '';
      expect(
        malformedFrom(() => ProjectResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });
  });

  group('RevisionDiffResponse', () {
    String digest(String seed) => List<String>.filled(64, seed).join();

    Map<String, Object?> evidence({int id = 1, String? rawText = 'DN600'}) =>
        <String, Object?>{
          'node_id': id,
          'identity_id': digest('b'),
          'node_type': 'drawing',
          'label': '',
          'drawing_no': 'C-204',
          'revision': 'A',
          'sheet': null,
          'page': null,
          'zone': null,
          'file_hash': digest('a'),
          'raw_text': rawText,
          'raw_text_truncated': false,
        };

    Map<String, Object?> document({int id = 1, String revision = 'A'}) =>
        <String, Object?>{
          'document_id': id,
          'file_name': 'synthetic.pdf',
          'file_hash': digest('a'),
          'drawing_no': 'C-204',
          'revision': revision,
          'evidence_rows': 1,
          'evidence_truncated': false,
        };

    Map<String, Object?> change() => <String, Object?>{
      'identity_id': digest('c'),
      'status': 'changed',
      'group_size': 1,
      'base': evidence(),
      'target': evidence(id: 2, rawText: 'DN900'),
      'changed_fields': <String>['raw_text'],
      'affected_claim_ids': <String>['Q-1'],
      'affected_claims_omitted': 0,
      'reason': null,
    };

    Map<String, Object?> valid() => <String, Object?>{
      'project_id': 7,
      'base': document(),
      'target': document(id: 2, revision: 'B'),
      'relationship': RevisionDiffResponse.sameDrawing,
      'counts': <String, Object?>{
        'added': 0,
        'removed': 0,
        'changed': 1,
        'unchanged': 0,
        'ambiguous': 0,
        'unresolved': 0,
      },
      'items': <Object?>[change()],
      'items_truncated': false,
      'items_omitted': 0,
      'checked_claims': 1,
      'malformed_claims': 0,
      'unverified_claim_references': 0,
      'unassociated_claims': 0,
      'warnings': <String>[],
    };

    test('parses the declared shape, nested objects included', () {
      final diff = RevisionDiffResponse.fromJson(valid());
      expect(diff.projectId, 7);
      expect(diff.relationshipVerified, isTrue);
      expect(diff.counts.changed, 1);
      expect(diff.counts.total, 1);
      expect(diff.counts.notComparable, 0);
      expect(diff.items, hasLength(1));

      final item = diff.items.single;
      expect(item.status, 'changed');
      expect(item.isChange, isTrue);
      expect(item.needsReview, isFalse);
      expect(item.changedFields, <String>['raw_text']);
      expect(item.affectedClaimIds, <String>['Q-1']);
      expect(item.base?.fileHash, digest('a'));
      expect(item.target?.rawText, 'DN900');
      expect(item.base?.label, '');
      expect(diff.needsReview, isFalse);
    });

    test('accepts an empty evidence label, a value the server can send', () {
      final item = RevisionDiffResponse.fromJson(valid()).items.single;
      expect(item.base?.label, '');
      expect(item.target?.label, '');
    });

    test('reads an unavailable comparison without inventing a change', () {
      final body = valid()
        ..['relationship'] = RevisionDiffResponse.evidenceUnavailable
        ..['items'] = <Object?>[]
        ..['items_truncated'] = true
        ..['items_omitted'] = 12;
      final diff = RevisionDiffResponse.fromJson(body);
      expect(diff.relationshipVerified, isFalse);
      expect(diff.items, isEmpty);
      expect(diff.itemsTruncated, isTrue);
      expect(diff.itemsOmitted, 12);
      expect(diff.needsReview, isTrue);
    });

    test('refuses a missing key', () {
      final partial = valid()..remove('counts');
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(partial)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses an unknown key', () {
      final extra = valid()..['verified'] = true;
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(extra)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a status outside the vocabulary', () {
      final body = valid();
      final items = body['items'] as List<Object?>;
      (items.single as Map<String, Object?>)['status'] = 'probably-fine';
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a relationship outside the vocabulary', () {
      final body = valid()..['relationship'] = 'probably-the-same-drawing';
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses more items than the declared ceiling', () {
      final body = valid()
        ..['items'] = <Object?>[
          for (var index = 0; index <= maxDiffItems; index++) change(),
        ];
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses more affected claims than the declared ceiling', () {
      final body = valid();
      final items = body['items'] as List<Object?>;
      (items.single as Map<String, Object?>)['affected_claim_ids'] = <String>[
        for (var index = 0; index <= maxDiffAffectedClaims; index++) 'Q-$index',
      ];
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a locator longer than the server would send', () {
      final body = valid();
      final items = body['items'] as List<Object?>;
      final base = (items.single as Map<String, Object?>)['base'];
      (base as Map<String, Object?>)['raw_text'] =
          List<String>.filled(maxDiffTextChars + 1, 'D').join();
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('refuses a nested side that is not an object', () {
      final body = valid();
      final items = body['items'] as List<Object?>;
      (items.single as Map<String, Object?>)['base'] = <String>['not', 'a map'];
      expect(
        malformedFrom(() => RevisionDiffResponse.fromJson(body)).kind,
        ApiFailureKind.malformed,
      );
    });

    test('carries no fragment of a rejected value', () {
      final body = valid();
      final items = body['items'] as List<Object?>;
      (items.single as Map<String, Object?>)['status'] = 'a secret status';
      final failure = malformedFrom(() => RevisionDiffResponse.fromJson(body));
      expect(failure.toString().contains('a secret status'), isFalse);
    });

    test('the status vocabulary is the server vocabulary', () {
      expect(RevisionChange.statuses, hasLength(6));
      expect(RevisionChange.statuses, contains('ambiguous'));
      expect(RevisionChange.statuses, contains('unresolved'));
      expect(RevisionDiffResponse.relationships, hasLength(3));
    });
  });

  group('malformed failures', () {
    test('carry no fragment of the offending value', () {
      final failure = malformedFrom(
        () => ProjectResponse.fromJson(<String, Object?>{
          'project_id': 7,
          'name': <String>['a secret label'],
        }),
      );
      expect(failure.toString().contains('a secret label'), isFalse);
      expect(failure.errorClass, isNull);
      expect(failure.fields, isEmpty);
    });

    test('render fixed local copy', () {
      const failure = ApiFailure(ApiFailureKind.malformed);
      expect(failure.userMessage, 'The gateway response could not be read.');
    });
  });
}

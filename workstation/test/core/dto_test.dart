import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_error.dart';
import 'package:flupper_workstation/core/dto/health_response.dart';
import 'package:flupper_workstation/core/dto/project_response.dart';
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

import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/core/dto_limits.dart';
import 'package:flupper_workstation/features/projects/project_view.dart';

import '../support/fake_transport.dart';

void main() {
  final config = ApiConfig.parse('http://127.0.0.1:8000');

  Future<void> pump(
    WidgetTester tester,
    FakeTransport transport, {
    VoidCallback? onUnauthorized,
  }) async {
    final auth = AuthStorage()..setToken('test-token');
    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectView(
            client: ApiClient(config: config, auth: auth),
            clientFactory: () => transport,
            onUnauthorized: onUnauthorized ?? () {},
          ),
        ),
      ),
    );
  }

  Future<void> ask(WidgetTester tester, String text) async {
    await tester.enterText(find.byType(TextField), text);
    await tester.tap(find.text('Load project'));
    await tester.pump();
    await tester.pump();
  }

  http.StreamedResponse okProject() => jsonResponse(200, <String, Object>{
    'project_id': 7,
    'name': 'Example project',
  });

  group('the identifier is bounded before a request exists', () {
    testWidgets('an empty id is refused', (tester) async {
      final transport = FakeTransport((_) async => okProject());

      await pump(tester, transport);
      await ask(tester, '');

      expect(find.text('Enter a project id.'), findsOneWidget);
      expect(transport.requests, isEmpty);
    });

    testWidgets('anything that is not digits is refused', (tester) async {
      final transport = FakeTransport((_) async => okProject());

      await pump(tester, transport);
      for (final bad in <String>['0', '-1', '1.5', '1e3', '1 2', '１２']) {
        await ask(tester, bad);
        expect(
          find.text('Enter a whole number greater than zero.'),
          findsOneWidget,
          reason: bad,
        );
      }

      expect(transport.requests, isEmpty);
    });

    testWidgets('an id past the ceiling is refused', (tester) async {
      final transport = FakeTransport((_) async => okProject());

      await pump(tester, transport);
      await ask(tester, '${maxProjectId + 1}');

      expect(
        find.text('That project id is larger than this client accepts.'),
        findsOneWidget,
      );
      expect(transport.requests, isEmpty);
    });

    testWidgets('a digit string no integer can hold is refused', (tester) async {
      final transport = FakeTransport((_) async => okProject());

      await pump(tester, transport);
      await ask(tester, '9' * 40);

      expect(transport.requests, isEmpty);
    });
  });

  group('a valid identifier', () {
    testWidgets('requests exactly /api/v1/projects/{id} and renders it', (tester) async {
      final transport = FakeTransport((_) async => okProject());

      await pump(tester, transport);
      await ask(tester, '7');

      expect(transport.requests, hasLength(1));
      expect(
        transport.requests.single.url.toString(),
        'http://127.0.0.1:8000/api/v1/projects/7',
      );
      expect(find.text('Id: 7'), findsOneWidget);
      expect(find.text('Name: Example project'), findsOneWidget);
    });

    testWidgets('accepts the largest id the client admits', (tester) async {
      final transport = FakeTransport(
        (_) async => jsonResponse(200, <String, Object>{
          'project_id': maxProjectId,
          'name': 'Example project',
        }),
      );

      await pump(tester, transport);
      await ask(tester, '$maxProjectId');

      expect(
        transport.requests.single.url.path,
        '/api/v1/projects/$maxProjectId',
      );
    });
  });

  group('failures', () {
    testWidgets('a 404 is a not-found state, not a blank pane', (tester) async {
      final transport = FakeTransport(
        (_) async => errorEnvelope(404, 'HTTPException'),
      );

      await pump(tester, transport);
      await ask(tester, '7');

      expect(find.text('Not found on the gateway.'), findsOneWidget);
    });

    testWidgets('a rejected token signs out', (tester) async {
      var unauthorized = 0;
      final transport = FakeTransport(
        (_) async => errorEnvelope(401, 'HTTPException'),
      );

      await pump(tester, transport, onUnauthorized: () => unauthorized++);
      await ask(tester, '7');

      expect(unauthorized, 1);
    });

    testWidgets('a 422 keeps field locations and shows fixed copy', (tester) async {
      final transport = FakeTransport(
        (_) async => errorEnvelope(
          422,
          'RequestValidationError',
          fields: <String>['path.project_id'],
        ),
      );

      await pump(tester, transport);
      await ask(tester, '7');

      expect(find.text('The gateway rejected the request shape.'), findsOneWidget);
      expect(find.textContaining('must never be rendered'), findsNothing);
    });
  });

  testWidgets('disposing mid-flight closes the transport', (tester) async {
    final completer = Completer<http.StreamedResponse>();
    final transport = FakeTransport((_) => completer.future);

    await pump(tester, transport);
    await ask(tester, '7');
    expect(transport.closed, isFalse);

    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    expect(transport.closed, isTrue);

    completer.complete(okProject());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
  });
}

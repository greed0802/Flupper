import 'dart:async';

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/health/health_screen.dart';

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
          body: HealthScreen(
            client: ApiClient(config: config, auth: auth),
            clientFactory: () => transport,
            onUnauthorized: onUnauthorized ?? () {},
          ),
        ),
      ),
    );
  }

  http.StreamedResponse okHealth() => jsonResponse(200, <String, Object>{
    'status': 'ok',
    'sandbox_root_ready': true,
  });

  testWidgets('shows a loading state, then the status', (tester) async {
    final transport = FakeTransport((_) async => okHealth());

    await pump(tester, transport);
    expect(find.text('Contacting the gateway...'), findsOneWidget);

    await tester.pump();
    expect(find.text('Status: ok'), findsOneWidget);
    expect(find.text('Sandbox root is ready.'), findsOneWidget);
    expect(transport.requests.single.url.path, '/api/v1/health');
  });

  testWidgets('a rejected token signs out instead of showing an error', (tester) async {
    var unauthorized = 0;
    final transport = FakeTransport(
      (_) async => errorEnvelope(401, 'HTTPException'),
    );

    await pump(tester, transport, onUnauthorized: () => unauthorized++);
    await tester.pump();

    expect(unauthorized, 1);
    expect(find.textContaining('rejected this session'), findsNothing);
    expect(find.text('Retry'), findsNothing);
  });

  testWidgets('a 5xx shows fixed copy, never the server sentence', (tester) async {
    final transport = FakeTransport(
      (_) async => errorEnvelope(500, 'SandboxError'),
    );

    await pump(tester, transport);
    await tester.pump();

    expect(find.text('The gateway reported an internal error.'), findsOneWidget);
    expect(find.textContaining('must never be rendered'), findsNothing);
    expect(find.textContaining('SandboxError'), findsOneWidget);
    expect(find.text('Retry'), findsOneWidget);
  });

  testWidgets('an unreadable body is reported as unreadable', (tester) async {
    final transport = FakeTransport((_) async => streamedResponse(200, 'nope'));

    await pump(tester, transport);
    await tester.pump();

    expect(find.text('The gateway response could not be read.'), findsOneWidget);
  });

  testWidgets('a silent gateway times out rather than waiting forever', (tester) async {
    final transport = FakeTransport((_) => neverResponds());

    await pump(tester, transport);
    await tester.pump(const Duration(seconds: 11));
    await tester.pump();

    expect(find.text('The gateway did not answer in time.'), findsOneWidget);
  });

  testWidgets('an unreachable gateway says so', (tester) async {
    final transport = FakeTransport((request) => connectionRefused(request.url));

    await pump(tester, transport);
    await tester.pump();

    expect(find.text('The gateway could not be reached.'), findsOneWidget);
  });

  testWidgets('retry issues a second request', (tester) async {
    final transport = FakeTransport(
      (_) async => errorEnvelope(503, 'SandboxError'),
    );

    await pump(tester, transport);
    await tester.pump();
    expect(transport.requests, hasLength(1));

    await tester.tap(find.text('Retry'));
    await tester.pump();
    await tester.pump();

    expect(transport.requests, hasLength(2));
  });

  testWidgets('disposing mid-flight closes the transport and drops the result', (
    tester,
  ) async {
    final completer = Completer<http.StreamedResponse>();
    final transport = FakeTransport((_) => completer.future);

    await pump(tester, transport);
    expect(transport.closed, isFalse);

    // Navigate away: the client is closed, which is the only cancellation
    // package:http actually offers.
    await tester.pumpWidget(const MaterialApp(home: SizedBox()));
    expect(transport.closed, isTrue);
    expect(transport.closeCount, 1);

    // The response arrives after disposal. Nothing may be rendered from it and
    // nothing may throw.
    completer.complete(okHealth());
    await tester.pumpAndSettle();
    expect(tester.takeException(), isNull);
    expect(find.text('Status: ok'), findsNothing);
  });
}

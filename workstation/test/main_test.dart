import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:http/http.dart' as http;
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/auth/login_screen.dart';
import 'package:flupper_workstation/features/projects/project_view.dart';
import 'package:flupper_workstation/main.dart';

import 'support/fake_transport.dart';
import 'support/pump_until.dart';

/// The shell decides which of the two screens exists. These tests pin that
/// decision and the one transition that matters: a rejected token or a sign-out
/// returns to the form and leaves no token behind.
///
/// Navigation during the tablet trial is additive: `ProjectView` gains two
/// read-only panes, and the toolbar gains two icons that reach them. The tests
/// that pin the *new* surface live in `test/features/`; what stays here is the
/// shell's own promise - the project pane still offers no mutation.
void main() {
  final config = ApiConfig.parse('http://127.0.0.1:8000');

  Future<http.StreamedResponse> routed(http.BaseRequest request) async {
    switch (request.url.path) {
      case '/api/v1/health':
        return jsonResponse(200, <String, Object>{
          'status': 'ok',
          'sandbox_root_ready': true,
        });
      case '/api/v1/projects/7':
        return jsonResponse(200, <String, Object>{
          'project_id': 7,
          'name': 'Example project',
        });
    }
    return errorEnvelope(404, 'HTTPException');
  }

  Future<void> pumpApp(
    WidgetTester tester, {
    ApiConfig? initialConfig,
    required AuthStorage auth,
    required FakeTransport transport,
    Finder? until,
  }) async {
    await tester.pumpWidget(
      WorkstationApp(
        initialConfig: initialConfig,
        auth: auth,
        clientFactory: () => transport,
      ),
    );
    await tester.pump();
    if (until != null) {
      // Bounded wait for whatever state this test is about, instead of a fixed
      // number of frames that the web binding can outrun.
      await pumpUntilFound(tester, until);
    }
  }

  testWidgets('no configured address means the login form, never a default', (
    tester,
  ) async {
    await pumpApp(
      tester,
      auth: AuthStorage(),
      transport: FakeTransport(routed),
      until: find.byType(LoginScreen),
    );

    expect(find.byType(LoginScreen), findsOneWidget);
    expect(find.textContaining('No API address is configured'), findsWidgets);
  });

  testWidgets('a configured address and a token open the session', (tester) async {
    final transport = FakeTransport(routed);

    await pumpApp(
      tester,
      initialConfig: config,
      auth: AuthStorage()..setToken('test-token'),
      transport: transport,
      until: find.text('Status: ok'),
    );

    expect(find.text('http://127.0.0.1:8000'), findsOneWidget);
    expect(find.text('Status: ok'), findsOneWidget);
    expect(
      transport.requests.single.url.toString(),
      'http://127.0.0.1:8000/api/v1/health',
    );
  });

  testWidgets('signing out clears the token and returns to the form', (tester) async {
    final auth = AuthStorage()..setToken('test-token');

    await pumpApp(
      tester,
      initialConfig: config,
      auth: auth,
      transport: FakeTransport(routed),
      until: find.text('Sign out'),
    );

    await tester.tap(find.text('Sign out'));
    await pumpUntilFound(tester, find.byType(LoginScreen));

    expect(find.byType(LoginScreen), findsOneWidget);
    expect(auth.hasToken, isFalse);
    expect(auth.toString().contains('test-token'), isFalse);
  });

  testWidgets('a rejected token drops the session without being asked', (tester) async {
    final auth = AuthStorage()..setToken('stale-token');

    await pumpApp(
      tester,
      initialConfig: config,
      auth: auth,
      transport: FakeTransport((_) async => errorEnvelope(401, 'HTTPException')),
      until: find.byType(LoginScreen),
    );

    expect(find.byType(LoginScreen), findsOneWidget);
    expect(auth.hasToken, isFalse);
  });

  testWidgets('the project pane is reachable and offers no mutation', (tester) async {
    await pumpApp(
      tester,
      initialConfig: config,
      auth: AuthStorage()..setToken('test-token'),
      transport: FakeTransport(routed),
      until: find.byIcon(Icons.folder_outlined),
    );

    await tester.tap(find.byIcon(Icons.folder_outlined));
    await pumpUntilFound(tester, find.text('Load project'));

    expect(find.byType(ProjectView), findsOneWidget);
    expect(find.text('Project id'), findsOneWidget);
    expect(find.text('Load project'), findsOneWidget);
    // Nothing rendered on the pane offers to change the store.
    expect(find.textContaining('Create'), findsNothing);
    expect(find.textContaining('Save'), findsNothing);
  });
}

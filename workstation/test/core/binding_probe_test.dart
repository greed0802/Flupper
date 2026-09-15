/// TEMPORARY diagnostic. Deleted once the Chrome binding is understood.
///
/// The per-file Chrome step fails on every test that needs an HTTP response to
/// reach a widget, with no readable error, while the same tests pass on the Dart
/// VM. This file prints what the binding actually does with a resolved
/// `FakeTransport` response, with no widgets, then through the widget, so the
/// next round says which step stalls instead of guessing.
library;

import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/projects/project_view.dart';

import '../support/fake_transport.dart';

String _visibleText(WidgetTester tester) {
  final List<String> texts = tester
      .widgetList<Text>(find.byType(Text))
      .map((Text text) => text.data ?? '')
      .where((String value) => value.isNotEmpty)
      .toList();
  return texts.join(' | ');
}

void main() {
  final config = ApiConfig.parse('http://127.0.0.1:8000');

  void probe(String label, String detail) {
    debugPrint('PROBE $label :: $detail');
  }

  testWidgets('probe the binding with no widgets', (WidgetTester tester) async {
    probe('binding', '${tester.binding.runtimeType}');

    final transport = FakeTransport(
      (_) async => jsonResponse(200, <String, Object>{
        'project_id': 7,
        'name': 'Example project',
      }),
    );
    final client = ApiClient(
      config: config,
      auth: AuthStorage()..setToken('probe-token'),
    );

    String state = 'pending';
    client
        .fetchProject(projectId: 7, client: transport)
        .then(
          (value) => state = 'ok',
          onError: (Object error) =>
              state = 'error(${error.runtimeType}): $error',
        );

    probe('no-pump', 'state=$state requests=${transport.requests.length}');
    await tester.pump();
    probe('bare-pump', 'state=$state');
    await tester.pump(const Duration(milliseconds: 100));
    probe('pump-100ms', 'state=$state');
    await tester.pump(const Duration(seconds: 1));
    probe('pump-1s', 'state=$state');

    await tester.runAsync(() => Future<void>.delayed(Duration.zero));
    await tester.pump();
    probe('runAsync-then-pump', 'state=$state');

    await tester.runAsync(() => Future<void>.delayed(Duration.zero));
    probe('runAsync-without-pump', 'state=$state');
  });

  testWidgets('probe the binding through the widget', (
    WidgetTester tester,
  ) async {
    final transport = FakeTransport(
      (_) async => jsonResponse(200, <String, Object>{
        'project_id': 7,
        'name': 'Example project',
      }),
    );
    final auth = AuthStorage()..setToken('probe-token');

    await tester.pumpWidget(
      MaterialApp(
        home: Scaffold(
          body: ProjectView(
            client: ApiClient(config: config, auth: auth),
            clientFactory: () => transport,
            onUnauthorized: () {},
          ),
        ),
      ),
    );

    await tester.enterText(find.byType(TextField), '7');
    await tester.tap(find.text('Load project'));
    await tester.pump();
    probe(
      'widget-1-pump',
      'requests=${transport.requests.length} text=${_visibleText(tester)}',
    );

    await tester.pump(const Duration(milliseconds: 100));
    probe(
      'widget-100ms',
      'requests=${transport.requests.length} text=${_visibleText(tester)}',
    );

    await tester.runAsync(() => Future<void>.delayed(Duration.zero));
    await tester.pump();
    probe(
      'widget-runAsync',
      'requests=${transport.requests.length} text=${_visibleText(tester)}',
    );

    for (var attempt = 0; attempt < 10; attempt++) {
      await tester.runAsync(() => Future<void>.delayed(Duration.zero));
      await tester.pump(const Duration(milliseconds: 20));
    }
    probe('widget-runAsync-loop', 'text=${_visibleText(tester)}');
  });
}

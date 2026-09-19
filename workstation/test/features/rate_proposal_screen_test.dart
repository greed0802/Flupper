import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/rates/rate_proposal_screen.dart';

import '../support/fake_transport.dart';
import '../support/pump_until.dart';

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
          body: RateProposalScreen(
            client: ApiClient(config: config, auth: auth),
            projectId: 42,
            clientFactory: () => transport,
            onUnauthorized: onUnauthorized ?? () {},
          ),
        ),
      ),
    );
  }

  Future<void> ask(WidgetTester tester, String nodes) async {
    await tester.enterText(find.byType(TextField), nodes);
    await tester.tap(find.text('Fetch'));
    await tester.pump();
  }

  testWidgets('renders proposals on success', (tester) async {
    final sourceHash = List.filled(64, 'a').join();
    final transport = FakeTransport((_) async => jsonResponse(200, <String, Object?>{
      'project_id': 42, 'reference_date': '2026-09-14', 'normalized': 1, 'unresolved': 0,
      'warnings': [],
      'proposals': [
        {
          'node_id': 100, 'label': 'Rate 1', 'status': 'normalized', 'confidence': 'exact', 'reason': null,
          'source_document_id': 200, 'source_file_name': 's.pdf', 'source_file_hash': sourceHash,
          'original_amount': '1.0', 'original_unit': 'm', 'normalized_amount': '1.0', 'normalized_unit': 'm',
          'currency': 'AUD', 'rate_category': 'labour_time', 'effective_date': '2026-01-01', 'source_age_days': 200,
          'locator_present': true, 'duplicate_quote_count': 0, 'warnings': []
        }
      ]
    }));
    await pump(tester, transport);
    await ask(tester, '100');
    await pumpUntilFound(tester, find.textContaining('Node: 100 (normalized)'));
    expect(find.textContaining('Node: 100 (normalized)'), findsOneWidget);
  });

  testWidgets('displays a 404 message', (tester) async {
    final transport = FakeTransport((_) async => jsonResponse(404, <String, Object>{'detail': 'not found'}));
    await pump(tester, transport);
    await ask(tester, '100');
    await pumpUntilFound(tester, find.textContaining('Not found on the gateway.'));
    expect(find.textContaining('Not found on the gateway.'), findsOneWidget);
  });

  testWidgets('refuses non-integer bounds locally', (tester) async {
    final transport = FakeTransport((_) async => neverResponds());
    await pump(tester, transport);
    await ask(tester, 'abc');
    expect(find.text('Enter a whole number greater than zero.'), findsOneWidget);
  });

  testWidgets('fires onUnauthorized callback when gateway rejects 401', (tester) async {
    var loggedOut = false;
    final transport = FakeTransport((_) async => jsonResponse(401, {'detail': 'unauth'}));
    await pump(tester, transport, onUnauthorized: () => loggedOut = true);
    await ask(tester, '100');
    await tester.pump();
    expect(loggedOut, isTrue);
  });
}

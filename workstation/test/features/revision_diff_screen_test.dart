import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_client.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/revisions/revision_diff_screen.dart';

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
          body: RevisionDiffScreen(
            client: ApiClient(config: config, auth: auth),
            projectId: 42,
            clientFactory: () => transport,
            onUnauthorized: onUnauthorized ?? () {},
          ),
        ),
      ),
    );
  }

  Future<void> ask(WidgetTester tester, String base, String target) async {
    await tester.enterText(find.byType(TextField).first, base);
    await tester.enterText(find.byType(TextField).last, target);
    await tester.tap(find.text('Diff'));
    await tester.pump();
  }

  testWidgets('renders diff changes on success', (tester) async {
    String digest(String input) => List.filled(64, input).join();
    final document = {
      'document_id': 1, 'file_name': 'synthetic.pdf', 'file_hash': digest('a'),
      'drawing_no': 'C-204', 'revision': 'A', 'evidence_rows': 1, 'evidence_truncated': false,
    };
    final evidence = {
      'node_id': 1, 'identity_id': digest('b'), 'node_type': 'drawing', 'label': '',
      'drawing_no': 'C-204', 'revision': 'A', 'sheet': null, 'page': null, 'zone': null,
      'file_hash': digest('a'), 'raw_text': 'DN600', 'raw_text_truncated': false,
    };
    final transport = FakeTransport((_) async => jsonResponse(200, <String, Object?>{
      'project_id': 42, 'base': document, 'target': document, 'relationship': 'same_drawing',
      'counts': {'added': 0, 'removed': 0, 'changed': 1, 'unchanged': 0, 'ambiguous': 0, 'unresolved': 0},
      'items': [
        {
           'identity_id': digest('f'), 'status': 'changed', 'group_size': 1,
           'base': evidence, 'target': evidence, 'changed_fields': ['value'], 'affected_claim_ids': ['c1'], 'affected_claims_omitted': 0, 'reason': null
        }
      ],
      'items_truncated': false, 'items_omitted': 0, 'checked_claims': 1, 
      'malformed_claims': 0, 'unverified_claim_references': 0, 'unassociated_claims': 0, 'warnings': []
    }));
    await pump(tester, transport);
    await ask(tester, '1', '2');
    final identity = List.filled(64, 'f').join();
    await pumpUntilFound(tester, find.textContaining(identity));
    expect(find.textContaining(identity), findsOneWidget);
  });

  testWidgets('displays a 404 message', (tester) async {
    final transport = FakeTransport((_) async => jsonResponse(404, <String, Object>{'detail': 'not found'}));
    await pump(tester, transport);
    await ask(tester, '1', '2');
    await pumpUntilFound(tester, find.textContaining('Not found on the gateway.'));
    expect(find.textContaining('Not found on the gateway.'), findsOneWidget);
  });

  testWidgets('refuses non-integer bounds locally', (tester) async {
    final transport = FakeTransport((_) async => neverResponds());
    await pump(tester, transport);
    await ask(tester, 'abc', 'def');
    expect(find.text('Enter a whole number greater than zero.'), findsNWidgets(2));
  });

  testWidgets('fires onUnauthorized callback when gateway rejects 401', (tester) async {
    var loggedOut = false;
    final transport = FakeTransport((_) async => jsonResponse(401, {'detail': 'unauth'}));
    await pump(tester, transport, onUnauthorized: () => loggedOut = true);
    await ask(tester, '1', '2');
    await tester.pump();
    expect(loggedOut, isTrue);
  });
}

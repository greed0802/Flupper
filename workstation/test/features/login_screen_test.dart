import 'package:flutter/material.dart';
import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_config.dart';
import 'package:flupper_workstation/core/auth_storage.dart';
import 'package:flupper_workstation/features/auth/login_screen.dart';

void main() {
  Widget host({
    required AuthStorage auth,
    String? initialAddress,
    void Function(ApiConfig config)? onAuthenticated,
  }) {
    return MaterialApp(
      home: LoginScreen(
        auth: auth,
        initialAddress: initialAddress,
        onAuthenticated: onAuthenticated ?? (_) {},
      ),
    );
  }

  Finder addressField() => find.byType(TextField).at(0);
  Finder tokenField() => find.byType(TextField).at(1);

  Future<void> submit(WidgetTester tester, {required String address, required String token}) async {
    await tester.enterText(addressField(), address);
    await tester.enterText(tokenField(), token);
    await tester.tap(find.byType(FilledButton));
    await tester.pump();
  }

  testWidgets('says so when no address is configured for the build', (tester) async {
    await tester.pumpWidget(host(auth: AuthStorage()));
    expect(find.textContaining('No API address is configured'), findsOneWidget);
    expect(tester.widget<TextField>(addressField()).controller?.text, isEmpty);
  });

  testWidgets('prefills the address without prefilling a token', (tester) async {
    await tester.pumpWidget(
      host(auth: AuthStorage(), initialAddress: 'https://gateway.example.test'),
    );
    expect(
      tester.widget<TextField>(addressField()).controller?.text,
      'https://gateway.example.test',
    );
    expect(tester.widget<TextField>(tokenField()).controller?.text, isEmpty);
    expect(find.textContaining('No API address is configured'), findsNothing);
  });

  testWidgets('the token field is obscured', (tester) async {
    await tester.pumpWidget(host(auth: AuthStorage()));
    expect(tester.widget<TextField>(tokenField()).obscureText, isTrue);
  });

  testWidgets('an empty address is refused and no token is stored', (tester) async {
    final auth = AuthStorage();
    var authenticated = 0;
    await tester.pumpWidget(
      host(auth: auth, onAuthenticated: (_) => authenticated++),
    );

    await submit(tester, address: '', token: 'test-token');

    expect(find.textContaining('No API address is configured'), findsWidgets);
    expect(auth.hasToken, isFalse);
    expect(authenticated, 0);
  });

  testWidgets('plaintext http to a remote host is refused', (tester) async {
    final auth = AuthStorage();
    await tester.pumpWidget(host(auth: auth));

    await submit(
      tester,
      address: 'http://gateway.example.test:8000',
      token: 'test-token',
    );

    expect(
      find.text('Plaintext http is only allowed for the local machine. Use https.'),
      findsOneWidget,
    );
    expect(auth.hasToken, isFalse);
  });

  testWidgets('a token a bearer header cannot carry is refused', (tester) async {
    final auth = AuthStorage();
    var authenticated = 0;
    await tester.pumpWidget(
      host(auth: auth, onAuthenticated: (_) => authenticated++),
    );

    await submit(tester, address: 'http://127.0.0.1:8000', token: 'has space');

    expect(
      find.text('The token contains characters a bearer header cannot carry.'),
      findsOneWidget,
    );
    expect(auth.hasToken, isFalse);
    expect(authenticated, 0);
  });

  testWidgets('a valid pair stores the token and reports the origin', (tester) async {
    final auth = AuthStorage();
    ApiConfig? received;
    await tester.pumpWidget(
      host(auth: auth, onAuthenticated: (config) => received = config),
    );

    await submit(tester, address: 'http://127.0.0.1:8000', token: 'test-token');

    expect(auth.token, 'test-token');
    expect(received?.baseUri.origin, 'http://127.0.0.1:8000');
  });

  testWidgets('the token is never rendered in the clear', (tester) async {
    final auth = AuthStorage();
    await tester.pumpWidget(host(auth: auth));

    await submit(tester, address: 'http://127.0.0.1:8000', token: 'secret-token');

    // The only widget that may hold the token is the obscured field itself.
    final rendered = find.text('secret-token', skipOffstage: false).evaluate();
    for (final element in rendered) {
      final widget = element.widget;
      expect(widget, isA<EditableText>());
      expect((widget as EditableText).obscureText, isTrue);
    }
    expect(auth.toString().contains('secret-token'), isFalse);
  });
}

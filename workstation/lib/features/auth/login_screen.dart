/// The only way a token enters the workstation.
///
/// On web this form is the *whole* token story: there is no `--dart-define`
/// token, no environment token, no URL parameter and no persisted copy. On
/// desktop a development run may be seeded from the process environment
/// (`FLUPPER_DEV_TOKEN`, see `seedTokenFromDevEnvironment`), and this form is
/// still the way to replace it.
///
/// The address field is not prefilled with a default. A blank address is a
/// visible configuration error the user must resolve, because a silent
/// `localhost` fallback in browser-facing code points a browser at the
/// viewer's own machine and hides the misconfiguration.
library;

import 'package:flutter/material.dart';

import '../../core/api_config.dart';
import '../../core/auth_storage.dart';

class LoginScreen extends StatefulWidget {
  const LoginScreen({
    super.key,
    required this.auth,
    required this.onAuthenticated,
    this.initialAddress,
  });

  final AuthStorage auth;

  /// Called with the validated origin once a token is held.
  final void Function(ApiConfig config) onAuthenticated;

  /// Compile-time development address, if one was supplied. Never a token.
  final String? initialAddress;

  @override
  State<LoginScreen> createState() => _LoginScreenState();
}

class _LoginScreenState extends State<LoginScreen> {
  late final TextEditingController _addressController;
  final TextEditingController _tokenController = TextEditingController();

  String? _addressError;
  String? _tokenError;

  @override
  void initState() {
    super.initState();
    _addressController = TextEditingController(
      text: widget.initialAddress ?? '',
    );
  }

  @override
  void dispose() {
    _addressController.dispose();
    _tokenController.dispose();
    super.dispose();
  }

  void _submit() {
    final addressError = _validateAddress();
    final tokenError = widget.auth.validateToken(_tokenController.text);
    setState(() {
      _addressError = addressError;
      _tokenError = tokenError;
    });
    if (addressError != null || tokenError != null) {
      return;
    }

    // setToken re-validates; validateToken above already proved it cannot fail.
    widget.auth.setToken(_tokenController.text);
    widget.onAuthenticated(ApiConfig.parse(_addressController.text));
  }

  String? _validateAddress() {
    try {
      ApiConfig.parse(_addressController.text);
      return null;
    } on ApiConfigException catch (error) {
      return error.reason;
    }
  }

  @override
  Widget build(BuildContext context) {
    final hasSeedAddress =
        (widget.initialAddress ?? '').trim().isNotEmpty;

    return Scaffold(
      appBar: AppBar(title: const Text('Flupper workstation')),
      body: Center(
        child: SingleChildScrollView(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Column(
              mainAxisSize: MainAxisSize.min,
              crossAxisAlignment: CrossAxisAlignment.stretch,
              children: <Widget>[
                Text(
                  'Local gateway',
                  style: Theme.of(context).textTheme.titleLarge,
                ),
                const SizedBox(height: 8),
                if (!hasSeedAddress)
                  const Padding(
                    padding: EdgeInsets.only(bottom: 8),
                    child: Text(
                      'No API address is configured for this build. Enter the '
                      'origin of your local gateway below.',
                    ),
                  ),
                TextField(
                  controller: _addressController,
                  autocorrect: false,
                  enableSuggestions: false,
                  keyboardType: TextInputType.url,
                  decoration: InputDecoration(
                    labelText: 'API origin',
                    hintText: 'http://127.0.0.1:8000',
                    errorText: _addressError,
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 16),
                TextField(
                  controller: _tokenController,
                  obscureText: true,
                  autocorrect: false,
                  enableSuggestions: false,
                  maxLength: AuthStorage.maxTokenChars,
                  decoration: InputDecoration(
                    labelText: 'Bearer token',
                    errorText: _tokenError,
                    border: const OutlineInputBorder(),
                  ),
                ),
                const SizedBox(height: 8),
                const Text(
                  'The token is held in memory for this run only. It is never '
                  'written to disk or browser storage.',
                ),
                const SizedBox(height: 16),
                FilledButton(
                  onPressed: _submit,
                  child: const Text('Connect'),
                ),
              ],
            ),
          ),
        ),
      ),
    );
  }
}

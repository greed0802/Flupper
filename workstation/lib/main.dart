/// Flupper workstation (Phase 5A).
///
/// Read-only by construction. Two screens sit behind one bearer token:
/// authenticated liveness (`GET /api/v1/health`) and a single-project read
/// (`GET /api/v1/projects/{project_id}`). There is no create route, no revision
/// diff, no rate model and no export here - those are 5B, 5C and 5D, each of
/// which needs its own contract.
///
/// The origin is resolved, never assumed. A build with no configured address
/// shows a configuration error; it never falls back to `localhost`.
library;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import 'core/api_client.dart';
import 'core/api_config.dart';
import 'core/auth_storage.dart';
import 'features/auth/login_screen.dart';
import 'features/health/health_screen.dart';
import 'features/projects/project_view.dart';

void main() {
  runApp(const WorkstationApp());
}

class WorkstationApp extends StatefulWidget {
  const WorkstationApp({
    super.key,
    this.initialConfig,
    this.auth,
    this.clientFactory,
  });

  /// Pre-validated origin. Normally `null` and resolved from the compile-time
  /// address, a desktop environment seed, or the login form.
  final ApiConfig? initialConfig;

  /// Injectable for tests. A real run builds its own.
  final AuthStorage? auth;

  /// Creates the transport for one request. Injectable for tests.
  final http.Client Function()? clientFactory;

  @override
  State<WorkstationApp> createState() => _WorkstationAppState();
}

class _WorkstationAppState extends State<WorkstationApp> {
  late final AuthStorage _auth;
  late final http.Client Function() _clientFactory;

  ApiConfig? _config;
  String? _configError;

  @override
  void initState() {
    super.initState();
    _auth = widget.auth ?? AuthStorage();
    _clientFactory = widget.clientFactory ?? http.Client.new;
    _config = widget.initialConfig;

    if (_config == null) {
      try {
        _config = ApiConfig.fromCompileTime();
      } on ApiConfigException catch (error) {
        // A supplied-but-refused address is reported, not ignored: silently
        // dropping it would leave the login form prefilled with nothing and no
        // explanation of why.
        _configError = error.reason;
      }
    }

    if (!_auth.hasToken) {
      // Desktop development only; always null on web. See AuthStorage.
      final seed = seedTokenFromDevEnvironment();
      if (seed != null) {
        _auth.setToken(seed);
      }
    }
  }

  void _signedIn(ApiConfig config) {
    setState(() => _config = config);
  }

  void _signOut() {
    // Drops the token and returns to the form. The origin is kept so the next
    // sign-in does not have to retype it, and the token is not kept anywhere.
    _auth.clear();
    setState(() {});
  }

  @override
  Widget build(BuildContext context) {
    return MaterialApp(
      title: 'Flupper workstation',
      theme: ThemeData(useMaterial3: true, colorSchemeSeed: Colors.teal),
      home: _home(),
    );
  }

  Widget _home() {
    final configError = _configError;
    if (configError != null) {
      return _ConfigurationErrorScreen(reason: configError);
    }

    final config = _config;
    if (config == null || !_auth.hasToken) {
      return LoginScreen(
        auth: _auth,
        initialAddress: config?.baseUri.origin ?? ApiConfig.compileTimeBaseUrl,
        onAuthenticated: _signedIn,
      );
    }

    return SessionShell(
      config: config,
      auth: _auth,
      clientFactory: _clientFactory,
      onSignOut: _signOut,
    );
  }
}

/// Two read-only panes behind one session.
///
/// The `ApiClient` is rebuilt on every build from the current config and token,
/// so a sign-out cannot leave a client holding a stale credential.
class SessionShell extends StatefulWidget {
  const SessionShell({
    super.key,
    required this.config,
    required this.auth,
    required this.clientFactory,
    required this.onSignOut,
  });

  final ApiConfig config;
  final AuthStorage auth;
  final http.Client Function() clientFactory;
  final VoidCallback onSignOut;

  @override
  State<SessionShell> createState() => _SessionShellState();
}

class _SessionShellState extends State<SessionShell> {
  int _index = 0;

  @override
  Widget build(BuildContext context) {
    final client = ApiClient(config: widget.config, auth: widget.auth);
    return Scaffold(
      appBar: AppBar(
        title: Text(widget.config.baseUri.origin),
        actions: <Widget>[
          TextButton(
            onPressed: widget.onSignOut,
            child: const Text('Sign out'),
          ),
        ],
      ),
      body: switch (_index) {
        0 => HealthScreen(
          client: client,
          clientFactory: widget.clientFactory,
          onUnauthorized: widget.onSignOut,
        ),
        _ => ProjectView(
          client: client,
          clientFactory: widget.clientFactory,
          onUnauthorized: widget.onSignOut,
        ),
      },
      bottomNavigationBar: NavigationBar(
        selectedIndex: _index,
        onDestinationSelected: (index) => setState(() => _index = index),
        destinations: const <NavigationDestination>[
          NavigationDestination(
            icon: Icon(Icons.monitor_heart_outlined),
            label: 'Health',
          ),
          NavigationDestination(
            icon: Icon(Icons.folder_outlined),
            label: 'Project',
          ),
        ],
      ),
    );
  }
}

/// Shown when a configured address was supplied and refused.
class _ConfigurationErrorScreen extends StatelessWidget {
  const _ConfigurationErrorScreen({required this.reason});

  final String reason;

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Configuration error')),
      body: Center(
        child: Padding(
          padding: const EdgeInsets.all(24),
          child: ConstrainedBox(
            constraints: const BoxConstraints(maxWidth: 480),
            child: Text(reason),
          ),
        ),
      ),
    );
  }
}

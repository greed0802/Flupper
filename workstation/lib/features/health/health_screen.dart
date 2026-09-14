/// Authenticated liveness: `GET /api/v1/health`.
///
/// Proves three things at once, which is the whole point of 5A-2: the origin is
/// reachable, the bearer token is accepted, and the response parses under this
/// client's bounds. A 401 is not shown as an error - it is a sign-out, because
/// a token the gateway has rejected is not a token worth keeping in memory.
library;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/api_client.dart';
import '../../core/api_error.dart';
import '../../core/dto/health_response.dart';
import '../common/resource_view.dart';

class HealthScreen extends StatefulWidget {
  const HealthScreen({
    super.key,
    required this.client,
    required this.clientFactory,
    required this.onUnauthorized,
  });

  final ApiClient client;

  /// Creates the `http.Client` for one request. The screen closes it in
  /// `dispose`, which is what aborts an in-flight request when the user
  /// navigates away.
  final http.Client Function() clientFactory;

  /// Called when the gateway rejects the session.
  final VoidCallback onUnauthorized;

  @override
  State<HealthScreen> createState() => _HealthScreenState();
}

class _HealthScreenState extends State<HealthScreen> {
  bool _loading = true;
  ApiFailure? _failure;
  HealthResponse? _health;
  http.Client? _transport;
  bool _disposed = false;

  @override
  void initState() {
    super.initState();
    _load();
  }

  @override
  void dispose() {
    // Order matters: mark disposed first so a completion that races this call
    // cannot reach setState, then close the transport to tear down the socket.
    _disposed = true;
    _transport?.close();
    _transport = null;
    super.dispose();
  }

  Future<void> _load() async {
    final transport = widget.clientFactory();
    _transport = transport;
    try {
      final health = await widget.client.fetchHealth(client: transport);
      if (_disposed) return;
      setState(() {
        _loading = false;
        _health = health;
        _failure = null;
      });
    } on ApiFailure catch (failure) {
      if (_disposed) return;
      if (failure.isAuthenticationFailure) {
        widget.onUnauthorized();
        return;
      }
      setState(() {
        _loading = false;
        _failure = failure;
      });
    }
  }

  void _retry() {
    _transport?.close();
    setState(() {
      _loading = true;
      _failure = null;
    });
    _load();
  }

  @override
  Widget build(BuildContext context) {
    return ResourceView<HealthResponse>(
      title: 'Gateway health',
      loading: _loading,
      failure: _failure,
      data: _health,
      onRetry: _retry,
      builder: (context, health) => Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text('Status: ${health.status}'),
          const SizedBox(height: 4),
          Text(
            health.sandboxRootReady
                ? 'Sandbox root is ready.'
                : 'Sandbox root is missing.',
          ),
          const SizedBox(height: 8),
          Text(
            'A healthy gateway says nothing about any quantity. It reports '
            'liveness only, and the workstation treats it that way.',
            style: Theme.of(context).textTheme.bodySmall,
          ),
        ],
      ),
    );
  }
}

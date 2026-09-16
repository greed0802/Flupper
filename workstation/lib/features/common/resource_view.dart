/// The one loading/failed/loaded presentation both 5A views share.
///
/// Failure copy comes from [ApiFailure.userMessage], which is fixed local text;
/// nothing renders a server-supplied string. The diagnostic line carries the
/// HTTP status and the envelope class name only, and is deliberately secondary
/// so it cannot be mistaken for the message.
library;

import 'package:flutter/material.dart';

import '../../core/api_error.dart';

class ResourceView<T> extends StatelessWidget {
  const ResourceView({
    super.key,
    required this.loading,
    required this.failure,
    required this.data,
    required this.onRetry,
    required this.title,
    required this.builder,
    this.scrolling = false,
  });

  final bool loading;
  final ApiFailure? failure;
  final T? data;
  final VoidCallback onRetry;
  final String title;
  final Widget Function(BuildContext context, T data) builder;

  /// When true, the loaded body is handed a bounded, scrollable area instead of
  /// being wrapped in a `Center`d column that is only as tall as its content.
  ///
  /// The tablet panes render lists the server is allowed to send in full (up to
  /// 500 diff items, up to 100 proposals). A `ListView` needs a bounded height,
  /// and a `Center`ed `Column` with `mainAxisSize.min` is exactly the constraint
  /// that cannot give it one. This flag is off by default, so the 5A panes keep
  /// the layout their tests were written against.
  final bool scrolling;

  @override
  Widget build(BuildContext context) {
    return scrolling ? _scrollableFrame(context) : _centredFrame(context);
  }

  Widget _centredFrame(BuildContext context) {
    return Center(
      child: Padding(
        padding: const EdgeInsets.all(24),
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 640),
          child: Column(
            mainAxisSize: MainAxisSize.min,
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 16),
              _body(context),
            ],
          ),
        ),
      ),
    );
  }

  Widget _scrollableFrame(BuildContext context) {
    return Padding(
      padding: const EdgeInsets.all(24),
      child: Align(
        alignment: Alignment.topCenter,
        child: ConstrainedBox(
          // On a 12-inch tablet in landscape the default width would stretch a
          // provenance line to the full screen and make it unreadable. Bounding
          // the column is what keeps a rate and its source on one eye line.
          constraints: const BoxConstraints(maxWidth: 720),
          child: Column(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Text(title, style: Theme.of(context).textTheme.titleLarge),
              const SizedBox(height: 16),
              Expanded(child: _body(context)),
            ],
          ),
        ),
      ),
    );
  }

  Widget _body(BuildContext context) {
    if (loading) {
      return const Row(
        children: <Widget>[
          SizedBox(
            width: 16,
            height: 16,
            child: CircularProgressIndicator(strokeWidth: 2),
          ),
          SizedBox(width: 12),
          Text('Contacting the gateway...'),
        ],
      );
    }

    final failure = this.failure;
    if (failure != null) {
      return Column(
        crossAxisAlignment: CrossAxisAlignment.start,
        children: <Widget>[
          Text(failure.userMessage),
          const SizedBox(height: 8),
          Text(
            _diagnostic(failure),
            style: Theme.of(context).textTheme.bodySmall,
          ),
          const SizedBox(height: 12),
          OutlinedButton(onPressed: onRetry, child: const Text('Retry')),
        ],
      );
    }

    final data = this.data;
    if (data == null) {
      return const Text('Nothing has been requested yet.');
    }
    return builder(context, data);
  }

  /// The machine-readable half of a failure: a status and an error class name,
  /// both already bounded by the client. Never a message.
  String _diagnostic(ApiFailure failure) {
    final parts = <String>[
      'kind=${failure.kind.name}',
      if (failure.statusCode != null) 'status=${failure.statusCode}',
      if (failure.errorClass != null) 'error=${failure.errorClass}',
    ];
    return parts.join(' ');
  }
}

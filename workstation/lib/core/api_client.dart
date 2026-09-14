/// The only place the workstation speaks to the gateway.
///
/// Four decisions live here rather than in the widgets:
///
/// * **Full paths, spelled once.** [healthPath] and [projectPath] carry the
///   `/api/v1` prefix literally. A route string assembled from a variable
///   prefix is a route string nobody can grep for.
/// * **The token is added per request.** `package:http` has no interceptor, so
///   this wrapper sets the `authorization` header itself and refuses to send at
///   all when no token is held - a request without a token is not attempted.
/// * **Reads are bounded.** The body is counted as it streams and the read is
///   abandoned past [maxResponseBytes], so a huge or endless response cannot
///   become a huge or endless string.
/// * **The caller owns the client.** Each call takes an `http.Client` supplied
///   by the caller. That is what makes cancellation real: `http` cannot cancel
///   an in-flight request, but closing the client it was issued on tears down
///   the connection. A widget that disposes mid-flight closes its client and
///   discards the result.
///
/// A failure caused by the caller closing the client mid-flight arrives as
/// [ApiFailureKind.offline], because `http` reports it as a `ClientException`
/// indistinguishable from a refused connection. Callers must therefore discard
/// any result that arrives after they were disposed.
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

import 'api_config.dart';
import 'api_error.dart';
import 'auth_storage.dart';
import 'dto/health_response.dart';
import 'dto/project_response.dart';
import 'dto_limits.dart';

/// One error envelope, already bounded.
typedef _Envelope = ({String? errorClass, List<String> fields});

class ApiClient {
  ApiClient({
    required this.config,
    required this.auth,
    Duration? timeout,
  }) : timeout = timeout ?? defaultRequestTimeout;

  /// Prefix every route below is built from, spelled literally.
  static const String apiPrefix = '/api/v1';

  /// `GET /api/v1/health` - authenticated liveness.
  static const String healthPath = '/api/v1/health';

  /// `GET /api/v1/projects/{project_id}` - one project, read-only.
  static String projectPath(int projectId) => '/api/v1/projects/$projectId';

  /// Deadline for one request, covering the send and the body read.
  static const Duration defaultRequestTimeout = Duration(seconds: 10);

  /// Ceiling on a response body. Larger is refused, never truncated.
  static const int maxResponseBytes = 256 * 1024;

  /// Ceiling on a constructed request URI.
  static const int maxUriChars = 2048;

  final ApiConfig config;
  final AuthStorage auth;

  /// Deadline applied to the send and to the body read separately.
  final Duration timeout;

  /// `GET /api/v1/health`.
  ///
  /// [client] is owned and closed by the caller.
  Future<HealthResponse> fetchHealth({required http.Client client}) =>
      _getJson(client: client, path: healthPath, parse: HealthResponse.fromJson);

  /// `GET /api/v1/projects/{projectId}`.
  ///
  /// The identifier is re-bounded here even though the view validates it first,
  /// because a bound that only exists in a widget is not a bound.
  Future<ProjectResponse> fetchProject({
    required int projectId,
    required http.Client client,
  }) {
    if (projectId < 1 || projectId > maxProjectId) {
      throw const ApiFailure(ApiFailureKind.invalidRequest);
    }
    return _getJson(
      client: client,
      path: projectPath(projectId),
      parse: ProjectResponse.fromJson,
    );
  }

  Future<T> _getJson<T>({
    required http.Client client,
    required String path,
    required T Function(Object? decoded) parse,
  }) async {
    if (path.length > maxRequestPathChars ||
        !path.startsWith('$apiPrefix/') ||
        path.contains('..')) {
      throw const ApiFailure(ApiFailureKind.invalidRequest);
    }

    final token = auth.token;
    if (token == null) {
      throw const ApiFailure(ApiFailureKind.noToken);
    }

    final uri = config.resolve(path);
    if (uri.toString().length > maxUriChars) {
      throw const ApiFailure(ApiFailureKind.invalidRequest);
    }

    final request = http.Request('GET', uri)
      ..headers['accept'] = 'application/json'
      ..headers['authorization'] = 'Bearer $token';

    try {
      final response = await client.send(request).timeout(timeout);
      final read = await _readBoundedBody(response).timeout(timeout);

      if (response.statusCode != 200) {
        throw _failureForStatus(response.statusCode, read);
      }
      if (read.exceeded) {
        throw const ApiFailure(ApiFailureKind.payloadTooLarge, statusCode: 200);
      }
      final text = read.text;
      if (text == null) {
        // Bytes that are not UTF-8 never become a string, so there is nothing
        // to decode. This is not a null-check shortcut: it is the one case
        // where a 200 carries no readable body at all.
        throw const ApiFailure(ApiFailureKind.malformed, statusCode: 200);
      }

      final Object? decoded;
      try {
        decoded = jsonDecode(text);
      } on FormatException {
        throw const ApiFailure(ApiFailureKind.malformed);
      }
      return parse(decoded);
    } on TimeoutException {
      throw const ApiFailure(ApiFailureKind.timeout);
    } on http.ClientException {
      // Covers a refused connection and a client the caller closed mid-flight;
      // see the library comment above.
      throw const ApiFailure(ApiFailureKind.offline);
    }
  }

  /// Reads a body up to [maxResponseBytes], reporting whether it was exceeded.
  ///
  /// An over-limit body is never partially decoded: half a JSON document is not
  /// a JSON document, and the point of the ceiling is to stop reading.
  Future<_BodyRead> _readBoundedBody(http.StreamedResponse response) async {
    final chunks = <List<int>>[];
    var total = 0;
    await for (final chunk in response.stream) {
      total += chunk.length;
      if (total > maxResponseBytes) {
        return const _BodyRead(text: null, exceeded: true);
      }
      chunks.add(chunk);
    }

    final String text;
    try {
      text = utf8.decode(<int>[for (final chunk in chunks) ...chunk]);
    } on FormatException {
      return const _BodyRead(text: null, exceeded: false, undecodable: true);
    }
    return _BodyRead(text: text, exceeded: false);
  }

  /// Maps a non-200 response onto a bounded failure.
  ///
  /// The envelope is read for its class name and field locations only. Its
  /// `message` is deliberately discarded: the UI shows fixed copy chosen here,
  /// so nothing the server writes becomes the sentence a user reads.
  ApiFailure _failureForStatus(int status, _BodyRead read) {
    final envelope = read.text == null
        ? (errorClass: null, fields: const <String>[])
        : _readEnvelope(read.text!);

    final kind = switch (status) {
      401 => ApiFailureKind.unauthorized,
      403 => ApiFailureKind.forbidden,
      404 => ApiFailureKind.notFound,
      413 => ApiFailureKind.payloadTooLarge,
      422 => ApiFailureKind.validation,
      >= 500 => ApiFailureKind.server,
      _ => ApiFailureKind.unexpected,
    };

    // An over-limit body must not mask the status, but authentication still
    // wins: signing out must not depend on how large the rejection was.
    if (read.exceeded &&
        kind != ApiFailureKind.unauthorized &&
        kind != ApiFailureKind.forbidden) {
      return ApiFailure(ApiFailureKind.payloadTooLarge, statusCode: status);
    }
    if (read.undecodable && kind == ApiFailureKind.unexpected) {
      return ApiFailure(ApiFailureKind.malformed, statusCode: status);
    }

    return ApiFailure(
      kind,
      statusCode: status,
      errorClass: envelope.errorClass,
      fields: envelope.fields,
    );
  }

  _Envelope _readEnvelope(String body) {
    final Object? decoded;
    try {
      decoded = jsonDecode(body);
    } on FormatException {
      return (errorClass: null, fields: const <String>[]);
    }
    if (decoded is! Map) {
      return (errorClass: null, fields: const <String>[]);
    }

    final rawError = decoded['error'];
    final String? errorClass;
    if (rawError is String &&
        rawError.isNotEmpty &&
        rawError.length <= ApiFailure.maxErrorClassChars) {
      errorClass = rawError;
    } else {
      errorClass = null;
    }

    final fields = <String>[];
    final rawFields = decoded['fields'];
    if (rawFields is List) {
      for (final field in rawFields) {
        if (fields.length >= ApiFailure.maxFields) {
          break;
        }
        if (field is String &&
            field.isNotEmpty &&
            field.length <= ApiFailure.maxFieldChars) {
          fields.add(field);
        }
      }
    }

    return (errorClass: errorClass, fields: fields);
  }
}

/// One bounded body read.
class _BodyRead {
  const _BodyRead({
    required this.text,
    required this.exceeded,
    this.undecodable = false,
  });

  final String? text;
  final bool exceeded;
  final bool undecodable;
}

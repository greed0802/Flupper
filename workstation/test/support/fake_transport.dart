/// A deterministic, in-repo HTTP transport for tests.
///
/// This replaces a mocking package rather than adding one. It does everything
/// the tests need and nothing else:
///
/// * records every request and a snapshot of its headers, so a test can assert
///   the exact URL, method and `authorization` header;
/// * counts `close()` calls, which is the whole mechanism behind the
///   cancellation claim - a test proves the transport was closed on dispose;
/// * fails `send` once closed, the way `package:http` does, so the
///   "closed mid-flight" path is exercised with real behaviour instead of a
///   stub that cannot happen.
///
/// No test in this directory opens a socket. Every response is built from
/// bytes held in memory.
library;

import 'dart:async';
import 'dart:convert';

import 'package:http/http.dart' as http;

class FakeTransport extends http.BaseClient {
  FakeTransport(this.handler);

  /// Answers one request. May return a future that completes later.
  final Future<http.StreamedResponse> Function(http.BaseRequest request) handler;

  final List<http.BaseRequest> requests = <http.BaseRequest>[];
  final List<Map<String, String>> headerSnapshots = <Map<String, String>>[];

  int closeCount = 0;

  bool get closed => closeCount > 0;

  /// The `authorization` header of request [index], or `null`.
  String? authorizationAt(int index) =>
      headerSnapshots[index]['authorization'];

  /// The `accept` header of request [index], or `null`.
  String? acceptAt(int index) => headerSnapshots[index]['accept'];

  @override
  Future<http.StreamedResponse> send(http.BaseRequest request) {
    requests.add(request);
    headerSnapshots.add(Map<String, String>.from(request.headers));
    if (closed) {
      // Mirrors IOClient.send after close(); the message is the real one.
      return Future<http.StreamedResponse>.error(
        http.ClientException(
          'HTTP request failed. Client is already closed.',
          request.url,
        ),
      );
    }
    return handler(request);
  }

  @override
  void close() {
    closeCount++;
  }
}
/// A single fixed response.
http.StreamedResponse streamedResponse(
  int status,
  String body, {
  Map<String, String> headers = const <String, String>{
    'content-type': 'application/json',
  },
}) {
  return http.StreamedResponse(
    Stream<List<int>>.value(utf8.encode(body)),
    status,
    headers: headers,
    reasonPhrase: 'reason',
    contentLength: utf8.encode(body).length,
  );
}

/// A JSON response of the shape the gateway actually sends.
http.StreamedResponse jsonResponse(int status, Object body) =>
    streamedResponse(status, jsonEncode(body));

/// A response whose body is exact bytes, for the cases JSON cannot express.
http.StreamedResponse rawResponse(int status, List<int> bytes) {
  return http.StreamedResponse(
    Stream<List<int>>.value(bytes),
    status,
    headers: const <String, String>{'content-type': 'application/json'},
    reasonPhrase: 'reason',
    contentLength: bytes.length,
  );
}

/// The gateway's error envelope.
http.StreamedResponse errorEnvelope(
  int status,
  String error, {
  String message = 'server text that must never be rendered',
  List<String>? fields,
}) {
  final payload = <String, Object>{'error': error, 'message': message};
  if (fields != null) {
    payload['fields'] = fields;
  }
  return jsonResponse(status, payload);
}

/// A body larger than the client's ceiling, on any status.
http.StreamedResponse oversizedResponse(int status) {
  final padding = 'a' * 300000;
  return streamedResponse(
    status,
    '{"status":"ok","sandbox_root_ready":true,"pad":"$padding"}',
  );
}

/// A body that never ends. The client must stop reading, not wait.
http.StreamedResponse endlessResponse() {
  final chunk = List<int>.filled(65536, 0x61);
  Stream<List<int>> body() async* {
    while (true) {
      yield chunk;
    }
  }

  return http.StreamedResponse(body(), 200);
}

/// A response that is never produced, for exercising the deadline.
Future<http.StreamedResponse> neverResponds() => Completer<http.StreamedResponse>().future;

/// A transport that refuses the connection before any response exists.
Future<http.StreamedResponse> connectionRefused(Uri url) =>
    Future<http.StreamedResponse>.error(
      http.ClientException('Connection refused', url),
    );

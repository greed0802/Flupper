/// One failure model for every way an API call can go wrong.
///
/// The renderer shows [ApiFailure.userMessage], which is fixed copy written
/// here. The gateway's own `message` field is **never** retained: it is
/// bounded server text, but it is still server text, and a UI that echoes a
/// server sentence has handed the wire the last word on what the user reads.
///
/// What is retained for diagnostics is the class name from the error envelope
/// plus any field locations, both truncated to a fixed width, because those are
/// the parts that say *where* to look without carrying a value.
library;

/// The set of outcomes the UI distinguishes.
enum ApiFailureKind {
  /// The API address is missing or not permitted.
  configuration,

  /// No bearer token is held, so the request was never sent.
  noToken,

  /// The client refused to build the request. The gateway never saw it.
  invalidRequest,

  /// 401 - the token is absent, malformed or wrong. Treated as logout.
  unauthorized,

  /// 403 - authenticated but not allowed (approval gates answer this way).
  forbidden,

  /// 404 - the addressed object does not exist.
  notFound,

  /// 413 - a request or response exceeded its byte ceiling.
  payloadTooLarge,

  /// 422 - the server rejected the shape of what was sent.
  validation,

  /// 5xx - the server failed. Fixed copy only.
  server,

  /// A 4xx the UI has no specific treatment for.
  unexpected,

  /// The body did not parse as the declared DTO.
  malformed,

  /// The request did not complete inside the client deadline.
  timeout,

  /// The gateway could not be reached at all.
  offline,
}

/// A bounded, renderable failure.
class ApiFailure implements Exception {
  const ApiFailure(
    this.kind, {
    this.statusCode,
    this.errorClass,
    this.fields = const <String>[],
  });

  /// Longest server-provided class name retained. Anything longer is dropped
  /// rather than truncated, because a truncated name is a misleading name.
  static const int maxErrorClassChars = 64;

  /// Most field locations retained from a 422 envelope.
  static const int maxFields = 8;

  /// Longest field location retained.
  static const int maxFieldChars = 64;

  final ApiFailureKind kind;

  /// HTTP status, when a response was received at all.
  final int? statusCode;

  /// The `error` value from the server envelope, if it was short enough to be a
  /// class name. Diagnostics only - never rendered as user copy.
  final String? errorClass;

  /// The `fields` list from a 422 envelope, bounded. Diagnostics only.
  final List<String> fields;

  /// True when the only correct response is to drop the token and re-authenticate.
  bool get isAuthenticationFailure =>
      kind == ApiFailureKind.unauthorized || kind == ApiFailureKind.noToken;

  /// Fixed, local copy for this kind. No server string reaches this getter.
  String get userMessage => switch (kind) {
    ApiFailureKind.configuration =>
      'No API address is configured. Enter the address of the local gateway.',
    ApiFailureKind.noToken => 'Sign in to continue.',
    ApiFailureKind.invalidRequest => 'That input is not valid for this request.',
    ApiFailureKind.unauthorized =>
      'The gateway rejected this session. Sign in again.',
    ApiFailureKind.forbidden =>
      'The gateway refused this action for this session.',
    ApiFailureKind.notFound => 'Not found on the gateway.',
    ApiFailureKind.payloadTooLarge =>
      'The gateway response was larger than this client accepts.',
    ApiFailureKind.validation =>
      'The gateway rejected the request shape.',
    ApiFailureKind.server => 'The gateway reported an internal error.',
    ApiFailureKind.unexpected => 'The gateway returned an unexpected status.',
    ApiFailureKind.malformed => 'The gateway response could not be read.',
    ApiFailureKind.timeout => 'The gateway did not answer in time.',
    ApiFailureKind.offline => 'The gateway could not be reached.',
  };

  @override
  String toString() {
    final parts = <String>[
      'ApiFailure(${kind.name}',
      if (statusCode != null) ' status=$statusCode',
      if (errorClass != null) ' error=$errorClass',
      if (fields.isNotEmpty) ' fields=${fields.length}',
      ')',
    ];
    return parts.join();
  }
}

/// Where the workstation is allowed to send requests, and what shape that
/// address may take.
///
/// Three rules, all enforced at construction rather than at call sites:
///
/// * **There is no default.** An unconfigured client fails with a visible
///   configuration error. It never falls back to `localhost`, because a silent
///   fallback in browser-facing code turns "I forgot to configure this" into a
///   request to whatever happens to answer on the viewer's own machine.
/// * **Plaintext is loopback-only.** `https` is accepted for any host; `http` is
///   accepted only when the host is the loopback interface. Anything else is a
///   refused configuration, not a warning.
/// * **The address carries no credentials.** A `user:password@host` authority,
///   a query or a fragment is rejected outright, so no part of a secret can be
///   smuggled into a URL that ends up in a crash report or a server log.
///
/// The base address is an origin: scheme, host and optional port. A sub-path is
/// refused so that route construction cannot drift; the API prefix lives in
/// [ApiClient], next to the paths that use it.
library;

/// Raised for a base address that is absent, malformed or not permitted.
///
/// The message is a fixed sentence chosen by this file. Nothing here echoes the
/// supplied address back, because a rejected address is the one most likely to
/// be a mistyped secret.
class ApiConfigException implements Exception {
  const ApiConfigException(this.reason);

  /// Local, bounded copy. Safe to render.
  final String reason;

  @override
  String toString() => 'ApiConfigException: $reason';
}

/// A validated API origin.
class ApiConfig {
  const ApiConfig._(this.baseUri);

  /// The validated origin. Always has a scheme, a host and no user-info.
  final Uri baseUri;

  /// Compile-time base address, used for desktop development only.
  ///
  /// A base address is not a credential, so it is safe to compile in. The
  /// bearer token is *not* read this way - see [AuthStorage].
  static const String compileTimeBaseUrl =
      String.fromEnvironment('FLUPPER_API_URL');

  /// Hosts treated as the local machine for the plaintext exception.
  static const Set<String> loopbackHosts = <String>{
    'localhost',
    '127.0.0.1',
    '::1',
    '[::1]',
  };

  static const Set<String> allowedSchemes = <String>{'http', 'https'};

  /// The compile-time address as a config, or `null` when it was not supplied.
  ///
  /// Throws [ApiConfigException] when it was supplied but is not permitted, so
  /// a bad `--dart-define` is reported instead of being ignored.
  static ApiConfig? fromCompileTime() {
    if (compileTimeBaseUrl.trim().isEmpty) {
      return null;
    }
    return parse(compileTimeBaseUrl);
  }

  /// Validates one address, or throws [ApiConfigException].
  ///
  /// Deliberately synchronous and dependency-free: the same function is used by
  /// the login form, by the compile-time path and by the tests.
  static ApiConfig parse(String raw) {
    final trimmed = raw.trim();
    if (trimmed.isEmpty) {
      throw const ApiConfigException(
        'No API address is configured. Enter the address of the local gateway.',
      );
    }

    final uri = Uri.tryParse(trimmed);
    if (uri == null || !uri.hasScheme || uri.host.isEmpty) {
      throw const ApiConfigException(
        'That is not an absolute address. Use a full origin such as https://host:port',
      );
    }

    final scheme = uri.scheme.toLowerCase();
    if (!allowedSchemes.contains(scheme)) {
      throw const ApiConfigException(
        'Only http and https addresses are supported.',
      );
    }

    final host = uri.host.toLowerCase();
    if (scheme == 'http' && !loopbackHosts.contains(host)) {
      throw const ApiConfigException(
        'Plaintext http is only allowed for the local machine. Use https.',
      );
    }

    if (uri.userInfo.isNotEmpty) {
      throw const ApiConfigException(
        'Credentials must not be embedded in the API address.',
      );
    }

    if (uri.hasQuery || uri.hasFragment) {
      throw const ApiConfigException(
        'The API address must not carry a query or a fragment.',
      );
    }

    if (uri.path.isNotEmpty && uri.path != '/') {
      throw const ApiConfigException(
        'The API address must be an origin, without a path.',
      );
    }

    return ApiConfig._(
      Uri(scheme: scheme, host: host, port: uri.hasPort ? uri.port : null),
    );
  }

  /// Builds an absolute request URI for one API path.
  ///
  /// [path] must be an absolute path such as `/api/v1/health`; a relative path
  /// would silently resolve against the origin in a way the caller did not ask
  /// for, so it is rejected instead.
  Uri resolve(String path) {
    if (!path.startsWith('/')) {
      throw ArgumentError.value(path, 'path', 'must be an absolute path');
    }
    return baseUri.replace(path: path);
  }

  @override
  String toString() => 'ApiConfig(${baseUri.origin})';
}

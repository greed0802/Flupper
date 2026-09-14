/// Selects the process-environment reader for the platform being built.
///
/// The conditional export is the mechanism that keeps the token off the web:
/// a browser build resolves `dev_env_stub.dart`, whose reader always returns
/// `null`, so there is no compiled code path from a browser bundle to an
/// environment-supplied token even if one were somehow set on the host.
library;

export 'dev_env_stub.dart' if (dart.library.io) 'dev_env_io.dart';

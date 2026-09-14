/// Reads the desktop development token from the process environment.
///
/// This file is the *web* variant of a conditional import: a web build has no
/// process environment, so there is nothing to read and no code path that could
/// put a token into a browser bundle. The io variant is selected by
/// `dev_env.dart` when `dart.library.io` is available.
library;

/// Always `null` where there is no process environment.
String? readDevTokenEnvironment() => null;

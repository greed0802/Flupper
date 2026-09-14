/// Reads the desktop development token from the process environment.
///
/// Selected by `dev_env.dart` for a build with `dart.library.io`. Only the
/// desktop development shell uses this, and only for a locally started gateway;
/// the web build resolves the stub instead, so a browser bundle has no route to
/// an environment-supplied token at all.
library;

import 'dart:io' show Platform;

/// Name of the environment variable a desktop development run may set.
///
/// Documented rather than exported as a constant: the web variant of this
/// library must not need to know the name exists.
const String _devTokenEnvVar = 'FLUPPER_DEV_TOKEN';

/// The trimmed value of `FLUPPER_DEV_TOKEN`, or `null` when unset or blank.
String? readDevTokenEnvironment() {
  final value = Platform.environment[_devTokenEnvVar];
  if (value == null) {
    return null;
  }
  final trimmed = value.trim();
  return trimmed.isEmpty ? null : trimmed;
}

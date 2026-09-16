/// Reads the operating system identity on a build that has one.
///
/// Selected by `platform_facts.dart` when `dart.library.io` is available, which
/// is every target except the browser.
library;

import 'dart:io' show Platform;

/// True when the running build is an Android build.
///
/// The one platform whose loopback address is not `127.0.0.1`. See
/// `core/android_loopback.dart` for what is done with that, and under which two
/// additional conditions.
bool runningOnAndroid() => Platform.isAndroid;
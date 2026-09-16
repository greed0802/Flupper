/// Reads the operating system identity where there is no operating system.
///
/// This file is the *web* variant of a conditional import. A browser build has
/// no `dart:io`, so it has no `Platform` to ask and no way to be Android; the
/// answer is the constant `false` rather than a guess.
library;

/// Always `false`: a browser build is not an Android build.
bool runningOnAndroid() => false;
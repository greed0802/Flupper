/// Bounded replacements for `pumpAndSettle` at an async boundary.
///
/// `pumpAndSettle` is the wrong tool when a spinner is on screen: a
/// `CircularProgressIndicator` schedules a frame forever, so it never settles
/// and the test dies on a timeout instead of on its own assertion. The
/// two-`pump` idiom usually hides that on the VM, but a `Stream` delivers its
/// element on an event-loop task rather than a microtask, so under the web
/// binding two frames do not always cover it and the assertion runs before the
/// response arrives.
///
/// These wait for exactly the condition the test is about, pumping a bounded
/// number of frames, and fail with a real message when it never comes true.
/// They cannot hang, and they cannot pass by waiting for something else.
library;

import 'package:flutter_test/flutter_test.dart';

/// Pumps until [condition] holds, or fails after [maxPumps] frames.
Future<void> pumpUntil(
  WidgetTester tester,
  bool Function() condition, {
  String description = 'condition',
  int maxPumps = 30,
  Duration step = const Duration(milliseconds: 20),
}) async {
  for (var attempt = 0; attempt < maxPumps; attempt++) {
    if (condition()) {
      return;
    }
    await tester.pump(step);
  }

  if (!condition()) {
    fail(
      '$description still not true after $maxPumps pumps of '
      '${step.inMilliseconds}ms',
    );
  }
}

/// Pumps until [finder] matches, or fails after [maxPumps] frames.
Future<void> pumpUntilFound(
  WidgetTester tester,
  Finder finder, {
  int maxPumps = 30,
  Duration step = const Duration(milliseconds: 20),
}) {
  return pumpUntil(
    tester,
    () => finder.evaluate().isNotEmpty,
    description: '$finder',
    maxPumps: maxPumps,
    step: step,
  );
}


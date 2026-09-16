/// The one exception to the plaintext rule, and the conditions it needs.
///
/// `ApiConfig` permits plaintext `http` only to the loopback interface. An
/// Android emulator breaks that assumption in a way the loopback rule cannot
/// express: the emulator is a separate virtual machine, so `127.0.0.1` inside it
/// is the emulator, and the developer's gateway is reached at the alias `10.0.2.2`
/// instead. That address is a *development* address. It is not loopback on the
/// device, it is not encrypted, and it points at a gateway that must never be
/// reachable from anywhere but the developer's own machine.
///
/// So the exception is not a second entry in the loopback set. It is a separate
/// permission with two further conditions attached, and both of them are values
/// rather than compile-time globals so that a test can supply every combination:
///
/// * **the build must be an Android build.** A desktop or web build has real
///   loopback and no reason to name an emulator;
/// * **the build must be a debug build.** A release build is not a development
///   tool, and a release build talking plaintext to an emulator alias is either
///   a mistake or something worse.
///
/// The real facts are read through [AndroidEmulatorPolicy.current]. Tests build
/// the policy directly. Nothing here reads `kDebugMode` at the decision point,
/// because `kDebugMode` is a compile-time constant and a test that pretended to
/// change it would be asserting about a build that does not exist.
library;

import 'package:flutter/foundation.dart' show kDebugMode;

import 'platform/platform_facts.dart';

/// The addresses an Android emulator maps onto the host machine.
///
/// `10.0.2.2` is the standard alias in the Android emulator's default
/// user-mode network. `10.0.3.2` is the same alias under the older qemu
/// network; both are included because both name "the machine running the
/// emulator" and neither exists on a physical device.
///
/// The gateway itself is unchanged and still binds to exact `127.0.0.1`. This
/// constant describes how an emulator reaches that socket, not a widening of it.
const Set<String> androidEmulatorHosts = <String>{'10.0.2.2', '10.0.3.2'};

/// The two facts the emulator exception is conditional on, taken as values.
class AndroidEmulatorPolicy {
  const AndroidEmulatorPolicy({
    required this.isAndroid,
    required this.isDebugBuild,
  });

  /// The running build's own facts: the real platform and the real build mode.
  ///
  /// The only place either fact is read. Every other user of this policy takes
  /// it as a parameter, so the decision is exercised by tests in all four
  /// combinations instead of being asserted about in a comment.
  factory AndroidEmulatorPolicy.current() => AndroidEmulatorPolicy(
    isAndroid: runningOnAndroid(),
    isDebugBuild: kDebugMode,
  );

  /// True when the build is running on Android.
  final bool isAndroid;

  /// True when this is a debug/development build.
  final bool isDebugBuild;

  /// Whether plaintext `http` to [host] is permitted by this policy.
  ///
  /// True only for an emulator alias, on Android, in a debug build. A release
  /// build answers `false` for every host, including the emulator aliases and
  /// including the true loopback names - the loopback set is a separate rule
  /// and is not affected by this one.
  bool allowsPlaintextHost(String host) =>
      isAndroid && isDebugBuild && androidEmulatorHosts.contains(host);

  @override
  String toString() =>
      'AndroidEmulatorPolicy(isAndroid: $isAndroid, '
      'isDebugBuild: $isDebugBuild)';
}
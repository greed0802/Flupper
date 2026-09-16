import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/android_loopback.dart';

void main() {
  group('AndroidEmulatorPolicy', () {
    test('accepts Android debug for aliases', () {
      final policy = const AndroidEmulatorPolicy(
        isAndroid: true,
        isDebugBuild: true,
      );
      expect(policy.allowsPlaintextHost('10.0.2.2'), isTrue);
      expect(policy.allowsPlaintextHost('10.0.3.2'), isTrue);
      expect(policy.allowsPlaintextHost('127.0.0.1'), isFalse);
    });

    test('rejects Android release', () {
      final policy = const AndroidEmulatorPolicy(
        isAndroid: true,
        isDebugBuild: false,
      );
      expect(policy.allowsPlaintextHost('10.0.2.2'), isFalse);
    });

    test('rejects non-Android debug', () {
      final policy = const AndroidEmulatorPolicy(
        isAndroid: false,
        isDebugBuild: true,
      );
      expect(policy.allowsPlaintextHost('10.0.2.2'), isFalse);
    });
  });
}

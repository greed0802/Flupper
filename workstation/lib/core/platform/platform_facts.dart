/// Selects the platform-identity reader for the platform being built.
///
/// The conditional export is the mechanism, the same one `dev_env.dart` uses: a
/// browser build resolves the stub, which answers `false`, so a browser bundle
/// has no `dart:io` import and no code path that could ever call the emulator
/// host permitted. The io variant answers with the real operating system.
///
/// This library reports one fact and nothing else. It is deliberately not a
/// utility bag: every fact it gains is a fact the security policy has to
/// consider.
library;

export 'platform_facts_stub.dart' if (dart.library.io) 'platform_facts_io.dart';
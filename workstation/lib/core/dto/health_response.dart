/// `GET /api/v1/health` response.
///
/// Mirrors `qsagent.api.contracts.HealthResponse`: two fields, both bounded on
/// arrival. The route is a liveness probe and carries no session, no project
/// and no credential, so nothing here can leak one.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class HealthResponse {
  const HealthResponse({required this.status, required this.sandboxRootReady});

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{'status', 'sandbox_root_ready'};

  /// Server liveness marker. Bounded to [maxHealthStatusChars].
  final String status;

  /// Whether the gateway's sandbox root exists. A boolean, never a path: the
  /// route deliberately does not tell a client where anything lives on disk.
  final bool sandboxRootReady;

  factory HealthResponse.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return HealthResponse(
      status: requireBoundedString(
        map,
        'status',
        maxLength: maxHealthStatusChars,
      ),
      sandboxRootReady: requireBool(map, 'sandbox_root_ready'),
    );
  }

  @override
  String toString() =>
      'HealthResponse(status: $status, sandboxRootReady: $sandboxRootReady)';
}

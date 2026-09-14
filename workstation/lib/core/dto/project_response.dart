/// `GET /api/v1/projects/{project_id}` response.
///
/// Mirrors `qsagent.api.contracts.ProjectResponse` (`project_id`, `name`). The
/// 5A workstation is read-only, so this DTO has no counterpart request model:
/// there is no `CreateProjectRequest` in the client and no `POST` path, because
/// creating a project mutates the store and that mutation is out of scope.
library;

import '../dto_limits.dart';
import 'strict_json.dart';

class ProjectResponse {
  const ProjectResponse({required this.projectId, required this.name});

  /// The declared key set. Exact match required - see `strict_json.dart`.
  static const Set<String> keys = <String>{'project_id', 'name'};

  /// Server-assigned identifier. Positive, and no larger than [maxProjectId].
  final int projectId;

  /// Project label, at most [maxProjectNameChars] characters.
  final String name;

  factory ProjectResponse.fromJson(Object? decoded) {
    final map = requireStrictMap(decoded, keys: keys);
    return ProjectResponse(
      projectId: requirePositiveInt(map, 'project_id', maxValue: maxProjectId),
      name: requireBoundedString(
        map,
        'name',
        maxLength: maxProjectNameChars,
      ),
    );
  }

  @override
  String toString() => 'ProjectResponse(projectId: $projectId, name: $name)';
}

/// Read-only single-project view: `GET /api/v1/projects/{project_id}`.
///
/// There is no project *list* route in the gateway, so this view asks for one
/// project by identifier and never assumes an array. There is no create route
/// either: 5A does not mutate the store, and a form that could would be a
/// different subphase with its own tests.
///
/// The identifier is bounded before a request is built. A project id is a
/// positive integer no larger than the largest value a Dart web integer holds
/// exactly, so a digit string that would round on the way to the URL is refused
/// locally instead of being sent as a different number.
library;

import 'package:flutter/material.dart';
import '../rates/rate_proposal_screen.dart';
import '../revisions/revision_diff_screen.dart';
import 'package:http/http.dart' as http;

import '../../core/api_client.dart';
import '../../core/api_error.dart';
import '../../core/dto/project_response.dart';
import '../../core/dto_limits.dart';
import '../common/resource_view.dart';

class ProjectView extends StatefulWidget {
  const ProjectView({
    super.key,
    required this.client,
    required this.clientFactory,
    required this.onUnauthorized,
  });

  final ApiClient client;

  /// Creates the `http.Client` for one request; closed in `dispose`.
  final http.Client Function() clientFactory;

  /// Called when the gateway rejects the session.
  final VoidCallback onUnauthorized;

  @override
  State<ProjectView> createState() => _ProjectViewState();
}

class _ProjectViewState extends State<ProjectView> {
  final TextEditingController _idController = TextEditingController();

  bool _loading = false;
  ApiFailure? _failure;
  ProjectResponse? _project;
  String? _inputError;
  http.Client? _transport;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _transport?.close();
    _transport = null;
    _idController.dispose();
    super.dispose();
  }

  /// Why the current text is not a project id, or `null` when it is.
  String? _validateId(String raw) {
    final text = raw.trim();
    if (text.isEmpty) {
      return 'Enter a project id.';
    }
    // Digits only: this rejects a sign, a decimal point, an exponent and a
    // unicode digit, none of which the route declares.
    if (!RegExp(r'^[0-9]+$').hasMatch(text)) {
      return 'Enter a whole number greater than zero.';
    }
    final parsed = int.tryParse(text);
    if (parsed == null || parsed < 1) {
      return 'Enter a whole number greater than zero.';
    }
    if (parsed > maxProjectId) {
      return 'That project id is larger than this client accepts.';
    }
    return null;
  }

  Future<void> _load(int projectId) async {
    final transport = widget.clientFactory();
    _transport = transport;
    try {
      final project = await widget.client.fetchProject(
        projectId: projectId,
        client: transport,
      );
      if (_disposed) return;
      setState(() {
        _loading = false;
        _project = project;
        _failure = null;
      });
    } on ApiFailure catch (failure) {
      if (_disposed) return;
      if (failure.isAuthenticationFailure) {
        widget.onUnauthorized();
        return;
      }
      setState(() {
        _loading = false;
        _failure = failure;
      });
    }
  }

  void _submit() {
    final error = _validateId(_idController.text);
    if (error != null) {
      setState(() => _inputError = error);
      return;
    }
    final projectId = int.parse(_idController.text.trim());
    _transport?.close();
    setState(() {
      _inputError = null;
      _loading = true;
      _failure = null;
    });
    _load(projectId);
  }

  @override
  Widget build(BuildContext context) {
    return Column(
      children: <Widget>[
        Padding(
          padding: const EdgeInsets.all(16),
          child: Row(
            crossAxisAlignment: CrossAxisAlignment.start,
            children: <Widget>[
              Expanded(
                child: TextField(
                  controller: _idController,
                  autocorrect: false,
                  enableSuggestions: false,
                  keyboardType: TextInputType.number,
                  onSubmitted: (_) => _submit(),
                  decoration: InputDecoration(
                    labelText: 'Project id',
                    errorText: _inputError,
                    border: const OutlineInputBorder(),
                  ),
                ),
              ),
              const SizedBox(width: 12),
              Padding(
                padding: const EdgeInsets.only(top: 4),
                child: FilledButton(
                  onPressed: _submit,
                  child: const Text('Load project'),
                ),
              ),
            ],
          ),
        ),
        Expanded(
          child: ResourceView<ProjectResponse>(
            title: 'Project',
            loading: _loading,
            failure: _failure,
            data: _project,
            onRetry: _submit,
            builder: (context, project) => Column(
              crossAxisAlignment: CrossAxisAlignment.start,
              children: <Widget>[
                Text('Id: ${project.projectId}'),
                const SizedBox(height: 4),
                Text('Name: ${project.name}'),
                const SizedBox(height: 16),
                Row(
                  children: [
                    FilledButton(
                      child: const Text('Revisions'),
                      onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => RevisionDiffScreen(
                          client: widget.client,
                          projectId: project.projectId,
                          clientFactory: widget.clientFactory,
                          onUnauthorized: widget.onUnauthorized,
                        ),
                      )),
                    ),
                    const SizedBox(width: 12),
                    FilledButton(
                      child: const Text('Rates'),
                      onPressed: () => Navigator.of(context).push(MaterialPageRoute(
                        builder: (_) => RateProposalScreen(
                          client: widget.client,
                          projectId: project.projectId,
                          clientFactory: widget.clientFactory,
                          onUnauthorized: widget.onUnauthorized,
                        ),
                      )),
                    ),
                  ],
                ),
                const SizedBox(height: 16),
                Text(
                  'Read-only. This view cannot create or change a project.',
                  style: Theme.of(context).textTheme.bodySmall,
                ),
              ],
            ),
          ),
        ),
      ],
    );
  }
}

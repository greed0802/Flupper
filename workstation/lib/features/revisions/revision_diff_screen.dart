library;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/api_client.dart';
import '../../core/api_error.dart';
import '../../core/dto/revision_diff_response.dart';
import '../../core/dto_limits.dart';
import '../common/id_input.dart';
import '../common/resource_view.dart';

class RevisionDiffScreen extends StatefulWidget {
  const RevisionDiffScreen({
    super.key,
    required this.client,
    required this.projectId,
    required this.clientFactory,
    required this.onUnauthorized,
  });

  final ApiClient client;
  final int projectId;
  final http.Client Function() clientFactory;
  final VoidCallback onUnauthorized;

  @override
  State<RevisionDiffScreen> createState() => _RevisionDiffScreenState();
}

class _RevisionDiffScreenState extends State<RevisionDiffScreen> {
  final TextEditingController _baseController = TextEditingController();
  final TextEditingController _targetController = TextEditingController();

  bool _loading = false;
  ApiFailure? _failure;
  RevisionDiffResponse? _response;
  String? _baseError;
  String? _targetError;
  http.Client? _transport;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _transport?.close();
    _baseController.dispose();
    _targetController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final parsedBase = parseId(_baseController.text, maxValue: maxDocumentId, noun: 'document id', ceiling: '$maxDocumentId');
    final parsedTarget = parseId(_targetController.text, maxValue: maxDocumentId, noun: 'document id', ceiling: '$maxDocumentId');
    
    setState(() {
      _baseError = parsedBase.isValid ? null : parsedBase.error;
      _targetError = parsedTarget.isValid ? null : parsedTarget.error;
    });

    if (!parsedBase.isValid || !parsedTarget.isValid) {
      return;
    }
    
    _transport?.close();
    _transport = widget.clientFactory();
    setState(() {
      _loading = true;
      _failure = null;
    });

    try {
      final response = await widget.client.fetchRevisionDiff(
        client: _transport!,
        projectId: widget.projectId,
        baseDocumentId: parsedBase.value!,
        targetDocumentId: parsedTarget.value!,
      );
      if (_disposed) return;
      setState(() {
        _loading = false;
        _response = response;
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

  @override
  Widget build(BuildContext context) {
    return Scaffold(
      appBar: AppBar(title: const Text('Revision Diff')),
      body: Center(
        child: ConstrainedBox(
          constraints: const BoxConstraints(maxWidth: 800),
          child: Column(
            children: <Widget>[
              Padding(
                padding: const EdgeInsets.all(16),
                child: Row(
                  crossAxisAlignment: CrossAxisAlignment.start,
                  children: <Widget>[
                    Expanded(
                      child: TextField(
                        controller: _baseController,
                        autocorrect: false,
                        enableSuggestions: false,
                        keyboardType: TextInputType.number,
                        onSubmitted: (_) => _submit(),
                        decoration: InputDecoration(
                          labelText: 'Base document id',
                          errorText: _baseError,
                          border: const OutlineInputBorder(),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Expanded(
                      child: TextField(
                        controller: _targetController,
                        autocorrect: false,
                        enableSuggestions: false,
                        keyboardType: TextInputType.number,
                        onSubmitted: (_) => _submit(),
                        decoration: InputDecoration(
                          labelText: 'Target document id',
                          errorText: _targetError,
                          border: const OutlineInputBorder(),
                        ),
                      ),
                    ),
                    const SizedBox(width: 12),
                    Padding(
                      padding: const EdgeInsets.only(top: 4),
                      child: FilledButton(
                        onPressed: _submit,
                        child: const Text('Diff'),
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ResourceView<RevisionDiffResponse>(
                  title: 'Changes',
                  scrolling: true,
                  loading: _loading,
                  failure: _failure,
                  data: _response,
                  onRetry: _submit,
                  builder: (context, response) => ListView.builder(
                    itemCount: response.items.length,
                    itemBuilder: (context, index) {
                      final change = response.items[index];
                      return ListTile(
                        title: Text('Identity: ${change.identityId} (${change.status})'),
                        subtitle: Text('Changed: ${change.changedFields.join(', ')}'),
                      );
                    },
                  ),
                ),
              ),
            ],
          ),
        ),
      ),
    );
  }
}

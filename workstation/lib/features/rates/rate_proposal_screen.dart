library;

import 'package:flutter/material.dart';
import 'package:http/http.dart' as http;

import '../../core/api_client.dart';
import '../../core/api_error.dart';
import '../../core/dto/rate_proposal_response.dart';
import '../../core/dto_limits.dart';
import '../common/id_input.dart';
import '../common/resource_view.dart';

class RateProposalScreen extends StatefulWidget {
  const RateProposalScreen({
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
  State<RateProposalScreen> createState() => _RateProposalScreenState();
}

class _RateProposalScreenState extends State<RateProposalScreen> {
  final TextEditingController _nodesController = TextEditingController();

  bool _loading = false;
  ApiFailure? _failure;
  RateProposalResponse? _response;
  String? _inputError;
  http.Client? _transport;
  bool _disposed = false;

  @override
  void dispose() {
    _disposed = true;
    _transport?.close();
    _nodesController.dispose();
    super.dispose();
  }

  Future<void> _submit() async {
    final parsed = parseIdList(
      _nodesController.text,
      maxValue: maxRateExactInt,
      maxItems: maxRateNodeIds,
      noun: 'node',
      ceiling: '$maxRateNodeIds',
    );
    if (!parsed.isValid) {
      setState(() => _inputError = parsed.error);
      return;
    }
    
    _transport?.close();
    _transport = widget.clientFactory();
    setState(() {
      _inputError = null;
      _loading = true;
      _failure = null;
    });

    try {
      final response = await widget.client.fetchRateProposals(
        client: _transport!,
        projectId: widget.projectId,
        nodeIds: parsed.values!,
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
      appBar: AppBar(title: const Text('Rate Proposals')),
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
                        controller: _nodesController,
                        autocorrect: false,
                        enableSuggestions: false,
                        keyboardType: TextInputType.number,
                        onSubmitted: (_) => _submit(),
                        decoration: InputDecoration(
                          labelText: 'Node ids (comma separated)',
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
                        child: const Text('Fetch'),
                      ),
                    ),
                  ],
                ),
              ),
              Expanded(
                child: ResourceView<RateProposalResponse>(
                  title: 'Proposals',
                  scrolling: true,
                  loading: _loading,
                  failure: _failure,
                  data: _response,
                  onRetry: _submit,
                  builder: (context, response) => ListView.builder(
                    itemCount: response.proposals.length,
                    itemBuilder: (context, index) {
                      final proposal = response.proposals[index];
                      return ListTile(
                        title: Text('Node: ${proposal.nodeId} (${proposal.status})'),
                        subtitle: Text(
                          proposal.isNormalized 
                             ? '${proposal.normalizedAmount} ${proposal.normalizedUnit}' 
                             : (proposal.reason ?? 'Unknown reason')
                        ),
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

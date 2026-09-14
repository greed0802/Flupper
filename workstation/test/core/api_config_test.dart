import 'package:flutter_test/flutter_test.dart';
import 'package:flupper_workstation/core/api_config.dart';

/// Every rule in [ApiConfig] is a refusal, so every test here asserts a refusal
/// or its absence. None of them assert a default, because there is no default.
void main() {
  group('ApiConfig.parse rejects', () {
    test('an empty address', () {
      expect(
        () => ApiConfig.parse(''),
        throwsA(isA<ApiConfigException>()),
      );
      expect(
        () => ApiConfig.parse('   '),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('a relative address', () {
      expect(
        () => ApiConfig.parse('/api/v1'),
        throwsA(isA<ApiConfigException>()),
      );
      expect(
        () => ApiConfig.parse('localhost:8000'),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('a scheme that is not http or https', () {
      expect(
        () => ApiConfig.parse('ftp://127.0.0.1:8000'),
        throwsA(isA<ApiConfigException>()),
      );
      expect(
        () => ApiConfig.parse('file:///tmp/x'),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('plaintext http to a host that is not the local machine', () {
      expect(
        () => ApiConfig.parse('http://gateway.example.test:8000'),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('an address carrying credentials', () {
      expect(
        () => ApiConfig.parse('https://user:hunter2@gateway.example.test'),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('a query or a fragment', () {
      expect(
        () => ApiConfig.parse('https://gateway.example.test?a=b'),
        throwsA(isA<ApiConfigException>()),
      );
      expect(
        () => ApiConfig.parse('https://gateway.example.test#frag'),
        throwsA(isA<ApiConfigException>()),
      );
    });

    test('a sub-path', () {
      expect(
        () => ApiConfig.parse('https://gateway.example.test/flupper'),
        throwsA(isA<ApiConfigException>()),
      );
    });
  });

  group('ApiConfig.parse accepts', () {
    test('https to any host', () {
      final config = ApiConfig.parse('https://gateway.example.test');
      expect(config.baseUri.scheme, 'https');
      expect(config.baseUri.host, 'gateway.example.test');
    });

    test('plaintext http only to loopback', () {
      for (final host in <String>['localhost', '127.0.0.1', '[::1]']) {
        final config = ApiConfig.parse('http://$host:8000');
        expect(config.baseUri.scheme, 'http', reason: host);
      }
    });

    test('a trailing slash and mixed-case scheme', () {
      final config = ApiConfig.parse('HTTPS://Gateway.Example.Test/');
      expect(config.baseUri.scheme, 'https');
      expect(config.baseUri.host, 'gateway.example.test');
      expect(config.baseUri.path, isEmpty);
    });

    test('an explicit port, kept', () {
      final config = ApiConfig.parse('https://gateway.example.test:8443');
      expect(config.baseUri.port, 8443);
    });
  });

  group('resolve', () {
    test('builds an absolute URI under the origin', () {
      final config = ApiConfig.parse('http://127.0.0.1:8000');
      expect(
        config.resolve('/api/v1/health').toString(),
        'http://127.0.0.1:8000/api/v1/health',
      );
      expect(
        config.resolve('/api/v1/projects/7').toString(),
        'http://127.0.0.1:8000/api/v1/projects/7',
      );
    });

    test('refuses a relative path', () {
      final config = ApiConfig.parse('http://127.0.0.1:8000');
      expect(() => config.resolve('api/v1/health'), throwsArgumentError);
    });
  });

  group('compile-time address', () {
    test('is absent in this test run, so nothing is defaulted', () {
      expect(ApiConfig.compileTimeBaseUrl, isEmpty);
      expect(ApiConfig.fromCompileTime(), isNull);
    });
  });

  test('a refused address is never echoed back in the reason', () {
    try {
      ApiConfig.parse('https://user:hunter2@gateway.example.test');
      fail('expected a refusal');
    } on ApiConfigException catch (error) {
      expect(error.reason.contains('hunter2'), isFalse);
      expect(error.reason.contains('gateway.example.test'), isFalse);
    }
  });
}

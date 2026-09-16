# Flupper workstation (Phase 5A shell; tablet trial next)

A read-only Flutter client for the local Flupper gateway.

## What 5A is

Two screens behind one bearer token:

| Screen | Route | Direction |
|---|---|---|
| Gateway health | `GET /api/v1/health` | response only |
| Project read | `GET /api/v1/projects/{project_id}` | response only |

That is the whole surface. There is no project list route in the gateway, so the
client asks for one project by identifier and never assumes an array. There is
no create route in the client either: creating a project mutates the store, and
5A does not mutate anything.

Revision diff, rate proposal, and artifact contracts now exist on the authenticated
backend. This client currently exposes only health and single-project read views;
5B–5D UI screens remain deferred to the tablet/mobile trial.

## Toolchain (pinned, and why this pin)

| | Version |
|---|---|
| Flutter | **3.47.2** stable, framework revision `d3b14c876900e553bc736ca19295fc09e3853e8e`, 2026-08-26 |
| Dart | **3.13.2** |
| `http` | 1.6.0 (resolved; `pubspec.lock` is committed) |
| `flutter_lints` | 6.0.0 (resolved) |

Phase 5A was verified with `flutter analyze --fatal-infos`, 107 VM tests, Web build,
and sequential per-file Chrome tests in CI. The current Flutter shell remains
read-only; tablet/mobile UI work is planned separately.

**The approved plan said 3.24.1. This pin is 3.47.2, deliberately.** The reason
is not preference: 3.24.1 is not installed on this workstation, the only SDK
present is 3.47.2 at `C:\src\flutter`, and pinning a version that has never been
executed would replace a measured result with an assumption. The pin follows the
toolchain that was actually run.

**No 3.47.2-specific API is used.** The newest language feature in `lib/` is the
unnamed `library;` declaration (Dart 3.4); everything else - records, pattern
matching, switch expressions - is Dart 3.0. What keeps an older SDK out is the
`environment: sdk: ^3.13.2` line, which `flutter create` generated under 3.47.2.
It has not been loosened, because loosening it would assert compatibility with an
SDK this machine has never run. Anyone who needs a 3.24.1 build has to do that
work under 3.24.1 and record the result; nothing in this repository claims it.

The CI workflow pins the same 3.47.2 exactly, for the same reason: "it passed on
a newer SDK" is a different claim.

### Re-verifying in a fresh environment

```bash
cd workstation
flutter clean                       # removes build/ and .dart_tool/
rm -rf .dart_tool
flutter pub get                     # re-resolves against the committed lock file
flutter analyze --fatal-infos
flutter test
flutter build web
```

`flutter test --platform chrome` belongs in that list on a machine that has a
Chrome executable - but **one file at a time**, not as a bulk suite run:

```bash
# Web-platform tests, sequentially, one file per Chrome session.
set -euo pipefail
while IFS= read -r -d '' file; do
  echo "--- Chrome: ${file} ---"
  flutter test --platform chrome "${file}"
done < <(find test -type f -name '*_test.dart' -print0)
```

### Why the Chrome tests run per file

Running the whole suite as a single `flutter test --platform chrome` invocation
loses the browser connection partway through in this project's environment: the
run stalls after the first file has loaded and never reports a result. Run one
file per invocation, every file passes.

So CI does exactly that, and the loop is written to stay honest about it:

- **Discovery is null-delimited** (`find -print0` into `read -d ''`), so a test
  path containing a space can never be split into two bogus arguments. The
  expansion is quoted for the same reason.
- **The file name is printed before each run**, so a log that stops mid-suite
  names the file that stopped it. A bulk run leaves you guessing.
- **It fails fast.** `set -euo pipefail` means the first non-zero exit ends the
  loop and fails the job, rather than letting later files mask an earlier
  failure.

The cost is that each file starts its own Chrome session and its own compile, so
the step is slower than one bulk invocation would be. That is the trade: a slower
step that reports, over a faster one that hangs.

The sequential per-file form is verified by the Workstation GitHub Actions job.
A local machine without Chrome may still report this check as unavailable; that
does not change the CI result.

## The address is configured, never assumed

An unconfigured build shows a configuration error. It does not fall back to
`localhost`; in browser-facing code that fallback points the viewer's browser at
the viewer's own machine and hides the misconfiguration.

1. **Compile-time address** (desktop development, and web builds you control):

   ```bash
   flutter run -d windows --dart-define=FLUPPER_API_URL=http://127.0.0.1:8000
   ```

2. **The login form.** Anything not supplied above is typed in at run time.

Rules enforced by `ApiConfig`:

- `https` is accepted for any host; `http` only for loopback
  (`localhost`, `127.0.0.1`, `::1`);
- no credentials in the address, no query, no fragment, no sub-path;
- the address is an origin. The `/api/v1` prefix lives in `ApiClient`, next to
  the paths that use it.

## The token

- held in memory for one run, and nowhere else. Persistence is off
  (`AuthStorage.persistenceEnabled` is a compile-time `false`);
- never a compile-time value on web - not in `--dart-define`, not in a URL, not
  in browser storage, not in a log line. The web path to a token is the form;
- on desktop, a development run may be seeded from `FLUPPER_DEV_TOKEN` in the
  process environment. `dev_env.dart` resolves a stub on web whose reader always
  returns `null`, so a browser bundle has no code path to it;
- sent per request as `Authorization: Bearer <token>` by `ApiClient`, which
  refuses to send at all when no token is held. `package:http` has no
  interceptor, so there is no other place a header could be added.

## Cancellation

`package:http` cannot cancel a request that has started. Each view therefore
creates its own `http.Client` per request and closes it in `dispose()`, which
tears down the connection, and discards any result that arrives after disposal.
Both halves are asserted in `test/features/`.

## Running it

```bash
flutter pub get
flutter analyze --fatal-infos
flutter test
flutter build web                 # requires no browser
flutter test --platform chrome    # requires a Chrome executable
```

The gateway must be running and authenticated first. The workstation never
starts, stops or reconfigures the gateway, and never runs a sandbox process.

## Layout

```
lib/
  core/
    api_client.dart        the only place a request is built
    api_config.dart        origin validation, no default
    api_error.dart         one bounded failure model, fixed local copy
    auth_storage.dart      in-memory token, bounds, redaction
    dto_limits.dart        bounds mirrored from the server, asserted by a Python test
    dto/                   HealthResponse, ProjectResponse, strict readers
    platform/dev_env.dart  conditional import: desktop reads the environment, web never does
  features/
    auth/                  the login form
    common/                the shared loading/failed/loaded view
    health/                GET /api/v1/health
    projects/              GET /api/v1/projects/{project_id}

5B–5D DTOs exist in core, but tablet/mobile screens for revision, rates, and
artifacts are intentionally deferred.
test/                      no sockets: every response is bytes in memory
```

`backend/tests/test_workstation_contract_mirror.py` keeps this client and the
gateway in step: the bounds are compared against the Pydantic models, the routes
against the app's own routing table, and the Dart sources are scanned for the
patterns that would quietly turn 5A into something stateful.

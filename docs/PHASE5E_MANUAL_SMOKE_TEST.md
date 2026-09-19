# Phase 5E manual smoke-test record

Date: 2026-09-17

## Verified manually

The local authenticated Flutter Web smoke test was completed with synthetic
project data only.

- Flutter Web launched successfully with Flutter 3.47.2 / Dart 3.13.2.
- The API remained bound to `127.0.0.1:8000`.
- Chrome used `http://127.0.0.1:8000` as its API origin.
- Authenticated gateway health returned `status: ok`.
- The server-owned sandbox reported ready.
- A synthetic project was created successfully.
- The project was loaded by identifier.
- The project view displayed the project and remained read-only.
- Navigation from the project view to Revision Diff succeeded.
- The Rates navigation control was present.
- CORS preflight and authenticated project requests completed successfully.

The temporary database and token used for this smoke test were local-only and
were not added to the repository.

## Deferred

Android emulator testing is intentionally deferred. The local AVD was not
reliably available during the trial, and no further emulator troubleshooting is
required for Phase 5E acceptance.

Physical-tablet testing will be considered during the beta phase, after the
product is otherwise ready. That trial requires separate approval and must use:

- synthetic or explicitly authorized data only;
- memory-only authentication;
- the API still bound to `127.0.0.1`;
- no production hostname, credentials, or public tunnel in tracked source;
- a separately approved controlled transport mechanism if a physical tablet
  cannot reach the workstation locally.

This record does not claim that an Android APK or a physical tablet has been
manually exercised.

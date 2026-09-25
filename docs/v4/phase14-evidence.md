# Phase 14 final pre-cutover evidence

Status: local pre-cutover evidence complete; production remains disabled and
untouched.

## Review basis and verdict

The bounded review covers the Phase 11–14 execution, cutover, production-adapter,
recovery, test, runbook and isolated-smoke contract chain identified in
`work/phase14/evidence-manifest.json`. The basis is HEAD
`e979b64c5a51c0ec9e33ac9c135bd5d641f7846f` plus the listed Worktree files. The
staged delta was empty when the evidence was frozen.

The initial Standards and Spec review found one reachable P1: production-effect
authorization was validated only after adapter execution. A fake-only RED test
proved an adapter could be called and an execution attempt consumed before the
receipt was rejected. The correction now:

- checks the exact adapter-required capability and deterministic execution key
  before reservation;
- repeats the check immediately before dispatch; and
- makes the production-shaped adapter reject a mismatched key before lookup,
  journal creation, download or Sonarr access.

The review also found that restore evidence was self-referential at the physical
file-hash layer. Backup, verification and restore now require the same deterministic
SQLite logical-dump SHA-256. Canary preflight now requires one structurally valid
envelope and a production authorization bound to its execution key.

Final bounded review verdict: no open P0, P1, P2 or P3 Standards or Spec findings.
This is not a production-readiness or production-compatibility claim.

## Local verification

- 10/10 focused pre-cutover integration tests passed.
- 24/24 Phase 11–14 downstream/cutover tests passed.
- 176/176 V4 tests passed in 71.101 seconds; none skipped.
- Changed V4 modules passed Python bytecode compilation.
- Docker Compose rendered successfully with a fresh empty temporary Docker config,
  without contacting the engine.
- Default runtime/API/Compose/frontend registration scan found zero references that
  construct or inject the production adapter.
- A count-only secret-signature scan over the bounded Phase 14 scope found zero
  matches; no environment values or matched content were printed.
- Incomplete and corrupt journal evidence remained `unknown` and caused zero
  downloader or Sonarr calls in an isolated loopback test.
- Backup/restore evidence used only temporary SQLite paths and independently matched
  logical content.

The global `python -m pip check` is not usable as project dependency evidence on this
host: an unrelated installed `hf-gradio 0.3.0` package requires
`gradio-client>=2,<3`, while the host has `gradio-client 1.14.0`. AniDown declares
neither package. No package installation or environment remediation was attempted,
so global dependency consistency remains Not verified.

## Safety boundary retained

No production endpoint, secret, database, library path, process or container was
accessed. No download, Sonarr write, mapping mutation, autosave, V3 change, runtime
registration, Compose enablement, Git staging, commit, configuration, reset or
discard occurred. All effect-bearing tests used temporary paths, fake downloaders
and an in-process loopback Sonarr double.

## External gates still required

Production cutover must remain unauthorized until all of these are completed under
separate operational authority:

1. Run the contract against a disposable real Sonarr matching the target production
   version and isolated library permissions, including restart reconciliation.
2. Independently review the frozen implementation/evidence and the intended
   downloader, Sonarr and filesystem permissions.
3. Back up the actual V4 database and affected production library, verify the
   evidence, and complete a restore rehearsal without overwriting the live system.
4. Review and mount new production deployment inputs: explicit origin and paths,
   API-key file and separately injected operator-control secret. Probe only after
   that configuration is approved.
5. Stop and disable V3 or deploy the cooperating shared guard, then observe that no
   V3 worker or queued downstream command remains before and after ownership transfer.
6. Select one low-risk approved envelope, generate a fresh authorization bound to its
   exact execution key, and obtain explicit user authorization for that one production
   download/write and its monitoring window.

Until those gates are evidenced, the current production stack must remain exactly as
it is.

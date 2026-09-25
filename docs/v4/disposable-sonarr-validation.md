# Disposable Sonarr validation harness

Status: Phase 7.1 compatibility-corrected and locally verified against an in-process
loopback double. Phase 7
external validation additionally proved the real client/adapter probe, bounded read APIs,
wrong-key rejection and restart recovery against a disposable Sonarr 4.0.20.3014. The
marker-gated fixture move plus `RescanSeries` command canary below has not yet been run
against that real disposable instance with the corrected identity. The harness is not
registered in V4 runtime, API, UI or Compose.

## Safety contract

`work/phase15/validate_disposable_sonarr.py` is a dormant operator tool for a future,
separately authorized disposable-service exercise. It fails closed unless all of the
following are true:

- the exact confirmation is `VALIDATE DISPOSABLE SONARR`;
- the origin is explicit loopback HTTP with a port and no credentials, path, query,
  fragment or redirect;
- the validation root is a real directory containing the regular file
  `.anidown-v4-disposable` with the exact line
  `ANIDOWN V4 DISPOSABLE SONARR VALIDATION`;
- staging, library, journal and API-key paths are distinct, nonsymlink paths contained
  by that root; staging and library already exist and every effect root is empty;
- the bounded Sonarr status response reports both the expected exact version and the
  fixed instance name `Sonarr AniDown V4 Disposable Validation`.

The `Sonarr` prefix is part of the safety identity, not decoration. Sonarr 4.0.20.3014
rejects host-config instance names unless the first or last word is `Sonarr`; the
previous `AniDown V4 Disposable Validation` literal could not be configured on a real
instance and must not be used.

The remote instance-name check occurs before series lookup, journal creation, fixture
artifact creation, filesystem move or `RescanSeries`. This protects against pointing
the harness at a same-version current Sonarr that happens to listen on loopback.

## Bounded behavior

After the gates pass, the harness reads the contained API-key file, verifies one
explicit series points inside the isolated library root, creates one fixed fixture
artifact under isolated staging, moves it to that library and submits one
`RescanSeries` command. It then constructs a fresh adapter and verifies that the
completed receipt can be recovered from the durable journal without a second command.

The JSON result contains only bounded compatibility evidence: version, series ID,
execution key, artifact count, receipt-key equality, instance-identity verification,
isolated mode and fresh-adapter lookup status. It excludes the API key, origin and
filesystem paths. Configuration failures return only `invalid_validation_config`;
adapter failures return their sanitized category.

## Future operator input

The manifest is an exact JSON object with these fields:

```json
{
  "schema_version": 1,
  "purpose": "disposable-sonarr-validation",
  "sonarr_origin": "http://127.0.0.1:18989",
  "expected_version": "4.0.20.3014",
  "api_key_file": "C:/isolated-anidown-v4-test/sonarr-api-key",
  "validation_root": "C:/isolated-anidown-v4-test",
  "staging_root": "C:/isolated-anidown-v4-test/staging",
  "library_root": "C:/isolated-anidown-v4-test/library",
  "journal_root": "C:/isolated-anidown-v4-test/journal",
  "series_id": 1
}
```

Do not run the command merely because the harness exists. A future authorization must
identify the disposable service/version, isolated paths and series. The command is:

```powershell
python work/phase15/validate_disposable_sonarr.py `
  --manifest C:/isolated-anidown-v4-test/manifest.json `
  --confirmation "VALIDATE DISPOSABLE SONARR"
```

## Evidence limits

The local tests prove guard ordering, request shape, one isolated move/rescan and
fresh-adapter journal lookup. The Phase 7 external probe proves real Sonarr status,
wanted/queue reads, key rejection and post-restart readiness, but not real command
acceptance, series-path containment, fixture movement or durable execution recovery.
The fixture bytes are deliberately not a real download and do not prove media import,
provider download behavior, production permissions or production recovery. Those
claims remain unavailable until their specifically bounded validations are authorized.

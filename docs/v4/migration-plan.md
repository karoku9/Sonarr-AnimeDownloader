# AniDown V4 — Ambiente, migrazione e piano per fasi

Data della proposta iniziale: 2026-09-14. Le sezioni iniziali conservano la baseline e la roadmap della fase 0; lo stato operativo corrente della fase 7 è descritto in fondo al documento e in [frontend-api-contract.md](frontend-api-contract.md).

## Stato iniziale preservato

Repository V3: `C:\Users\Willi\Documents\Codex\2026-05-21\you-are-working-on-my-fork`.
Branch `main`, upstream `origin/main`, ahead/behind 0/0, HEAD `e979b64c5a51c0ec9e33ac9c135bd5d641f7846f`. Al controllo iniziale: 16 file tracciati modificati, nessuna modifica staged; file/cartelle non tracciati comprendono Compose, matcher, mapping, runtime, catalogo/ricerca V3, estensione Chrome e test. Non sono elementi trascurabili: costituiscono parte della V3 funzionante.

Worktree creato: `C:\Dev\AniDown-v4`, branch `v4`, a partire dal medesimo HEAD. Sono stati copiati 219 file tracciati/non tracciati dello stato di lavoro, verificandone SHA-256. Nessun reset/discard/stash, nessun commit o stage nella V3. File ignorati, `.env`, node_modules e log non copiati. I file di backup non tracciati presenti restano nello snapshot V4; non sono dipendenze del frontend nuovo.

Le modifiche locali ereditate restano visibili come dirty anche in V4; questa fase non crea un commit baseline e non pubblica su remoto. Prima del primo commit V4 effettuare controllo segreti e distinguere snapshot ereditato, infrastruttura e documenti. Non fare `git add .` alla cieca: alcuni file legacy di sviluppo/test contengono credenziali hardcoded da sanitizzare solo in V4 prima di qualunque pubblicazione. La V3 non viene corretta in questa fase.

Il worktree condivide repository Git e object store con V3, ma ha HEAD, indice e file di lavoro separati. Non è un backup indipendente: eliminare il repository originario romperebbe la relazione del worktree. Nessuna modifica globale safe.directory: i controlli usano `git -c safe.directory=...` per il singolo comando.

## Identità e isolamento Docker predisposti

| Risorsa | V3 osservata | V4 predisposta |
|---|---|---|
| Compose project | `you-are-working-on-my-fork` | `anidown-v4` |
| Service | `anidown_v3` | `anidown-v4` |
| Container | `anidown-v3` | `anidown-v4` |
| Image | `anidown-v3:latest` | `anidown-v4:latest` |
| Porta host | 5000 | 127.0.0.1:5004 |
| Data/download/script | bind `C:/anidown/...` | named volumes V4 distinti |
| Plex/library | bind B: | nessun mount della libreria reale |
| Restart | unless-stopped | no |
| Avvio | già attiva, healthy | non avviata; profile esplicito `v4-preview` |

Compose V4 ha top-level `name: anidown-v4`; invocare comunque `-p anidown-v4` per evitare override ambientali. Dockerfile aggiorna titolo OCI e VERSION a AniDown v4, senza riscrivere startup o dipendenze. Il Compose di test viene allineato alla stessa identità, porta e volumi isolati; non può convivere contemporaneamente con l'istanza preview perché usa intenzionalmente lo stesso container V4.

Nessun `.env` operativo V3 copiato; `.env.v4.example` contiene solo valori vuoti/default V4 e configurazione futura esplicitamente non ancora consumata. Non usare URL/key Sonarr produzione per avviare il bootstrap ereditato: `src/main.py` avvia ancora Core/downloader/frontend_OLD. Il profile è un attrito operativo, non un guardrail completo; i guardrail applicativi verranno implementati prima del primo avvio. Nessun mount dei dati V3 o Plex, ma una chiave Sonarr reale potrebbe comunque produrre comandi remoti: per questo l'avvio è rinviato alla fase 1.

Il gruppo `you-are-working-on-my-fork` oggi appartiene alla V3 in esecuzione. La V4 non crea risorse con quel nome. Eliminare/rinominare il gruppo V3 richiederebbe ricreare la V3: non eseguito per rispettare il vincolo di lasciarla esattamente com'è. Questo è il solo limite rispetto alla richiesta di assenza totale del nome in Docker Desktop.

Riferimenti: [nome Compose e precedenza](https://docs.docker.com/compose/how-tos/project-name/), [top-level name](https://docs.docker.com/reference/compose-file/version-and-name/).

## Comandi della fase 0 (storici)

```powershell
# Read-only: validazione configurazione, nessun avvio
docker compose -p anidown-v4 --profile v4-preview -f C:\Dev\AniDown-v4\docker-compose.yml config --quiet
docker compose -p anidown-v4 --profile v4-preview -f C:\Dev\AniDown-v4\tests\compose.yaml config --quiet

# Test V3 offline ereditati, da eseguire solo nel worktree V4
Set-Location C:\Dev\AniDown-v4
python -m unittest tests.test_search_v3 tests.test_mapping_management tests.test_runtime_mapping

# Inventario finale, senza mutazioni Docker
docker ps -a
docker compose ls
```

Questi comandi documentano la validazione iniziale e non avviano Docker. La preview applicativa corrente usa il runtime V4 separato descritto nella sezione Phase7. Docker V4 resta rinviato; non cambiare tag V3 e non utilizzare `down` contro il project V3.

## Migrazione dati proposta

1. Esportazione read-only del database reale V3 e configurazione, con manifest hash e backup fuori dalla V3; import su copia V4. Non basta copiare `src/database`: i dati di produzione sono i bind mount esterni. La fase 4 ha acquisito uno snapshot metadata sanitizzato in lettura; l'import definitivo dei dati live resta rinviato.
2. Import legacy table, preferences, manual aliases e drafts tramite staging SQLite versionato. Gli ID Sonarr vengono riconciliati da snapshot; omonimie e serie non trovate generano review, non merger per titolo normalizzato.
3. Conservare ordine delle URL, absolute e parti, flags/intent audio e manual overrides; costruire ranges e season crosswalk espliciti. Non inferire coverage corretto dal solo ordine delle URL.
4. URL canonicalizzate con policy per provider: dedup di una stessa release non equivale ad autorizzare condivisione cross-season. Shared URL solo con ranges/numbering documentati o conferma umana tracciata.
5. Review legacy conserva notes, candidati ed evento originale se disponibile. Se il motivo non è recuperabile usare `legacy_reason_unknown`; non inventare la causa. Importare anche review di stagioni complete. Gli eventi sono solo supporto alla ricostruzione, mai il DB autoritativo delle review future.
6. Cache alias V3 senza identità/scope viene marcata untrusted; niente migrazione automatica come alias affidabili. Nuovo catalogo/metadata enrichment in shadow mode.
7. Dry-run con conteggi input/output, collisioni, reason codes e diff mapping. Import idempotente via checksum e legacy record ID, transazione atomica, schema_version e foreign keys; backup prima di ogni migration.

SQLite è V4 soltanto. Non si converte in-place il JSON V3 e non si condivide alcun file SQLite tra processi V3/V4. Export compatibile V3 può essere previsto, ma il rollback immediato consiste nel fermare solo V4 e continuare con V3 immutata; nessun downgrade distruttivo di dati V4.

## Rischi e mitigazioni

| Rischio | Mitigazione / verifica |
|---|---|
| Perdere lavoro locale creando branch da HEAD | Copia dei file attuali, manifest SHA-256, confronto indice/status/head V3 dopo preparazione |
| Alias inquinati da opere/season diverse | Scoping/provenienza, quarantena cache legacy, fixture Black Lagoon |
| Date catalogo non sono prime release | date kind e precisione espliciti, no confronto tra scope diversi |
| Season Sonarr ≠ cour/provider | Crosswalk verificato, ranges, review quando incompleto |
| Downloader eredita side effect di startup | Nessun container avviato ora; bootstrap sandbox e write guard prima di runtime |
| Mapping scelto ma non approvato usato dal processor | Planner V4 legge solo mapping approved; contract test autosave off e ambiguous |
| Review perduta con rotazione log o Sonarr completo | Entità Review autonoma, test restart/rotation/completezza |
| Concurrent judge/azione utente | Hash snapshot, revision check e transazione; manual resolution prevale |
| LLM errato/costo/quota | Judge off e suggestion-only iniziale, budget/circuit breaker, validazione hard |
| Dipendenze larghe e build legacy | Lock/reproducibilità in slice dedicata; nessun upgrade generale ora |
| Credenziali legacy hardcoded | Sanitizzazione V4 prima di commit/push; segreti runtime fuori dal Git |
| UI ripete complessità V3 | Nuovo frontend, cinque aree, Advanced e gate delle tre skill richieste |

## Piano implementazione in fasi

Questo è il roadmap proposto nella fase iniziale. Le sezioni Phase5–7 in fondo documentano lo stato implementato corrente e le decisioni successive dell’utente; Gemini/judge, downloader e migrazione definitiva restano rinviati.

### Fase 0 — completata come predisposizione, proposta da revisionare

Worktree v4, snapshot codice corrente, Docker identity isolata e quattro documenti. Verifica: V3 head/status/index/file hash invariati; Compose valido; inventario Docker V3 healthy e nessuna V4 avviata. Le verifiche effettivamente eseguite e il risultato dei test sono riportati nel report della sessione.

### Fase 1 — runtime sandbox e baseline

Separare bootstrap API/worker dal download; default `search_only=true`, `downloads_enabled=false`, `sonarr_write_enabled=false`, `auto_save=false`, `judge=false` applicati nel backend. Adapter Sonarr di test/read-only; nessuna libreria produzione montata. Sanitizzare credenziali legacy solo in V4; snapshot commit privato e poi commit infrastruttura/spec separati. Verifica: avvio API non lancia Core/downloader e nessun POST Sonarr; build V4 e localhost smoke test. Frontend_OLD non è parte del bootstrap nuovo.

### Fase 2 — metadata, catalogo e fixtures

Modelli typed, title scope, Unicode normalization, provenance, lookup per ID e season crosswalk; riuso listing con dettaglio progressivo/cache. Fixtures offline Black Lagoon, Nadia, Sailor Moon Crystal e Ranma ½. Verifica: titoli giapponesi conservati, frazione idempotente, alias S2 non presenti in S1, dati non verificati restano unknown.

### Fase 3 — matcher deterministico in shadow mode

Hard constraints, tiers, evidence, audio tie-break e fuzzy finale; confronto V3/V4 su snapshot senza salvataggi. Verifica: tutti i casi del matching-spec, permutation invariance, zero scelta di hard reject, reason codes reali e mismatch dates/episodes. Report diff per serie/stagione prima di autosave.

### Fase 4 — persistenza, review e import

SQLite repositories/migrations, review lifecycle indipendente da missing, mapping approval e collisioni URL atomiche, API contract e importer dry-run. Verifica: 100% Sonarr conserva review, restart/log rotation, import idempotente, rollback su copia, concurrent resolve 409. Nessun download.

### Fase 5 — remote judge opzionale

Provider interface, adapter Gemini/Flash-Lite o equivalente, schema JSON, no tools/grounding, timeout/budget/cache. Inizialmente suggestion-only. Verifica: null/incerto/ID inventato/injection/quota/stale response, nessun superamento vincoli deterministici. Calibrazione prima di proporre autosave LLM.

### Fase 6 — frontend nuovo

Usare esplicitamente le tre skill/guideline indicate in ui-spec, scegliere direzione visiva e revisionare Library/Needs Review campione; costruire cinque aree su API V4. Rimuovere/escludere frontend_OLD nella V4 dopo sostituzione del bootstrap, senza toccare V3. Verifica: accessibilità/visual/e2e, review completa in Sonarr ancora visibile, assenza controlli tecnici fuori Advanced.

### Fase 7 — download controllato e adozione

Riuso adapter download dietro planner V4, ranges assoluti/parti e write guards. Test su Sonarr e libreria sandbox; abilitazione produzione soltanto dopo risultati concreti e revisione dell'impatto. Evitare due writer contemporanei sugli stessi episodi/library. V3 rimane disponibile fino all'adozione verificata; nessuna sua rimozione in questa richiesta.

## Criterio di arresto della fase 0 (storico)

La fase 0 si fermava prima della riscrittura principale. Le fasi successive hanno realizzato foundation, API e frontend V4 senza giudizio LLM, migrazione definitiva dei dati live o riconfigurazione della V3. Il confine operativo corrente è nella sezione Phase7 sotto.

## Verifiche effettivamente eseguite nella fase 0

- `python -m unittest tests.test_search_v3 tests.test_mapping_management tests.test_runtime_mapping`: **54 test, tutti passati**, nel worktree V4. Sono test offline ereditati V3; non provano un matcher V4 ancora inesistente.
- Entrambi i Compose validati con e senza profile; con `v4-preview` attivo verificati via JSON project/service/container/image V4, porta localhost 5004 e soli named volumes V4. Senza profile la configurazione non attiva servizi.
- HEAD, status Git, SHA-256 dell'indice e di tutti i 219 file della V3 confrontati con il manifest iniziale: **invariati**.
- File ereditati nel worktree V4 confrontati con lo snapshot: **identici**, eccetto i tre file infrastrutturali Docker intenzionalmente cambiati. Il nuovo example environment e i quattro documenti sono aggiunte.
- Le quattro copie `docs/v4/` nel worktree sono identiche ai documenti consegnati in outputs.
- Inventario Docker finale: `anidown-v3` ancora **running/healthy**, nessun container V4; project legacy V3 ancora visibile e preservato.
- Nessun commit, push, image build, container start o migrazione dati eseguito. La nuova regressione V4 è specificata e rimane lavoro della fase 2/3.

## Stato della foundation pura

Il commit baseline è stato esplicitamente rinviato dall’utente; nessuna configurazione user.name/user.email modificata. Lo staging preliminare è stato rimosso nella sola V4 su richiesta; indice senza modifiche staged e file conservati sul disco. La foundation deterministica resta pura/offline e non modifica Docker o downloader; lo strato applicativo e la preview separata sono ora descritti nelle sezioni Phase6/7 sotto. Vedere [foundation.md](foundation.md); le verifiche correnti sono conservate in progress.md e gli esiti di review nel report locale ignorato work/.


## Phase 5 implemented offline contract

See [release-resolver.md](release-resolver.md): N-release/N-season MappingPlan, observed episode coverage, deterministic crosswalk and replay. A Sonarr season is not a catalog release. Runtime orchestration is implemented in Phase6/7; production mapping migration remains deferred.


## Phase 6 application contract

See [frontend-api-contract.md](frontend-api-contract.md): standalone versioned JSON API, DTO-only frontend boundary and V4-local transactional composite review lifecycle. Proposed is distinct from approved. At Phase6 completion the API was unmounted and there was no frontend; Phase7 below supplies the local runtime/UI. Foundation remains unchanged, with no old-review import, LLM or legacy production autosave.


## Phase7 local preview boundary

At Phase7 completion the new frontend was served by src.v4.runtime on127.0.0.1:5004 with isolated work/phase7/application.sqlite3. Phase8 changes the runtime default and Docker-published port to6004, using Dockerfile.v4 and V4-only named-volume storage. Run scan reads configured sanitized V4 snapshots and creates V4 proposals/reviews only. No startup scan, V3 review import, legacy mapping save or source write. Test actions in Phase7 used a separate work/phase7/visual-check.sqlite3 copy of V4 data. Interrupted jobs are retained as failed, completed history is not deleted. No commit or shared Git configuration.

## Current Docker preview ingress

Target-host validation found that Docker did not make a published loopback port reachable
when the application container was attached only to an internal network. The current
preview keeps `anidown-v4` exclusively on `v4-preview-internal` with no `ports` entry and
adds `anidown-v4-loopback`, a same-image fixed-upstream TCP relay. Only the relay joins
the ordinary `v4-preview-ingress` bridge and publishes `127.0.0.1:6004`. The ingress
bridge is its explicit default gateway; the app therefore remains without an outbound
route. This topology must be revalidated on the disposable Docker host before any further
cutover work. It does not authorize production deployment, live Sonarr, real data,
downloads, autosync, or production credentials.

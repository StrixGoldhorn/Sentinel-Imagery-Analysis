# Sentinel Imagery Analysis — System Architecture Report

**Assessment date:** 9 September 2026  
**Scope:** Current repository and working-tree implementation  
**System type:** Single-host Python web/CLI application for Sentinel-1 SAR acquisition, vessel-candidate detection, satellite-pass scheduling, and AIS correlation

## Executive summary

Sentinel Imagery Analysis is a modular maritime-intelligence application that acquires Sentinel-1 synthetic-aperture radar (SAR) imagery, detects candidate vessels using classical computer vision, predicts future satellite passes, and gathers Automatic Identification System (AIS) positions for comparison. It provides a server-rendered Flask web interface, JSON APIs, command-line workflows, and an interactive desktop annotation tool.

The codebase follows Clean Architecture with meaningful separation between domain entities, application use cases and ports, infrastructure adapters, delivery interfaces, and the bootstrap composition root. Automated architecture tests enforce the inward dependency rule, and inspection found no direct imports from the application or domain layers into Flask, SQLite, OpenCV, or other outer-layer implementations. This is the system's strongest architectural quality: external providers and persistence mechanisms can be substituted without rewriting core orchestration.

Operationally, the system has evolved from a simple local prototype into a hardened modular monolith. Background tasks are persisted durably via SQLite (`DatabaseTaskQueue`), scheduler execution across multiple processes/workers is coordinated via distributed lease-based leader election (`SchedulerLeaderElector`), API endpoints are secured with API key authentication and sliding-window rate limiting (with trusted reverse-proxy support), storage lifecycle retention is enforced, and production containerization (`Dockerfile`, `docker-compose.yml`, Gunicorn WSGI) is established. The system remains a modular monolith suited for single-host or containerized operations, with horizontal scaling of compute workers and external object storage as potential future phases.

The end-to-end mission design is coherent: an analyst defines an area of interest (AOI); the system combines N2YO orbital predictions with historical Copernicus acquisition cycles; scheduled monitors collect AIS near a flypast; post-pass jobs poll the Copernicus catalogue; matching imagery is downloaded and tiled, stitched, optionally masked with elevation data, and processed by a deterministic OpenCV detector. The result is useful candidate-vessel intelligence, but it should not be treated as confirmed classification or identity correlation. The detector confidence is a brightness-derived heuristic rather than a calibrated probability, and no explicit SAR-to-AIS association engine is present.

The most important operational hardening milestones have been achieved: durable database-backed task queues, scheduler leadership election, API key authentication and rate limiting, storage retention policies, pinned dependencies, and production Docker containerization. Future priorities include CSRF protection for browser forms, fine-grained role-based access control, centralized structured logging, and extracting background workers to a distributed broker if workload demands exceed single-host capacity.

### Overall assessment

| Quality attribute | Assessment | Rationale |
|---|---|---|
| Modularity | Strong | Ports, protocols, use cases, and adapters are clearly separated; dependency direction is tested. |
| Functional cohesion | Strong | Workflows map cleanly to mission concepts: scans, AOIs, passes, AIS, detection, and post-pass ingestion. |
| Maintainability | Moderate–strong | Good boundaries and broad tests, offset by several large modules, compatibility branches, and duplicated fallback logic. |
| Reliability | Strong | Atomic file writes, transactions, retries, cooldowns, shutdown coordination, durable task queue (SQLite-backed), and scheduler leadership election exist. |
| Security | Moderate | Input/path validation, response headers, API key authentication (`X-API-Key` and `Bearer`), sliding-window rate limiting, and trusted-proxy validation exist; CSRF protection and an encrypted secret store remain open improvements. |
| Scalability | Moderate (Single-host) | Multi-worker WSGI supported via scheduler leader election and durable task persistence; scaling beyond a single host would require shared storage and a distributed task broker. |
| Observability | Moderate | Scraper execution logs, scheduler status, task heartbeats, and health/readiness probes (`/healthz`, `/readyz`, `/livez`) exist. |
| Testability | Strong | Over 260 tests with Clean Architecture boundary verification; CI covers Python 3.11, 3.12, and 3.13 with pinned dependencies. |

## Detailed analysis

### 1. System purpose and boundaries

The system covers six related capabilities:

1. Define and manage geographic AOIs as validated bounding boxes.
2. Search Copernicus Data Space for Sentinel-1 acquisitions, download SAR/DEM tiles, stitch them, and retain scan metadata.
3. Detect bright connected regions in SAR imagery and calculate axis-aligned and oriented vessel measurements.
4. Predict satellite passes by combining N2YO orbital tracking with historical Sentinel-1 acquisition patterns.
5. Ingest and retain vessel identity and position data from pluggable AIS sources.
6. Automate AIS monitoring around a pass and SAR ingestion after a pass.

The system does not currently provide calibrated vessel classification, confirmed SAR-to-AIS identity matching, multi-tenant access control, distributed processing, or a production operations plane.

### 2. Architectural style

The implementation is a Clean Architecture modular monolith.

```mermaid
flowchart LR
    Browser[Browser UI] --> Web[Flask web interface]
    CLI[Command line] --> CLIAdapter[CLI interface]
    Desktop[Annotation UI] --> DesktopAdapter[Desktop interface]

    Web --> UC[Application use cases]
    CLIAdapter --> UC
    DesktopAdapter --> UC
    UC --> Domain[Domain entities and validation]
    UC --> Ports[Application-owned ports]

    Bootstrap[ApplicationContainer] --> Web
    Bootstrap --> UC
    Bootstrap --> Infra[Infrastructure adapters]
    Infra -. implements .-> Ports

    Infra --> SQLite[(SQLite)]
    Infra --> Files[(Scan files and tile cache)]
    Infra --> Copernicus[Copernicus APIs]
    Infra --> N2YO[N2YO API]
    Infra --> AIS[AIS web/UDP providers]
    Infra --> Nominatim[Nominatim]
```

The package roles are:

| Layer | Location | Responsibility |
|---|---|---|
| Domain | `sentinel_analysis/domain` | Immutable entities, coordinate/time normalization, validation errors, satellite definitions. |
| Application | `sentinel_analysis/application` | Use cases, provider/repository protocols, result types, application errors, cooperative shutdown state. |
| Infrastructure | `sentinel_analysis/infrastructure` | SQLite/filesystem repositories, Copernicus/N2YO/geocoder clients, AIS plugins, image processing, scheduling, and task execution. |
| Interfaces | `sentinel_analysis/interfaces` | Flask routes and serialization, CLI commands, desktop annotation UI. |
| Bootstrap | `sentinel_analysis/bootstrap` | Environment configuration and dependency composition. |

Dependency inversion is real rather than cosmetic. Application use cases depend on structural `Protocol` ports such as `ImageryProvider`, `ScanRepository`, `AISRepository`, `PassPredictor`, `TaskQueue`, and `SettingsRepository`. `ApplicationContainer` is the composition root that selects concrete adapters.

### 3. Runtime topology

The default web deployment is one Python process launched through `app.py`:

```mermaid
flowchart TB
    subgraph Host[Single application host]
        Flask[Flask development server]
        Container[ApplicationContainer]
        Queue[ThreadPoolExecutor\n4 workers]
        Scheduler[APScheduler background scheduler\nor timer-loop fallback]
        Monitors[Per-pass daemon threads/timers]
        SQLite[(data.db)]
        Output[(static/output scan workspaces)]
        Cache[(.cache hashed PNG tiles)]

        Flask --> Container
        Container --> Queue
        Container --> Scheduler
        Scheduler --> Monitors
        Container --> SQLite
        Container --> Output
        Container --> Cache
    end

    Container --> External[Copernicus, N2YO, Nominatim, AIS providers]
```

Important runtime consequences:

- Schedulers across multiple processes or workers coordinate through `SchedulerLeaderElector` leasing in SQLite/file locks, ensuring only a single active leader executes pass monitoring and scheduled jobs.
- Asynchronous background tasks are persisted in the SQLite `background_tasks` table via `ThreadedTaskQueue` (`DatabaseTaskQueue`), providing durable state, heartbeat tracking, progress persistence, and restart survivability.
- APScheduler jobs use `max_instances=1` within a process, reinforced by database lease heartbeats across multi-process deployments.
- SQLite uses short-lived connections, foreign keys, a five-second busy timeout, and transactions.
- Scan imagery and metadata live in per-scan filesystem directories, while relational mission state lives in SQLite. Configurable storage retention policies (`ExecuteStorageRetention`) prune expired scans and cache tiles based on age and quota limits.

This topology provides a resilient single-host deployment model that safely supports multi-worker WSGI processes.

### 4. Component analysis

#### 4.1 Domain model

The central immutable entities include `BoundingBox`, `Acquisition`, `ImageTile`, `Scan`, `AreaOfInterest`, `ShipDetection`, `BackgroundTask`, `Vessel`, `VesselPosition`, `AISRecord`, and `PostPassIngestionJob`.

Notable strengths:

- Geographic coordinates are finite, range-checked, and ordered.
- Timestamps are normalized to UTC at the domain boundary.
- Vessel navigation values, task progress, detection dimensions, and post-pass state values are validated.
- Frozen dataclasses reduce accidental mutation across threads and layers.
- `BoundingBox.split_into_zones()` supports providers with viewport or response-size limits.

Limitations:

- The post-pass state machine is represented as validated strings rather than a dedicated enum/state-transition model.
- Detection confidence is stored in the same shape as a probabilistic score, although its implementation is heuristic.
- There is no first-class entity for association between a SAR detection and an AIS vessel track.

#### 4.2 Application services

Use cases are generally small orchestration units. Representative flows include `CreateScan`, `DetectShips`, `IngestAIS`, `PredictAreaOfInterest`, `CheckAndScheduleAOIs`, `IngestPostPassImagery`, and scraper/settings management.

The design supports isolated testing with fake adapters. However, a growing amount of compatibility code uses `hasattr()` and catches `TypeError` to accommodate adapters with older signatures. That preserves backwards compatibility but weakens the otherwise explicit port contracts. Port evolution should be made deliberate through versioned interfaces or coordinated adapter updates.

Several broad exception catches intentionally preserve partial mission progress, such as allowing DEM or vessel detection to fail without invalidating a downloaded SAR scan. This is a reasonable policy, but failures are sometimes suppressed without a durable warning record. Mission results should explicitly capture partial-success states.

#### 4.3 Web and CLI interfaces

The Flask application registers blueprints for scans, AOIs, AIS, schedules/post-pass jobs, scrapers/logs, background tasks, and settings. Server-rendered templates are enhanced by substantial browser-side JavaScript for maps, layers, inspection, notifications, scheduling, and AIS timelines.

Positive controls include:

- Strict bounding-box/domain validation.
- JSON object and integer parsing helpers.
- Scan-folder validation in both the web boundary and repository.
- Stable translation of expected errors to HTTP 400, 404, and 502 responses.
- Generic 500 responses for unexpected exceptions.
- A one-megabyte Flask request limit.
- `nosniff`, `SAMEORIGIN`, and referrer-policy headers; JSON responses are marked `no-store`.
- Escaping helpers for dynamically generated browser markup, covered by tests.

The CLI exposes `detect`, `download`, `predict`, `ingest`, and `annotate` commands and maps expected application errors to stable process exit codes. The web and CLI both construct or consume the same application use cases, which is a good example of interface independence.

#### 4.4 External-service adapters

The principal external dependencies are:

| Dependency | Use | Resilience behavior |
|---|---|---|
| Copernicus identity endpoint | OAuth access token | Token cache, safety-adjusted expiry, forced refresh after authorization failure. |
| Copernicus STAC Catalogue | Latest/historical acquisition search | Request validation, timeouts, transient retry behavior. |
| Sentinel Hub Process API | SAR and DEM tile retrieval | Long download timeout, token refresh, transient retries, filesystem cache. |
| N2YO | Radio-pass prediction | 30-second timeout and response-shape translation. |
| Copernicus catalogue history | Mission repeat-cycle analysis | Converted into historical and extrapolated pass evidence. |
| Nominatim | Reverse geocoding | Best-effort label resolution; scan creation can retain a fallback. |
| AIS Friends | Community AIS data | Plugin isolation, multi-zone collection, configurable pacing. |
| VesselFinder / APRS.fi | Browser-assisted web AIS collection | Playwright sessions, stealth support, cooldown after blocking/rate limits. |
| UDP NMEA input | Local AIS receiver | Daemon listener and buffered decoding via `pyais`. |

AIS plugins are isolated at execution time: failure in one provider is logged and does not stop the next provider. Automated ingestion honors enabled/disabled state and escalating cooldowns. The default registry also enables mock data providers, which is convenient for demos but can mix synthetic and live data if an operator does not understand the defaults.

#### 4.5 Image-processing pipeline

The scan pipeline performs the following sequence:

1. Search for an acquisition in a default or requested time window.
2. Calculate a geographic tile grid.
3. Download SAR tiles, using hashed filesystem cache entries where possible.
4. Stitch the complete tile grid to a temporary output and atomically replace the final image.
5. Best-effort download and stitch DEM tiles.
6. Reverse-geocode the AOI centre and write `metadata.json` atomically.
7. Delete intermediate workspace tiles.

The detector loads SAR imagery in grayscale, optionally applies a DEM-derived land/coastal mask, optionally denoises using Lee or Frost filtering, thresholds bright pixels, dilates fragmented returns, extracts contours, rejects contours outside configured area bounds, and calculates oriented bounding boxes. Length and beam are derived using configured pixel spacing; heading comes from the OpenCV minimum-area rectangle.

The implementation is deterministic and interpretable, but the reported confidence is calculated from mean contour intensity relative to the threshold. It is not empirically calibrated. False positives from sea state, structures, sidelobes, and coastal clutter remain expected even with DEM masking.

### 5. Principal workflows

#### 5.1 Manual or asynchronous scan

```mermaid
sequenceDiagram
    actor Analyst
    participant API as Flask API
    participant Queue as Task queue
    participant Scan as CreateScan
    participant COP as Copernicus
    participant FS as Filesystem repository

    Analyst->>API: POST /scan or /api/tasks/scan
    alt asynchronous request
        API->>Queue: submit scan closure
        API-->>Analyst: 202 + task ID
        Queue->>Scan: execute
    else synchronous request
        API->>Scan: execute
    end
    Scan->>COP: search catalogue
    Scan->>COP: download SAR and DEM tiles
    Scan->>FS: stitch image and save metadata
    Scan-->>API: Scan
    API-->>Analyst: scan metadata and media URL
```

The scan workspace is rolled back if preparation succeeds but a later mandatory step fails. DEM failure is explicitly non-fatal. The asynchronous path has no progress callbacks despite the task model supporting progress.

#### 5.2 Pass prediction and scheduling

For each auto-capture AOI, the hybrid predictor retrieves N2YO and historical mission predictions in parallel, filters to enabled satellites, merges matching events within a 15-minute window, and assigns source/confidence metadata. Historical extrapolation receives more weight than N2YO tracking. N2YO-only predictions can support AIS monitoring but are intentionally excluded from automatic post-pass SAR ingestion because they do not prove an imaging acquisition.

The scheduler performs frequent AOI checks (default 30 seconds) and separate post-pass catalogue scans (default one hour). A pass monitor uses timers and daemon threads to collect AIS roughly once per minute within a ±5-minute flypast window. Eligible historical or cross-validated passes create persistent post-pass ingestion jobs.

#### 5.3 Post-pass SAR ingestion

Persistent jobs move through `PENDING_PASS`, `POLLING_CATALOG`, `INGESTING`, and terminal `COMPLETED`, `TIMED_OUT`, or `FAILED` states. Catalogue polling uses progressive delays of 2, 3, 5, then 10 minutes at the use-case level, although the enclosing scheduler normally checks due work hourly by default. Matching imagery must fall within ±1 hour of the expected acquisition. A newer acquisition beyond that window is treated as evidence the expected pass was missed.

A responsibility overlap currently exists: `SQLitePostPassIngestionRepository.get_jobs_due_for_poll()` automatically changes jobs older than 24 hours to `TIMED_OUT` before returning due jobs, while `IngestPostPassImagery.execute()` also contains timeout transition and result-generation logic. The repository can therefore hide an expired job from the use case. This is the cause of the reproducible failing timeout test (`expected one TIMED_OUT result, received none`). Timeout ownership should be placed in one layer.

#### 5.4 AIS ingestion

AIS ingestion selects one or all registered plugins, injects stored plugin configuration, skips disabled or cooling-down plugins for automated runs, authenticates and fetches records, normalizes them into domain entities, persists vessel and position data, and updates execution logs. Failures are isolated per plugin. Cooldown duration rises from 15 minutes to one hour and then four hours after repeated network, rate-limit, or anti-bot failures.

### 6. Data architecture

#### 6.1 SQLite

Versioned SQL migrations create the following persistent concerns:

- AOIs and auto-capture state.
- Vessels and timestamped vessel positions.
- Scraper execution logs, configuration, cooldown, tags, and trigger reasons.
- Background-task schema backing durable execution in `ThreadedTaskQueue` with state persistence, progress updates, and task resumption.
- Cached AOI prediction/mission-analysis payloads with expiry.
- Post-pass ingestion jobs and expected imagery times.
- General system settings stored as JSON values.

Repositories open short-lived connections and use parameterized SQL. Transactions commit or roll back at the connection boundary, and foreign-key enforcement is enabled on every connection. The schema has useful indexes for vessel history, forecasts, and due post-pass work.

Concerns:

- Migrations run independently from the constructors of four repositories, causing repeated startup checks and making schema ownership diffuse.
- Migration filenames have two separate `005_...` entries; ordering is deterministic by full filename but the numeric prefix is not a unique version.
- The existing `ARCHITECTURE.md` lists obsolete Python migration names and does not reflect the active SQL migration set.
- Storage retention policies (`ExecuteStorageRetention`) manage scan and cache lifecycles via configurable `scan_retention_days` and `cache_retention_days`; archival and retention for high-volume historical AIS vessel positions and scraper logs can be extended further.
- N2YO's API key can be stored as JSON text in SQLite. It is masked in settings responses but not encrypted at rest.

#### 6.2 Filesystem

Each scan is a directory containing an `images` subdirectory and `metadata.json`. The repository validates scan names and ensures image paths remain within the intended workspace. Metadata and cache entries use temporary files followed by atomic replacement. Scan deletion recursively removes the validated scan directory.

Because imagery and SQLite state are separate stores, a crash can leave an orphaned scan or a job referring to missing files. A reconciliation command or startup audit would improve recoverability.

### 7. Security and trust model

Existing safeguards include:
- Input type/range checks, filename/path containment, and parameterized SQL queries.
- API key authentication protecting `/api/*` routes via `X-API-Key` or `Authorization: Bearer <token>` headers (rejecting query-parameter tokens to prevent leakage in server access logs and browser history).
- Sliding-window rate limiting (`SlidingWindowRateLimiter`) with configurable requests-per-minute limits.
- Trusted reverse-proxy IP validation (`get_client_ip`), ensuring `X-Forwarded-For` is only honored from explicitly configured proxy addresses (`trusted_proxies`) to prevent rate-limit spoofing.
- Secret masking in the settings API and keeping Copernicus credentials out of SQLite.
- Standard security headers and generic unexpected-error responses.

Open security considerations and remaining gaps:

1. **Authorization**: API authentication is uniform across endpoints based on the configured key; fine-grained role-based authorization (e.g., distinguishing read-only analysts from administrators) remains an open enhancement.
2. **CSRF Protection**: State-changing browser requests in the web UI do not yet use anti-CSRF tokens.
3. **Transport Security & CSP**: HTTPS termination and Content Security Policy should be managed by the fronting reverse proxy or ingress controller.
4. **Credential Encryption**: N2YO credentials stored through the settings UI are masked in responses but stored as plaintext JSON in SQLite.
5. **Scraping Adapter Risks**: Third-party scraping relies on browser-stealth mechanisms; source terms of service and availability should be reviewed independently.
6. **Synthetic AIS Isolation**: Synthetic AIS providers are enabled by default for local testing and should be visually and operationally flagged when operating against live feeds.

### 8. Reliability, concurrency, and performance

Strengths include atomic scan metadata/image output, transactional SQLite writes, Copernicus retry/token-refresh behavior, per-plugin AIS failure isolation, scraper cooldown, APScheduler job coalescing, `max_instances=1`, cooperative shutdown checks, and explicit cleanup of scheduler, task pool, pass monitors, and UDP listeners.

Key risks:

- Scheduler coordination across multi-worker deployments is handled via distributed lease election (`SchedulerLeaderElector`), though standby workers must await lease expiration before assuming leadership upon leader failure.
- Background tasks are persisted in SQLite via `DatabaseTaskQueue` / `ThreadedTaskQueue`, ensuring task progress, metadata, and results survive restarts, though long-running active worker threads still require graceful shutdown.
- Thread workers can perform long external HTTP downloads of up to 300 seconds per request; shutdown cancels queued futures but cannot forcibly stop running calls.
- SQLite's default journal mode and five-second busy timeout may surface lock errors under simultaneous ingestion, scheduling, and web writes.
- The Frost filter uses nested Python loops over every pixel and will scale poorly on large stitched scenes.
- Full images are read into memory for OpenCV analysis and crop extraction; there is no explicit upper bound on stored image dimensions.
- The pass scheduler's hourly due-job scan can dominate the use case's intended 2–10-minute progressive polling unless operators lower the interval.
- Error suppression in fallback paths can turn actionable dependency failures into absent or stale data without a unified alert.
- The shutdown watchdog uses `os._exit(0)` after its deadline, which guarantees termination but can bypass flush/finalizers and reports a success exit code.

### 9. Configuration and deployment

Environment configuration defines project, database, output, cache, Copernicus credentials, N2YO key, debug flag, and port. Feature settings are then seeded into SQLite and can be edited through the web UI. Some settings are live-read by adapters, and scheduler intervals are explicitly rescheduled after updates.

There are two configuration planes with partial overlap. Runtime-critical settings such as port, filesystem roots, debug mode, and the scheduler's initially injected API key are fixed when the process starts, even though similarly named values appear in the settings database. The UI can therefore imply that a change is active when a restart or explicit runtime wiring is required. Settings should be classified as either bootstrap-only or live-reloadable and presented accordingly.

Production deployment is container-ready via `Dockerfile` (based on `python:3.13-slim`), `docker-compose.yml`, production WSGI configuration (`wsgi.py`, `gunicorn.conf.py`), and operational health probes (`/healthz`, `/readyz`, `/livez`). All dependencies in `requirements.txt` are pinned to exact verified versions. CI runs unit, integration, and Clean Architecture boundary verification tests across Python 3.11, 3.12, and 3.13 on Ubuntu.

### 10. Maintainability and test posture

The repository currently contains approximately:

- 112 application Python files / 11,295 lines.
- 25 Python test files / 5,872 lines.
- 17 JavaScript files / 6,675 lines.
- 10 HTML templates / 2,509 lines.
- 261 discovered test functions/methods.

Architecture tests explicitly check dependency direction and the absence of removed legacy imports. Tests also cover domain invariants, provider contracts, retries, migrations, caching, image processing, web input/error contracts, task execution, scheduler behavior, pass monitoring, post-pass ingestion, settings, and scraper management.

The current local full-suite run is not green. Many errors are attributable to permission failures under the configured test temporary directory in this managed Windows environment. Independently, the isolated post-pass timeout test fails reproducibly because expired jobs are transitioned and filtered in the repository before the use case can report them. The working tree also contains pre-existing uncommitted changes, so this assessment describes the current snapshot rather than asserting the main branch has the same result.

Large modules worth splitting by responsibility include `sqlite_ais.py`, `copernicus.py`, `manage_scrapers.py`, `sqlite_settings.py`, `ingest_post_pass_imagery.py`, and the scheduler/monitor modules. Splitting should preserve the current ports and be driven by cohesive responsibilities rather than line count alone.

### 11. Key architectural findings

| Priority | Finding | Impact | Recommendation |
|---|---|---|---|
| Medium | APIs use API key authentication and sliding rate limits; fine-grained RBAC and CSRF remain open. | Single shared key controls all API access; web forms lack CSRF tokens. | Add role-based authorization scopes and CSRF tokens for state-changing browser routes. |
| Resolved | Scheduler leader election implemented via `SchedulerLeaderElector`. | Multi-worker deployments no longer duplicate background pass monitoring or ingestion. | Database/file lease election coordinates single-active scheduler leader with heartbeat renewals. |
| Resolved | Durable task queue implemented via `DatabaseTaskQueue`. | Tasks and status survive restarts and are visible across processes. | SQLite `background_tasks` table persists task state, progress, results, and heartbeats. |
| High | Post-pass timeout transitions have two owners. | Expired jobs can disappear from use-case results; a focused test currently fails. | Move timeout state transition and result construction entirely into the use case or return transitioned jobs from the repository. |
| Resolved | Production deployment and pinned dependencies established. | Containerized deployment and reproducible builds across environments. | Provided Dockerfile, docker-compose.yml, Gunicorn WSGI configuration, pinned `requirements.txt`, and Python 3.11–3.13 CI coverage. |
| Medium | Bootstrap and live settings overlap without consistent activation semantics. | Operators may believe persisted changes are active when components still use startup values. | Classify settings as bootstrap-only/live and provide explicit reload/restart status. |
| Medium | SQLite and filesystem form a non-transactional aggregate. | Crashes can create orphaned state or broken job-to-scan references. | Add idempotency keys, reconciliation, and periodic integrity checks. |
| Medium | Detection score looks probabilistic but is heuristic. | Analysts may over-trust candidate confidence and dimensions. | Rename/label the score, calibrate against a labelled validation set, and report uncertainty. |
| Medium | Broad exception suppression reduces diagnosability. | Partial failure can appear as missing data rather than a clear degraded state. | Emit structured warnings and persist mission partial-success/failure reasons. |
| Resolved | Data lifecycle and storage retention policies implemented. | Scans and cached tiles are prevented from unbounded disk consumption. | `ExecuteStorageRetention` enforces configurable `scan_retention_days`, `cache_retention_days`, and quota limits. |
| Low | Existing architecture documentation is stale. | Onboarding and operations decisions may use incorrect workflows/schema information. | Replace or link it to this report and generate migration/component inventories from code where practical. |

### 12. Recommended target evolution

#### Phase 1 — Stabilize the local modular monolith (Completed / In Progress)

- [x] Implement durable background task persistence in SQLite (`DatabaseTaskQueue`).
- [x] Implement scheduler leader election (`SchedulerLeaderElector`) to coordinate multi-worker pass monitoring.
- [x] Implement API key authentication (`X-API-Key`, `Authorization: Bearer`), sliding-window rate limiting, and trusted-proxy validation.
- [x] Implement configurable storage retention policies (`ExecuteStorageRetention`) and disk quota enforcement.
- [x] Pin all dependencies in `requirements.txt` and expand CI matrix across Python 3.11, 3.12, and 3.13.
- [x] Provide production containerization (`Dockerfile`, `docker-compose.yml`, `wsgi.py`, `gunicorn.conf.py`) and health probes (`/healthz`, `/readyz`, `/livez`).
- [ ] Move migration execution to one explicit bootstrap step and adopt unique migration versions.
- [ ] Fix the post-pass timeout ownership regression.
- [ ] Add structured logs with correlation identifiers for scans, AOIs, jobs, and scraper runs.

#### Phase 2 — Harden for a shared service

- Add fine-grained authorization roles (RBAC) and CSRF protection for browser forms.
- Terminate TLS via fronting reverse proxy (Nginx, Caddy, or cloud load balancer) with strict CSP headers.
- Enable and validate SQLite WAL/backup behavior, or move relational state to PostgreSQL if write concurrency demands it.
- Add Prometheus-compatible metrics for queue depth, external latency, job age, and ingestion outcomes.

#### Phase 3 — Scale only if workload requires it

- Extract long-running scan and AIS work into durable workers backed by an external message broker (e.g. Celery / Redis).
- Store large imagery in managed/shared object storage (S3 / MinIO) while retaining metadata in the relational database.
- Introduce distributed job locks, idempotency keys, retry/dead-letter policy, and trace propagation.
- Consider a dedicated SAR/AIS association service only after a validated correlation model and throughput requirement exist.

The current port/use-case boundaries already provide useful seams for this evolution. A broad microservice rewrite is not required to achieve the remaining phases.

### 13. Final assessment

Sentinel Imagery Analysis has a solid internal architecture adhering to Clean Architecture principles. Its clean boundaries, rich domain validation, adapter isolation, atomic filesystem operations, and extensive automated tests create a credible base for further development.

Recent operational hardening has brought the deployment and runtime architecture up to standard: durable task state, scheduler leadership election, API key authentication, request throttling, trusted-proxy validation, storage retention, pinned dependencies, and production WSGI/Docker containerization now protect and stabilize the modular monolith. Future evolution can focus on fine-grained access control, browser CSRF protection, and distributed worker extraction if scale requires it.

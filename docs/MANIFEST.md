# MAYHOME CENTER — Implementation Manifest
**Version:** MHC-RC1 / Manifest 1.0  
**Date:** 2026-10-16  
**Target:** `mayhome` (Ubuntu host)  
**Public URL target:** `https://mayhome.cuong.win` (not provisioned by this manifest)  
**First priority module:** Nextcloud Backup Manager  
**Implementation owner:** Codex  
**Status:** Proposed specification; not deployed; no production modifications authorized until acceptance gates.

---

## 0. Codex execution contract — READ FIRST

Build a **separate web application** named **MAYHOME CENTER (MHC)** on the Linux host `mayhome`. Scope RC1 to a high-quality, responsive, **strictly read-only** Nextcloud Backup Manager plus a lightweight host overview. The architecture MUST support future Storage, Docker, Nextcloud, Services and Alerts modules without implementing their management actions now.

**Never** alter the installed Nextcloud Backup Engine RC1.3, recovery watchdog, retention rules, systemd backup units/timers, Docker Compose, Nextcloud database, backup snapshots, disk mounts, or Mount Guard in RC1. No `docker stop/start`, `systemctl start/stop/enable/disable` for existing services, `occ maintenance:*`, deletion, restore, update, chmod/chown on production backup paths, or root shell execution from the web app. Any future control operations require a separately approved RC2 manifest and explicit least-privilege authorization.

Implement incrementally with **stage gates**: code + tests + runtime + security + operations + acceptance evidence. Keep patches reviewable. Do not merge/deploy until previous gate passes. No fabricated test results or runtime status. First discover actual `mayhome` permissions, systemd and tunnel state. Mark any unverified assumptions **UNVERIFIED**; do not auto-correct production.

### Fixed preservation contract

| Resource | Existing baseline / requirement |
|---|---|
| Production app | Docker `nextcloud-app` — Nextcloud 35.0.1.1 at last observation |
| Cron | Docker `nextcloud-cron` |
| DB | Docker `nextcloud-db` — MariaDB |
| Source data mount | `/mnt/nextcloud_storage`, UUID `aa274c8e-844a-4482-aac2-9313ad6f0108` |
| Backup destination | `/mnt/nextcloud_backup`, UUID `5740eea6-8bf1-4f3e-ac9d-2c66bda6147c` |
| Backup root | `/mnt/nextcloud_backup/nextcloud` |
| Baseline | `database/nextcloud-20261015-174303.sql.gz`, backed-up `data/`, `config/`, `manifests/` |
| Protected checkpoint | `snapshots/20261015-210345` |
| Backup timer | `nextcloud-auto-backup.timer` — `OnCalendar=*-*-* 02:00:00`, `AccuracySec=5min`, `Persistent=false` |
| Watchdog timer | `nextcloud-backup-watchdog.timer` — `OnBootSec=2min`, `OnUnitInactiveSec=2min` |
| Retention | `/usr/local/sbin/nextcloud-backup-retention`; default DRY-RUN; 14-day threshold; baseline, fixed checkpoint and latest two completed protected |
| Recovery marker | `/var/lib/nextcloud-auto-backup/maintenance-owned` |
| Backup lock | `/run/lock/nextcloud-auto-backup.lock` |
| Mount Guard | `nextcloud-mount-guard.timer` must remain enabled/active per existing policy |
| Previously verified runtime | RC1.2 actual backup ~65.6 seconds, snapshot 187G logical, database gzip/SHA256 PASS, Nextcloud returned maintenance OFF. Do **not** treat as a current value. |

**Important:** The installed RC1.3 backup/watchdog timers were enabled and `systemctl list-timers` showed the backup planned for 2026-10-17 at 02:00. Verify live state at runtime. The RC1.3 candidate package also defined a retention audit timer for 04:00, but whether it is enabled on host is **UNVERIFIED**. Its presence is not permission to activate it. The first automatic 02:00 run and restore-from-snapshot have not been accepted in the supplied evidence.

---

## 1. Product vision and boundaries

**MAYHOME CENTER** is a single extensible administration portal for the `mayhome` home server. RC1 is **Nextcloud Backup Manager Monitoring**, not backup engine replacement. A separate PiCC may exist for the Raspberry Pi; do not merge repositories, share root credentials or operate PiCC resources from MHC.

**Primary goals**
1. See last backup, latest verified checkpoint, next schedule, current progress and errors at a glance.
2. Understand safety: source/backup HDD status, Mount Guard, cron, maintenance, recovery marker, retention candidates.
3. Examine history, manifests and limited integrity results without reading user files or changing backup state.
4. Work well on phone and desktop; accessible through Cloudflare Access and a Cloudflare Tunnel.
5. Be modular, testable and safe when Nextcloud, Docker, HDD or journal is unavailable.

**Non-goals in RC1:** run backup, restart container/service, restore files/DB, enable/disable timers, retention apply, delete snapshots, alter backup configuration, mount disks, modify Docker Compose, inspect or stream user file contents, expose host shell, set up notifications through an external provider, auto-deploy tunnel or DNS.

**Future modules (interfaces only):** Host Overview, Storage Health, Nextcloud Operations, Docker Monitoring, systemd Services, Alerts, Host Updates. Make no incomplete clickable controls for these.

---

## 2. Architecture and process isolation

```text
Browser (desktop/mobile)
     │ HTTPS / Cloudflare Access policy
     ▼
Cloudflare Tunnel: mayhome.cuong.win (only after separate configuration)
     ▼
127.0.0.1:5010 — MHC web (unprivileged Linux account)
     ├── UI (server-rendered or lightweight frontend)
     ├── Auth boundary / headers / session protection
     ├── API v1
     ├── BackupStatusService
     ├── SnapshotInventoryService
     ├── IntegrityMetadataService
     ├── JournalService
     └── HostReadAdapter (allowlist only)
              ├── `systemctl show` / `list-timers` (read-only)
              ├── `journalctl` (bounded read-only)
              ├── `docker inspect` (selected fields only) or privilege-separated status exporter
              ├── `stat` / `statvfs` / `findmnt` (read-only)
              ├── snapshot filenames / COMPLETE / manifest / checksum metadata
              └── optional signed/root-owned **status JSON exporter** (preferred for restricted paths)

Existing RC1.3 backup, watchdog, retention, mounts, Docker and Nextcloud stay independent.
```

### Technology choices (proposed; check repository conventions)
- Backend: Python 3.12+ with **Flask** application factory, typed service layer, Gunicorn for production. Avoid Celery/Redis for RC1.
- UI: Jinja2 templates, accessible responsive CSS, small vanilla JS modules for polling; no mandatory Node bundler. If using a SPA, justify CPU/RAM cost and preserve same API contracts.
- Data: **no required database** for RC1. Read live state and optional bounded in-memory cache. An optional small SQLite database may be added only for UI preferences and sanitized alert acknowledgment after approval; never copy Nextcloud DB.
- Listen only on `127.0.0.1:5010`. No direct public bind.
- Service identity: dedicated `mayhome-center` system user with no interactive shell; filesystem read access only through explicitly authorized safe paths/exporter.
- Deployment target: `/opt/mayhome-center` with versioned release or Git-managed working tree, plus systemd `mayhome-center.service`.
- Resource budget (initial target, not guaranteed): application RSS < 200 MiB idle, status poll < 1s for cached responses, no full filesystem traversal of 182 GiB, <= 1 core transient. Measure on host.

### Security: preferred read-only data collection
Do **not** add the web service to the `docker` group (it is effectively root-equivalent). Do **not** grant passwordless general sudo, journal access to unrelated system logs, read access to `.env`, `config.php` contents, SQL dumps, or private NC user files.

Implement a **separate privileged status collector**, executed by a root-owned `systemd` oneshot+timer or tightly audited root helper, which:
- Reads only a fixed allowlist of systemd unit names, Docker container *state* fields, storage UUID/mount status, safe snapshot metadata and recent tagged backup log lines.
- Validates root filesystem ownership, path locations, mount UUIDs and symlinks; rejects paths escaping backup root.
- Emits **atomic JSON** under `/run/mayhome-center/status.json` with no secrets (root-owned directory, JSON readable by dedicated service group). Use temp+fsync+rename, with restrictive permissions.
- Includes `generated_at` timestamp and per-source freshness/errors; missing updates must be visible as STALE, not healthy.
- Cannot accept arbitrary shell commands or user-supplied paths. All subprocesses use fixed argv, timeout, max output bytes and no `shell=True`.
- Does not run Backup Guard if it could affect state without verifying its contents; guard is presumed read-only based on prior observed use, but inspect before invoking.
- Avoids expensive hashing of snapshots in each status refresh. Read existing database SHA manifests and optionally cache validated checksums from a separately scheduled, approved integrity verifier; mark unknown rather than pretend verified.

This architecture avoids giving a network-facing app root access. If a direct read-only adapter is chosen instead, Codex must document why it is equally least-privileged and obtain approval before deployment.

### Deployment/topology boundaries
- Configure the Cloudflare hostname only after checking existing tunnel config and collisions. Domain `mayhome.cuong.win` is a **desired** hostname, not proof of DNS/Tunnel readiness.
- Require a Cloudflare Access identity policy / MFA for remote access; ensure unauthorized requests never reach meaningful API data. Backend must also independently enforce authentication or validate trusted Cloudflare Access JWTs against issuer/audience/JWKS (no blind trust in a forgeable header). If using local app auth, apply strong password/session security. Select one coherent scheme during Stage 0 and document it.
- Restrict origin to loopback, strict Host validation, forward header trust only from recognized proxy, secure cookies when applicable, CSRF controls for login/preferences, CSP, HSTS at edge, `X-Content-Type-Options`, `Referrer-Policy`, `Cache-Control: no-store` for health/log responses.
- Never log passwords, cookies, tunnel tokens, env variables, SQL content or personal filenames.

---

## 3. UX and information architecture

### Primary navigation (RC1)
1. **Overview** — overall protection summary, last run, next run, warnings.
2. **Backup Manager** — checkpoints/history, jobs, integrity and storage.
3. **Logs** — scoped, redacted backup/guard/watchdog/retention logs.
4. **Settings / About** — read-only configuration summary, version, last refresh, help; no controls to modify system behavior.

Reserve stable module IDs and navigation slots for future `storage`, `docker`, `nextcloud`, `services`, `alerts` without exposing dummy pages as working features.

### Desktop layout
- Fixed/expandable left navigation; compact top bar with `MAYHOME CENTER`, host label `mayhome`, UTC+07 clock, freshness state.
- Main content max-width ~1440px. Use purposeful whitespace, clear hierarchy, restrained status colors.
- Top summary cards: **Protection status**, **Last backup**, **Next scheduled run**, **Backup HDD**.
- Below: 14–30 day job timeline, source/backup mount status, current backup phase, retention status, recent errors.
- Snapshot detail is a separate route or drawer, not a table expanded to 182 GiB of content.

### Mobile UX
- Responsive 360px minimum without horizontal scrolling of entire viewport; bottom or compact tab navigation, large touch areas >=44px.
- Cards stack vertically, history table becomes structured cards; logs monospaced with horizontal scroll in the log pane only.
- Status updates never flash/displace button positions; use polite live regions.
- Theme follows system light/dark; accessible contrast AA, keyboard navigation and screen-reader labeling.
- Preferred text language: Vietnamese default, English-ready string catalog; do not hardcode status semantics in translations.

### Status semantics (mandatory)
| Badge | Meaning |
|---|---|
| `HEALTHY` | All essential telemetry fresh; source+backup UUID mounted correctly/RW; services expected state; recent backup within documented policy; no unresolved recovery marker. |
| `RUNNING` | Backup service active, with latest known stage and elapsed duration. Not automatically success. |
| `WARNING` | Backup overdue, low space threshold, retention has candidates, history missing, or degraded noncritical signal. |
| `CRITICAL` | Wrong/missing/RO mount, unresolved marker with maintenance on, backup failed, checksum mismatch, or app/cron down unexpectedly. |
| `UNKNOWN` | Incomplete telemetry, collector error or never-run event. Do not infer success. |
| `STALE` | Collector data older than TTL; show age and do not display GREEN based on stale data. |

**No invented progress %:** RC1.3 writes stage logs, not byte-level progress; display **stage + elapsed time**, e.g. `Pre-sync`, `Maintenance`, `Final sync`, `Database dump`, `Commit`, `Recovering`, or `Unknown`. A percent bar is forbidden unless engine provides grounded progress telemetry. Show duration for last completed jobs.

### Dashboard cards (real evidence only)
- **Last completed:** timestamp, duration, status (from journald + systemd results; detect unknown after reboot).
- **Next run:** `systemctl list-timers`/`show`; timezone from host, not hardcoded.
- **Snapshots:** count of complete, incomplete staging count, protected count; latest complete and checksum metadata.
- **Storage:** size/used/free, filesystem, mountpoint, expected vs actual UUID. `du -sh` logical snapshot size is **not** incremental disk usage; show HDD actual used delta only as separately measured.
- **Protection:** Mount Guard, Backup Guard, marker, Nextcloud maintenance and cron. Do not claim maintenance OFF if occ status not accessible; mark unknown.
- **Retention:** 14 days, dry-run, eligible/protected, actual delete authorization state; never expose or invoke `--apply` in RC1.

---

## 4. Snapshot and operational data contracts

### Snapshot types
- `baseline`: root-level `data/`, `config/application/`, `database/nextcloud-20261015-174303.sql.gz`, `manifests/backup-20261015-174303.txt` (not a directory under `snapshots/`).
- `checkpoint`: timestamp-named directory `snapshots/YYYYMMDD-HHMMSS` with `data/`, `application/`, `database/nextcloud.sql.gz`, `config/`, `manifests/database.sha256`, `manifests/checkpoint.txt`, `COMPLETE`.
- `staging`: `.staging-*` folders, INCOMPLETE; display but do not remove or infer orphan without corroborating service/lock status.

### Integrity levels — do not conflate
- `present`: metadata and completion marker observed.
- `structure_valid`: expected files/marker match allowed structure.
- `db_checksum_verified`: computed hash matched manifest and gzip validated at a recorded timestamp.
- `restoration_verified`: recovered in an **isolated environment** under a separately approved process. Never claim for RC1.3 snapshots without evidence.
- `unknown` / `failed`: missing, stale, or mismatched evidence.

Merely reading a `.sha256` file is not checksum verification. `COMPLETE` means engine committed a directory; it is not equivalent to a successful restore test. Historical checks must record source and verification time.

### Proposed internal domain objects
```text
HostStatus { host, collected_at, collector_version, freshness, errors[] }
MountStatus { mountpoint, expected_uuid, observed_uuid?, filesystem?, rw?, total_bytes?, used_bytes?, available_bytes?, status }
ServiceStatus { unit_name, active_state, sub_state?, result?, enabled_state?, observed_at }
ContainerStatus { name, running?, health?, observed_at }
BackupJob { id, start_at?, finish_at?, result, stage, duration_sec?, source: journal|systemd|unknown, log_available }
Snapshot { id, type: baseline|checkpoint|staging, timestamp?, completeness, integrity_level, verified_at?, protected, protection_reasons[], logical_bytes?, metadata_errors[] }
RetentionStatus { days, mode: dry_run|apply_authorized|unknown, protected_count, eligible_count, generated_at, errors[] }
RecoveryStatus { marker_present, marker_validity: valid|invalid|unreadable|absent, phase?, watchdog_state, last_log_event?, severity }
```

Avoid storing raw paths, raw Docker inspection dictionaries, configuration contents or arbitrary shell output in API objects. IDs are allowlisted timestamp strings or fixed `baseline`; never interpolate user-supplied IDs into a host path without strict parsing and root containment.

### API v1 (GET only, RC1)
| Endpoint | Contents |
|---|---|
| `GET /healthz` | Local app liveness only; must not claim backup healthy. |
| `GET /readyz` | App readiness/collector freshness, without secrets. |
| `GET /api/v1/overview` | Aggregated status and alerts, server timestamp. |
| `GET /api/v1/backups/status` | Timer, engine, guard, marker, cron, maintenance, current phase. |
| `GET /api/v1/backups/jobs?limit=20&cursor=...` | Bounded job history reconstructed from journal. |
| `GET /api/v1/backups/snapshots?limit=50&cursor=...` | Baseline/checkpoint/staging inventory. |
| `GET /api/v1/backups/snapshots/{id}` | Metadata only; protected reasons, integrity evidence. |
| `GET /api/v1/backups/retention` | Retention policy and last known dry-run output. |
| `GET /api/v1/backups/storage` | Source/destination mount status and disk usage. |
| `GET /api/v1/logs?source=backup&limit=100&cursor=...` | Redacted, bounded, scoped log entries. |
| `GET /api/v1/capabilities` | Enabled feature flags, all mutating features `false`. |

Canonical envelope:
```json
{
  "data": {},
  "meta": {"collected_at": "2026-10-16T09:00:00+07:00", "freshness": "fresh", "source": "collector-v1"},
  "errors": []
}
```
Errors must include stable code (`MOUNT_UUID_MISMATCH`, `COLLECTOR_STALE`, `JOURNAL_UNAVAILABLE`, `CHECKSUM_UNKNOWN`, `MARKER_PRESENT`, etc.) and safe human description, not exception dumps. Use HTTP 200 for partial telemetry with per-field errors, 503 for collector entirely unavailable, 401/403 for authentication failures, 404 for missing snapshot, 429 on abuse. No POST/PUT/PATCH/DELETE management endpoints in RC1.

### Parsing standards
- Use `systemctl show --property=... --value` or DBus, not column-position parsing of human `systemctl status` output.
- Parse timer scheduling from systemd properties (e.g. `NextElapseUSecRealtime`) rather than scraping formatted `list-timers` and assuming locale.
- Parse journald with `journalctl -o json` and strict unit allowlist/time window/record cap; redact before output.
- Docker: only exact container names `nextcloud-app`, `nextcloud-cron`, `nextcloud-db` and allowed state fields. Never return environment, mounts with secrets, labels with credentials or inspect payload.
- Disk: `findmnt -J`/`lsblk` or statvfs with UUID and RW verification; **never** infer correct backup media from `/dev/sdb2` because kernel device names can change.
- Snapshot directory: bounded number entries and scan time; no recursive walk through user data. No file content browsing.
- Source logs of script have `NC-BACKUP-RC1.3` and `BACKUP PASS/FAILED`; handle old RC1.2 records as historical, not current engine version.
- On service restart/system reboot, history reconstruction must not mark truncated journal as success. State confidence tracked explicitly.

---

## 5. Monitoring cadence and resource safety

- Collector refresh target: 15–30 seconds for volatile service status; snapshot inventory 2–5 minutes; expensive integrity checks excluded from polling. Set actual schedule after profiling.
- UI polls `/api/v1/overview` every 15–30s when visible, every 60–120s when not; stop polling on disconnect and exponential backoff errors. No mandatory WebSockets in RC1.
- TTL: service state e.g. 90 seconds; inventory 10 minutes. Exceed TTL -> `STALE`.
- Timeouts: short subprocess command limits (2–5s typical) and max bytes; bounded journal requests (e.g. <=200 entries per response); rotate collector diagnostics.
- Do not run `du -sh` recursively over 182 GiB on each request. Any size scanning is optional background-only and resource-budgeted.
- Slow HDD, missing mounts and missing journal must not hang HTTP workers or cause 500-loop.
- Root collector must never follow arbitrary snapshot symlinks or read `.env`/`config.php` contents.

---

## 6. Repository and extension design

Suggested tree (adapt to project conventions):
```text
mayhome-center/
  README.md
  MANIFEST.md
  pyproject.toml
  src/mayhome_center/
    __init__.py
    app.py                 # app factory, blueprints
    config.py
    auth/
    core/
      contracts.py        # schema/version and status semantics
      registry.py         # explicit module registry
      errors.py
      security.py
    modules/
      backup_manager/
        api.py
        service.py
        models.py
        parsers.py
        views.py
        templates/
        static/
      overview/
    collectors/
      root_status_collector.py
      systemd.py
      docker_state.py
      disk.py
      journal.py
      snapshot_inventory.py
  packaging/
    systemd/mayhome-center.service
    systemd/mayhome-center-collector.service
    systemd/mayhome-center-collector.timer
    installer.sh
    rollback.sh
  tests/
    fixtures/
    unit/
    integration/
    security/
    runtime/
  docs/
    api-v1.md
    ops-runbook.md
    threat-model.md
    acceptance-matrix.md
```

**Module contract**: each module registers `{id, title, icon, routes, required_capabilities, nav_order}`; services depend on typed collector ports rather than importing backup-specific shell commands. No dynamic plugin loading from user-writable locations. `core` knows no Nextcloud-specific paths. Only backup module and its collectors know backup data schemas. Future modules must be gated by feature flags and separately approved permissions.

**Naming**: product `MAYHOME CENTER`; Python package `mayhome_center`; Linux service `mayhome-center`; internal module `backup_manager`; hostname `mayhome.cuong.win`.

---

## 7. Stage-gated Codex plan

### Stage 0 — Audit / evidence baseline (read-only)
**Outputs:** hardware/OS runtime inventory (only necessary details), service unit snapshot, source script SHA256, mount UUIDs, permission matrix, current timers, tunnel routes and port availability; threat model; actual source data access mapping; design ADR for authentication and collector. Check that retention timer actual state and 02:00 job outcome are known or marked unknown. Verify git repo location and clean worktree before editing.

**Acceptance:** no modified production state; read-only commands; document every actual vs assumed value; sign off collector permissions. **STOP** if mount mismatch, active backup, recovery marker, or Nextcloud maintenance ON during deployment window.

### Stage 1 — Foundation and safe collector
**Outputs:** app factory, config, typed contracts, root collector with fixed whitelist, sanitization and atomic JSON, systemd packaging, health/readiness, module registry, fixture-based tests.

**Acceptance:** negative tests for privilege escalation, path traversal, symlink, wrong UUID, stale JSON, huge/truncated journal, Docker absent, crash/reboot; no network app root privileges; no secrets emitted. ≥95% coverage on parsing/security paths (target, not release blocker when coverage cannot measure; disclose).

### Stage 2 — Nextcloud Backup Manager backend
**Outputs:** history parsing, snapshot inventory, integrity levels, retention state, storage/guard status, health aggregator, API v1, stable error contracts, pagination.

**Acceptance:** verified known baseline & checkpoint recognition; `.staging-*` never considered complete; checksum existence not shown as verified; `systemctl` inactive vs failed distinguished; source/destination UUID mismatch CRITICAL; partial errors remain visible; no recursive read of data directory.

### Stage 3 — Desktop/mobile UI
**Outputs:** Overview + Backup Manager + Logs + Settings/About, card/table/detail layout, visual states, bilingual-ready strings, polling and refresh control, responsive mobile, accessible status presentation.

**Acceptance:** 360px/390px mobile, 768px tablet, 1440px desktop; no page-wide horizontal scrolling; keyboard, contrast AA, contrast-safe statuses and logs, reduced-motion support, no fake live numbers or percentage.

### Stage 4 — Security, deployment, tunnel
**Outputs:** release packaging, root collector hardened service, Gunicorn service bound to loopback, Cloudflare hostname `mayhome.cuong.win` and Access policy only with explicit config review/approval, TLS/identity validation, start-on-boot MHC services; runbook and rollback.

**Acceptance:** external unauthorized users cannot get data; auth bypass/header spoof tests fail; no admin/root web process; port not publicly exposed; existing `cloud.cuong.win`, Nextcloud containers and RC1.3 unchanged; safe uninstall only MHC files.

### Stage 5 — Runtime / freeze RC1
**Outputs:** Playwright automated viewport tests, short observed runtime (≥15 minutes), resource profile, 02:00 backup visibility once actually executed, service failure simulation using mock collector (not shutdown production), Acceptance Matrix with evidence and screenshots, frozen RC1 tag/commit.

**Acceptance:** all P0/P1 tests pass; fresh snapshot appears after real engine run without MHC intervention; system recovers gracefully from stale collector; app Restart=on-failure works; backup schedule/engine baseline not changed. If first auto-run not yet observed, mark **RC1 operational acceptance pending** rather than PASS.

---

## 8. Detailed acceptance matrix

| ID | Priority | Scenario | Expected |
|---|---|---|---|
| MHC-001 | P0 | Start web without root | Starts under restricted account, only loopback listener |
| MHC-002 | P0 | Existing Nextcloud baseline and protected checkpoint | Visible, never mutated |
| MHC-003 | P0 | API attempts POST backup/delete/restore | 404/405; no executable handler |
| MHC-004 | P0 | Docker API exposure attempt | No docker socket, env or secrets accessible |
| MHC-005 | P0 | Snapshot path traversal / symlink | Rejected, no read outside backup root |
| MHC-006 | P0 | Wrong/unmounted source or backup HDD | CRITICAL; no false GREEN |
| MHC-007 | P0 | Incomplete staging | INCOMPLETE, never verified/eligible for deletion |
| MHC-008 | P0 | `COMPLETE` present without validated SQL checksum | `present` not `db_checksum_verified` |
| MHC-009 | P0 | Backup running | Correct stage + elapsed; no fabricated % |
| MHC-010 | P0 | Backup failed | Failure, safe error, evidence from logs; no actions |
| MHC-011 | P0 | Recovery marker present/invalid | CRITICAL/WARNING with current evidence; do not clear |
| MHC-012 | P0 | Source app/cron/DB down or unknown | Clear degraded/unknown status |
| MHC-013 | P0 | Collector stopped or stale | Explicit STALE; UI cannot say HEALTHY |
| MHC-014 | P0 | Unauthenticated remote browser | Access denied, no API data |
| MHC-015 | P0 | Forged Cloudflare header/JWT | Rejected |
| MHC-016 | P0 | Database dump, `.env`, `config.php` | Never exposed in responses/logs |
| MHC-017 | P0 | No write access to backup system | App/collector cannot modify backup paths, timers, Docker |
| MHC-018 | P0 | Rollback MHC | Leaves original backups and Nextcloud services untouched |
| MHC-019 | P1 | Next backup schedule | Exact host-local systemd schedule; DST/timezone safe |
| MHC-020 | P1 | Retention dry-run | Protected/eligible counts accurate; no apply control |
| MHC-021 | P1 | Large log response | Paginated, bounded, redacted |
| MHC-022 | P1 | 360px mobile view | No global horizontal scroll; usable cards |
| MHC-023 | P1 | Accessibility | Keyboard + semantic states, screen-reader labels |
| MHC-024 | P1 | Unknown historical journal | Not falsely marked successful |
| MHC-025 | P1 | Hardlink storage metric | Logical size distinct from actual used bytes |
| MHC-026 | P1 | Browser refresh/network error | Graceful recovery, no repeated uncontrolled requests |
| MHC-027 | P1 | Backup source and destination storage status | Expected UUID, actual UUID, mount RW and free space |
| MHC-028 | P1 | Multiple snapshots | Correct protection reasons and sorting |
| MHC-029 | P1 | 15+ minute runtime | Resource consumption recorded, UI responsive |
| MHC-030 | P1 | First daily 02:00 backup | Shown correctly after observed success; no invented PASS |
| MHC-031 | P1 | Existing Cloudflare tunnel coexistence | `cloud.cuong.win` unchanged |
| MHC-032 | P1 | MHC stops unexpectedly | Backup RC1.3 proceeds independent of MHC |
| MHC-033 | P2 | Vietnamese and English strings | Stable locale-aware labels |
| MHC-034 | P2 | Retention audit 04:00 timer not enabled | Report actual state; never activate implicitly |

Evidence for each: test command, expected, actual, PASS/FAIL, timestamp, code commit, screenshot for UI, production safety confirmation. No blanket “runtime PASS” without evidence.

---

## 9. Deployment and rollback contract

### Safe install
1. Review repo, `git status`, dependencies and release SHA; no dirty overwrite.
2. Confirm backup guard and `maintenance: false`, recovery marker absent, mounts/UUIDs safe; avoid backup-active maintenance window.
3. Install new MHC files only into `/opt/mayhome-center` and MHC-specific systemd units. Collector output under `/run/mayhome-center`.
4. Use `systemd-analyze verify` on **new MHC units** before enabling MHC services. Existing Nextcloud units untouched.
5. Bind to loopback, test curl locally, then configure Cloudflare only after checking existing config/credentials via safe commands that do not print tokens.
6. Check auth and policy before exposing domain; verify remote access on desktop and mobile.
7. Re-check Nextcloud state, Mount Guard, RC1.3 timers, baseline files and backup guard before declaring acceptance.

### Rollback
- Disable **only** `mayhome-center.service` and `mayhome-center-collector.timer` (and own service) and restore previous MHC release; never stop or alter Nextcloud/backup units.
- Preserve MHC logs for troubleshooting, remove only known MHC-owned files if uninstalling.
- Confirm production health and backups remain operational, verify no MHC listener left on public interfaces.

### Operational handbook requirements
- Install/upgrade/rollback commands, systemd logs, collector stale resolution, authentication rotation, Tunnel/Access access control, disk-full dashboard behavior, backup failure triage, and incident runbook. No instruction to fix backup by automatically deleting data.
- Owner must be able to disable MHC entirely without affecting automatic backups.

---

## 10. Future RC2+ extensibility (not to implement now)

`MHC-RC2` may propose controlled actions through a root-owned, minimal privileged broker with allowlisted operations, CSRF, reauthentication, action confirmation, idempotency keys, concurrency lock checks and immutable audit trail. Specifically: run backup now, enable/disable schedule, explicitly approved integrity verification, retention dry-run triggering, and guarded retention apply (requires separate approval). **Never** provide arbitrary command execution or direct shell input.

`MHC-RC3` may add storage/docker/nextcloud monitoring and Alerts with separate read adapters. Dangerous actions (snapshot delete, restore, mount, maintenance toggles, container lifecycle) require explicit future manifests and disaster recovery drills.

Later: authenticated export of non-secret reports, mobile push/email notifications, versioned plugins, aggregate PiCC overview if explicitly requested. Do not pre-install these dependencies today.

---

## 11. Codex deliverable checklist

- [ ] Source repo initialized/updated with stage-gated commits and precise change list
- [ ] `/docs/architecture.md`, security threat model and permission matrix
- [ ] Typed data contracts, API docs, safe collector + fixtures
- [ ] Desktop/mobile UI with documented screenshots and accessibility evidence
- [ ] Linux service units, explicit install/rollback scripts, no side effects on RC1.3
- [ ] Cloudflare configuration plan and approved deployment evidence; never expose secrets
- [ ] Automated tests and `Acceptance Matrix` with actual command output
- [ ] Runtime performance and 15+ minute observation results
- [ ] Host protection postchecks (Guard, mount UUIDs, cron, maintenance, schedules)
- [ ] RC1 freeze commit/tag and concise operator guide

### Codex first instruction (paste into Codex)

> Implement MAYHOME CENTER RC1 from this manifest. **First perform Stage 0 read-only audit on `mayhome` and report findings, blockers, actual permission model and implementation plan. Do not modify production backup engine RC1.3 or its timers, disks, Docker/Nextcloud, restore data, or execute any management operation.** After Stage 0 approval, implement independent root-isolated read-only collector, API and responsive Nextcloud Backup Manager. Proceed stage-by-stage with tests and acceptance evidence; prepare deployment behind `mayhome.cuong.win` with Cloudflare Access but do not create DNS/tunnel routes or enable services until authorized. Preserve baseline `20261015-174303` and checkpoint `20261015-210345` unmodified. Clearly label historical, unknown and current states. Do not claim runtime acceptance until measured on `mayhome`.

---

**Manifest scope freeze:** MHC-RC1 Nextcloud Backup Manager **read-only**. **Backup Engine RC1.3 is the source of truth; MAYHOME CENTER is a separate observer.**

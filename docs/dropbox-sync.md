# Dropbox sync

One-way backup of registered xylocopa projects to the user's Dropbox through
the Dropbox HTTP API v2. No desktop client, no changes to project folders.

The feature is **off by default**. It is switched on per project from the
project's Settings card; the first time any project is enabled the UI walks
the user through linking a Dropbox app (OAuth 2 + PKCE) and then lets them
pick which top-level folders of that project to sync.

## Concepts

| Term | Meaning |
|---|---|
| Link | The orchestrator holds a refresh token for one Dropbox account. Global, shared by all projects. |
| App key | Client id of a Dropbox app the user creates (App Folder access). Not a secret; stored with the link. There is no app secret anywhere. |
| Project sync | Per-project switch (`Project.dropbox_sync`) plus a folder selection (`Project.dropbox_folders`) and extra ignore rules (`Project.dropbox_ignore`). |
| Run | One pass over all enabled projects: scan → diff → upload (→ optional prune). Interval-scheduled, plus manual "Sync now". |
| Remote layout | `/<project-name>/<relative path>` inside the app folder, i.e. `Dropbox/Apps/<AppName>/<project-name>/...`. |

## Files on disk

All under `DROPBOX_SYNC_DIR` (default `data/dropbox/`, gitignored with the rest of `data/`):

| File | Contents | Perms |
|---|---|---|
| `token.json` | `{app_key, refresh_token, access_token, expires_at, account_id, account_name, email}` | `0600` |
| `config.json` | Global runtime config (see below) | `0600` |
| `state.db` | SQLite state table for incremental sync | `0600` |

## Global config (`config.json`, edited via `PUT /api/dropbox/config`)

| Key | Default | Meaning |
|---|---|---|
| `enabled` | `true` | Master switch for the scheduler. Linking sets it; "Pause" clears it. |
| `interval_hours` | `1` | Scheduler interval. |
| `concurrency` | `4` | Concurrent uploads. |
| `chunk_mb` | `8` | Upload-session chunk size (multiple of 4 MiB). |
| `max_file_mb` | `2048` | Files larger than this are skipped (reported as `too_large`). |
| `max_files_per_project` | `300000` | Budget; a project over budget is reported and skipped until narrowed. |
| `allowlist_mode` | `false` | If true, only files whose extension is in `allowlist_exts` are synced. |
| `allowlist_exts` | code/docs list | Used only in allowlist mode. |
| `prune` | `false` | Propagate local deletions to Dropbox (`files/delete_batch`). |
| `bandwidth_kbps` | `0` | Upload throttle; `0` = unlimited. |

## Ignore rules

Evaluated in this order; a path is skipped if any rule matches. Symlinks are
never followed and never uploaded.

1. Folder selection: only the top-level entries listed in `Project.dropbox_folders`
   (`null` = everything). The pseudo-entry `.` stands for files directly in the
   project root.
2. Built-in defaults (gitignore syntax): `.git/`, `*venv*/`, `.venv/`, `node_modules/`,
   `__pycache__/`, `.cache/`, `*.egg-info/`, `build/`, `dist/`, `torch_home/`,
   `.thumbcache/`, `wandb/`, `.trash/`, `.xylo-internal/`, `*.pyc`, `.DS_Store`.
3. `<project>/.xylocopa-syncignore` (gitignore syntax, optional).
4. `Project.dropbox_ignore` — extra rules edited in the UI.
5. Budgets: `max_file_mb`, allowlist mode.

Matching uses `pathspec` (`gitwildmatch`).

## Upload engine

- Scan runs in a worker thread (`asyncio.to_thread`) with `os.scandir`, never
  following symlinks.
- Diff against `state.db`: unchanged `(size, mtime_ns)` → skip without reading;
  otherwise compute the Dropbox `content_hash` (SHA-256 per 4 MiB block, then
  SHA-256 of the concatenated digests) and skip if it equals the stored hash.
- Every file is uploaded through an upload session: `upload_session/start`
  (`close=true` when the whole file fits in one ≤150 MB request), otherwise
  `append_v2` in `chunk_mb` chunks. Sessions are committed in groups with
  `upload_session/finish_batch` (`mode=overwrite`, `mute=true`, `client_modified`
  from the local mtime) and `finish_batch/check` — this is what Dropbox
  recommends for bulk uploads because each `files/upload` is a separate
  namespace write and trips `too_many_write_operations`.
- Retries: exponential backoff on 429/5xx honouring `Retry-After`; a 401
  refreshes the access token once and retries.
- Resumable: open sessions are recorded in `state.db` (`session_id`, `offset`)
  so a restart continues where it stopped.
- Prune (opt-in): state rows whose local file is gone are deleted remotely in
  `files/delete_batch` groups.
- The engine never blocks the API: all HTTP is `httpx.AsyncClient`, file reads
  and hashing run in threads, and a semaphore bounds concurrency.

## State schema (`state.db`)

```sql
CREATE TABLE files (
  project TEXT NOT NULL, rel_path TEXT NOT NULL,
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL,
  content_hash TEXT NOT NULL, remote_rev TEXT,
  uploaded_at TEXT NOT NULL,
  PRIMARY KEY (project, rel_path)
);
CREATE TABLE pending_sessions (
  project TEXT NOT NULL, rel_path TEXT NOT NULL,
  session_id TEXT NOT NULL, offset INTEGER NOT NULL,
  size INTEGER NOT NULL, mtime_ns INTEGER NOT NULL, content_hash TEXT NOT NULL,
  PRIMARY KEY (project, rel_path)
);
CREATE TABLE runs (
  id INTEGER PRIMARY KEY, started_at TEXT NOT NULL, finished_at TEXT,
  status TEXT NOT NULL,             -- running | ok | error | cancelled
  trigger TEXT NOT NULL,            -- schedule | manual
  files_scanned INTEGER DEFAULT 0, files_uploaded INTEGER DEFAULT 0,
  bytes_uploaded INTEGER DEFAULT 0, files_deleted INTEGER DEFAULT 0,
  errors INTEGER DEFAULT 0, error_sample TEXT
);
CREATE TABLE project_stats (
  project TEXT PRIMARY KEY, files_synced INTEGER, bytes_synced INTEGER,
  last_synced_at TEXT, last_error TEXT
);
```

## Database columns (`projects` table)

| Column | Type | Meaning |
|---|---|---|
| `dropbox_sync` | `BOOLEAN NOT NULL DEFAULT 0` | Per-project switch. |
| `dropbox_folders` | `TEXT` | JSON array of selected top-level entries; `NULL` = all. |
| `dropbox_ignore` | `TEXT` | Extra gitignore-style rules. |

Exposed on `ProjectOut` and editable through `PATCH /api/projects/{name}/settings`.

## HTTP API

| Method | Path | Effect |
|---|---|---|
| `GET` | `/api/dropbox/status` | Link state, account, space usage, config, current run progress, last run, per-project stats, queue length, recent errors. |
| `PUT` | `/api/dropbox/config` | Update global config (validated, persisted to `config.json`, wakes the scheduler). |
| `POST` | `/api/dropbox/link/start` | Body `{app_key}` → `{authorize_url}`. Generates the PKCE verifier and keeps it in memory. |
| `POST` | `/api/dropbox/link/complete` | Body `{code}` → exchanges the code, stores the token, returns the account. |
| `DELETE` | `/api/dropbox/link` | Revoke the token and delete `token.json`. Remote files are left alone. |
| `POST` | `/api/dropbox/sync` | Body `{project?}` → start a run now (whole account or one project). |
| `POST` | `/api/dropbox/pause` / `/resume` | Pause / resume the scheduler (a running run finishes its current file batch and stops). |
| `POST` | `/api/dropbox/dry-run` | Body `{project, folders?, ignore?}` → per-top-level-entry `{files, bytes, skipped}` after ignore rules, without uploading. Runs the scan in a thread. |
| `GET` | `/api/projects/{name}/dropbox/folders` | Top-level entries of a project (`name, type, default_ignored, selected`) for the folder picker. |

## OAuth flow (PKCE, no app secret)

1. The user creates a Dropbox app at <https://www.dropbox.com/developers/apps>:
   *Scoped access → App folder*, permissions `files.metadata.read`,
   `files.content.read`, `files.content.write`, `account_info.read`.
   The link dialog links to this page and lists the permissions.
2. `link/start`: the orchestrator builds
   `https://www.dropbox.com/oauth2/authorize?client_id=…&response_type=code&code_challenge=…&code_challenge_method=S256&token_access_type=offline`
   (no `redirect_uri` — Dropbox shows the code for copy/paste).
3. The user pastes the code; `link/complete` posts it to
   `https://api.dropboxapi.com/oauth2/token` with the verifier and stores the
   refresh token.
4. Access tokens are refreshed with `grant_type=refresh_token` when expired.
5. Unlink calls `/2/auth/token/revoke` then deletes `token.json`.

## MCP tools (read-only)

- `dropbox_status()` — same payload as `GET /api/dropbox/status`.
- `dropbox_dry_run(project)` — same as `POST /api/dropbox/dry-run`.

## Web UI

- **Project → Settings → Dropbox Sync** row: toggle. Turning it on when no
  account is linked opens the link dialog first, then the folder picker
  (multi-select with *Select all* / *Deselect all*, per-folder file/size
  counts from a dry run, extra ignore rules). When on, the row shows the
  selection summary and last sync time with *Folders…* and *Sync now*.
- **Monitor → Dropbox** card: account, space, scheduler config, last/current
  run, per-project counts, errors, *Sync now* / *Pause* / *Unlink*.

## v2 (designed, not built)

Two-way sync: `files/list_folder` + `/continue` cursors per project namespace,
`files/list_folder/longpoll` to wake on remote changes, download changed files
into the workspace with conflict copies (`name (conflicted copy <date>).ext`)
when the local file changed since the last known state, never touching ignored
paths. The `files` table already carries `remote_rev` for this.

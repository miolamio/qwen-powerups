# Senior Advisor configuration

Operator settings for packet kinds, consultation policy, and session records. For provider installation, login, and readiness, see [providers.md](providers.md). These settings describe `scripts/ask_senior.py`; `--help` lists flags, while environment defaults and enforcement are defined in the source.

## Set it up at the start of a session

Choose a level, autonomous budget, confusion reserve, minimum interval, and log root in the shell inherited by the agent. From the repository root:

```bash
export SENIOR_POLICY=normal
export SENIOR_MAX_CONSULTATIONS=5
export SENIOR_CONFUSION_RESERVE=1
export SENIOR_MIN_INTERVAL_SECONDS=60
export SENIOR_LOG_DIR=.senior-advisor
python3 senior-advisor/scripts/ask_senior.py --status
```

Read `policy`, `remaining`, `window_minutes`, `window_sessions`, `counts`, `refusals`, `next_allowed_in`, and `path` in the JSON output. `--status` does not call a provider or create a session on disk; `exists: false` is normal before the first recorded attempt. A relative log root is resolved against the calling process's working directory; use an absolute path when several working directories should share policy and history.

The operator owns these settings. The agent may write `policy.json` only after an explicit user request to change policy and must echo the level, budget, reserve, and interval written. Environment and flag overrides still win over that file: verify the effective result with `--status`.

## Policy levels

These are the built-in defaults; the file can customise triggers as described below.

| Level | Allowed autonomous triggers | Default autonomous budget | Default confusion reserve | Default minimum interval (seconds) |
|---|---|---:|---:|---:|
| `off` | none | 0 | 0 | 0 |
| `rare` | `stakes`, `confusion` | 2 | 1 | 300 |
| `normal` | `stakes`, `failure`, `assumption`, `confusion`, `planning` | 5 | 1 | 60 |
| `eager` | `stakes`, `failure`, `assumption`, `confusion`, `planning` | 12 | 2 | 0 |

`user` is permitted at every level, bypasses the autonomous budget and minimum interval, and does not consume that budget. `confusion` bypasses only the interval; it still needs an allowed trigger and remaining budget. Both remain subject to duplicate and repeated-provider-failure guards. `off` forbids every autonomous trigger even if the file lists one.

The budget, duplicate guard, and minimum interval share a window across the log root. Include every session directory whose activity time is less than `SENIOR_SESSION_IDLE_MINUTES` old (default 240 minutes), and count its entire history, including older entries. Resolve activity from the newest parseable, offset-aware entry timestamp, using each entry's `finished` or, if unusable, its `started`. If no entry yields a usable time, fall back to `session.json`'s offset-aware `last_activity`, then the `ledger.jsonl` file's mtime. If all sources are unusable, include the session: undatable history restricts consultations. An old metadata timestamp or file mtime still lets undatable entries age out. Sessions without entries or with unreadable ledgers are skipped; malformed JSONL lines are skipped individually. Rotating `--session-id` or using `--new-session` does not restore budget, permit duplicate questions, or clear the interval. Separate `--log-dir` roots are the supported way to obtain genuinely separate budgets.

The budget counts recorded autonomous provider attempts (`trigger != "user"`), successful or failed. The interval runs from the newest resolved session activity in the window, including user requests and failures; the broker rounds remaining seconds up. For ordinary dated entries this is the newest `finished`. If all activity sources for a session are unusable, the interval conservatively runs from the current check time. Entry numbers are local to sessions. A policy refusal is exit `4`. When confusion is refused because the budget is spent, the agent stops and asks the user; it must not continue alone, change triggers, raise its own budget, or reset the session to evade the refusal.

Reserve K of the N autonomous calls for confusion, with `K = min(configured_reserve, max(0, N - 1))`. A budget of 1 therefore allows one consultation of any allowed kind. Non-confusion autonomous triggers are refused at `used >= N - K`, with a message that remaining calls are held for confusion. Confusion spends the general pool first, then the reserve, and is refused only at `used >= N`. The asserted trigger cannot be verified; an unlimited exemption would let any caller evade the budget by saying `confusion`. The reserve never increases total spend.

`--status` reports the effective clamped value as `policy.confusion_reserve` and `remaining: {"general": G, "confusion": C}`, where `G = max(0, N - K - used)` and `C = max(0, N - used)`. C includes G; do not add them. Trigger restrictions and the other guards still apply. `counts` and entry summaries cover the window; each summary includes `session_id` and `finished` so the counts and `window_sessions` can be reconciled. Session identity, path, start time, existence, and `refusals` describe only the selected session.

## Triggers

| Name | Use |
|---|---|
| `user` | The user asked for a senior in this turn. |
| `stakes` | Irreversible or high blast radius. |
| `failure` | Two materially different approaches already failed. |
| `assumption` | A consequential plan rests on an assumption that cannot be checked locally. |
| `confusion` | Stuck, self-contradictory, or unable to choose between approaches. |
| `planning` | Decomposition of a complex task. |

Trigger and packet kind are independent: an explicitly requested plan uses `--trigger user --kind plan`. Autonomous planning uses `--trigger planning --kind plan`.

## Precedence chains

Each line runs from highest priority to fallback; policy fields resolve independently.

- Policy: `--policy` → `SENIOR_POLICY` → `<log-root>/policy.json` field `level` → `normal`.
- Budget: `--max-consultations` → `SENIOR_MAX_CONSULTATIONS` → `policy.json` field `max_consultations` → effective level's default.
- Reserve: `--confusion-reserve` → `SENIOR_CONFUSION_RESERVE` → `policy.json` field `confusion_reserve` → effective level’s default; clamp after resolution.
- Interval: `--min-interval-seconds` → `SENIOR_MIN_INTERVAL_SECONDS` → `policy.json` field `min_interval_seconds` → effective level's default.
- Log root: `--log-dir` → `SENIOR_LOG_DIR` → `.senior-advisor` relative to the working directory.
- Session id: `--session-id` → `SENIOR_SESSION_ID` → auto-detection; `--new-session` replaces auto-detection and conflicts with either explicit-id source.
- Model: nonblank `--model` → nonblank provider-specific variable (`SENIOR_CODEX_MODEL`, `SENIOR_CLAUDE_MODEL`, `SENIOR_GROK_MODEL`, `SENIOR_KIMI_MODEL`, or `SENIOR_ZAI_MODEL`) → lane default (CLI chooses; Z.AI uses nonblank `ZAI_MODEL`, then `glm-5.3`).

An explicitly empty log root disables writes but resolves the root path to `.senior-advisor` for policy and history reads. Empty session ids are invalid. Model overrides are stripped of surrounding whitespace.

## `policy.json`

The file is `<log-root>/policy.json`, shared by sessions under that root. It is a JSON object with only the following optional keys; `{}` uses defaults.

| Key | Accepted value | If omitted |
|---|---|---|
| `level` | `off`, `rare`, `normal`, or `eager` | `normal` |
| `max_consultations` | Non-negative integer, or decimal integer string optionally prefixed with `+`; booleans rejected | Effective level's budget |
| `confusion_reserve` | Non-negative integer, or decimal integer string optionally prefixed with `+`; booleans rejected | Effective level’s reserve, clamped to at most `max(0, budget - 1)` |
| `min_interval_seconds` | Non-negative finite number or numeric string; booleans rejected | Effective level's interval |
| `triggers` | Array of the six trigger names; duplicates removed, empty array allowed | Effective level's triggers |

Example file:

```json
{
  "level": "rare",
  "max_consultations": 3,
  "confusion_reserve": 1,
  "min_interval_seconds": 120,
  "triggers": ["stakes", "confusion", "planning"]
}
```

The custom `triggers` list applies only when the level comes from the file or the default. Setting the level with a flag or `SENIOR_POLICY` restores that level's built-in triggers; it does not discard file budget, reserve, or interval values. Listing or omitting `user` cannot change its exemption. The `off` level always blocks autonomous calls.

A missing file is fine. An unreadable file, malformed JSON, non-object value, unknown key, or invalid field raises a broker error (exit `2`) during a real consultation or `--status`, even when a higher-priority override would replace that field. The broker does not silently ignore malformed policy. `--lint`, `--doctor`, and `--dry-run` do not load policy.

## Flags and environment variables

The table includes all phase additions and the existing broker flags needed alongside them. A flag wins over its paired environment variable; defaults below are final fallbacks. Provider credentials and CLI isolation setup remain in [providers.md](providers.md).

| Name | What it does | Default |
|---|---|---|
| `--kind` | Select `advice` or `plan` | Detect the first nonblank line: `Question:` or `Objective:`; otherwise advice validation rejects the unknown opening |
| `--trigger` | State one of the six consultation reasons | Required for a real consultation; optional for local checks |
| `--policy`, `SENIOR_POLICY` | Select policy level | File, then `normal` |
| `--max-consultations`, `SENIOR_MAX_CONSULTATIONS` | Set non-negative autonomous budget | File, then level default |
| `--confusion-reserve`, `SENIOR_CONFUSION_RESERVE` | Reserve a non-negative integer number of autonomous calls for confusion; clamped after resolution | File, then level default |
| `--min-interval-seconds`, `SENIOR_MIN_INTERVAL_SECONDS` | Set non-negative finite interval, in seconds; fractions accepted | File, then level default |
| `--log-dir`, `SENIOR_LOG_DIR` | Set ledger root; empty string disables writes | `.senior-advisor` |
| `--session-id`, `SENIOR_SESSION_ID` | Select an explicit session | Auto-detect |
| `SENIOR_SESSION_IDLE_MINUTES` | Positive finite idle window in minutes; validated even with an explicit id | `240`; environment only |
| `--new-session` | Mint a fresh id for a real consultation; conflicts with an explicit id | Off |
| `--no-ledger` | Disable ledger and refusal writes; existing policy/history are still read | Off; writes enabled |
| `--status` | Print session metadata/refusals, policy, window size and session count, window counts, remaining pools, interval, and entry summaries without a request | Off |
| `--lint` | Validate packet structure, narrative, size, and likely secrets; print JSON findings | Off |
| `--dry-run` | Validate packet and show selected provider/model/kind/size; no provider call, policy enforcement, or ledger write | Off |
| `--allow-sensitive` | Human-only bypass of the likely-secret guard | Off |
| `--allow-narrative` | Human-only bypass of the entire packet gate | Off |
| `--allow-repeat` | Human-only bypass of the duplicate guard | Off |
| `--provider`, `SENIOR_PROVIDER` | Choose `auto`, `codex`, `claude`, `grok`, `kimi`, or `zai` | `auto` |
| `--provider-order`, `SENIOR_PROVIDER_ORDER` | Comma-separated readiness order for auto-selection | `codex,claude,grok,kimi,zai` |
| `--model` | Override the selected lane's model | Provider variable, then lane default |
| `SENIOR_CODEX_MODEL`, `SENIOR_CLAUDE_MODEL`, `SENIOR_GROK_MODEL`, `SENIOR_KIMI_MODEL` | Configure the respective CLI lane's model | Unset; CLI chooses |
| `SENIOR_ZAI_MODEL`, `ZAI_MODEL` | Configure Z.AI model, in that precedence order | `glm-5.3` |
| `--timeout`, `SENIOR_TIMEOUT_SECONDS` | Positive integer provider timeout in seconds | `300` |
| `--max-input-bytes`, `SENIOR_MAX_INPUT_BYTES` | Positive integer input limit before the packet gate | `32768`; does not replace the gate's tighter limits |
| `--json` | Print the successful consultation envelope, including `kind`, checks, and ledger location | Off; plain answer |
| `--doctor` | Check provider readiness without authentication or a consultation | Off |
| `--help` | Print CLI usage and exit | Off |

Advice uses `Question`, `Stack`, `Given`, `Constraints`, `Ruled out`, `Evidence`; plan uses `Objective`, `Stack`, `Given`, `Constraints`, `Unknowns`, `Done when`. Follow the ordered templates in [SKILL.md](../SKILL.md). Opening content is limited to 300 UTF-8 bytes and two lines; an advice question ends in `?`, a plan objective does not. Advice prose before `Evidence:` is at most 1200 bytes; evidence including its header is at most 100 lines. All plan prose is at most 1600 bytes; the whole packet is at most 8192 bytes. `Done when:` must contain observable criteria, not an empty or `none` value. The narrative scan covers prose; the secret scan covers the entire packet.

Local checks do not reserve budget or guarantee a later call is allowed. `--status --trigger confusion` reports the interval exemption; without a trigger, status reports the ordinary interval. `--status`, `--lint`, and `--doctor` are mutually incompatible in pairs.

## Exit codes

| Code | Meaning |
|---|---|
| `0` | success; also a clean `--lint`, or a `--doctor` run that found a ready provider. |
| `2` | broker error: packet gate rejection, oversized packet, suspected secret, unavailable provider, provider failure or timeout. The reason is on stderr after `ask_senior:`; for `--lint` the findings are the JSON on stdout. |
| `3` | `--doctor` found no ready provider. |
| `4` | consultation refused by policy: budget spent or held for confusion, trigger not allowed at this level, minimum interval not elapsed, duplicate answered packet, or two provider calls in a row failed. The message on stderr names which. |

Invalid configuration is also a broker error. Never retry exit `4` by changing the trigger. A readiness check diagnoses availability; it does not clear recorded failures.

## Ledger layout

```text
<log-root>/
  policy.json
  current.json
  <session-id>/
    ledger.md
    ledger.jsonl
    session.json
```

| File | Contents |
|---|---|
| `ledger.md` | Human-readable `Q<n>` packet verbatim and `A<n>` answer or failure diagnostic; kind, trigger, timestamps, provider, model, duration, size, and bypass notices |
| `ledger.jsonl` | One compact JSON record per provider attempt; metadata and question summary, not the full packet or answer |
| `session.json` | `id`, `started`, `last_activity`, session-local `counts` (`total`, `ok`, `error`, `autonomous`, `user`), and `refusals` |
| `current.json` | Root-level pointer with `id` and `last_activity`, used for auto-detection |
| `policy.json` | Operator policy shared across this root's sessions |

The packet is stored verbatim in `ledger.md`; `ledger.jsonl` also exposes its opening summary, and answers or errors can contain sensitive information. Put `.senior-advisor/` (or the chosen in-repository log root) in `.gitignore` before using a versioned repository; do not commit ledgers. The operator reads the ledger; the agent must not delete or edit it.

New ledger root/session directories use mode `0700` and new files use `0600` on POSIX; existing directories and append-only files are not tightened. A temporary `.ledger.lock` serialises writes. Entry numbers increase from the largest readable `n`; unreadable JSONL lines are skipped and counted as `unreadable_entries` in status.

This is an actual JSONL line emitted by the broker during an offline check using example 6 and a synthetic answer. The `codex` lane was stubbed; no external model was called.

```json
{"n":1,"kind":"advice","trigger":"confusion","started":"2026-09-10T11:48:37+03:00","finished":"2026-09-10T11:48:37+03:00","provider":"codex","model":null,"duration_seconds":0.001,"packet_bytes":597,"packet_sha256":"56cea044a1347ff294a052ed84ce7f6dd7f4e694012bfc4b0bea067b10b8e979","question":"How should a receiver validate a callback when its HMAC covers only an identifier but unsigned fields drive state changes?","answer_chars":62,"status":"ok","bypassed":[],"error":null}
```

| Field | Meaning |
|---|---|
| `n` | Positive entry number shared by `Q<n>` and `A<n>` |
| `kind` | `advice` or `plan` |
| `trigger` | Reason supplied for the consultation |
| `started` | Local ISO 8601 timestamp with UTC offset, at attempt start |
| `finished` | Local ISO 8601 timestamp with UTC offset, at completion or failure |
| `provider` | Selected provider name |
| `model` | Resolved model override; `null` means CLI default, not the actual model id used by that CLI |
| `duration_seconds` | Elapsed monotonic time rounded to three decimal places |
| `packet_bytes` | Length of the original packet encoded as UTF-8 |
| `packet_sha256` | SHA-256 of the entire packet after whitespace collapse and Unicode case folding |
| `question` | Opening question or objective content, stripped and truncated to 160 characters |
| `answer_chars` | Answer length in characters; zero on failure |
| `status` | `ok` or `error` |
| `bypassed` | Applied bypass names: `sensitive`, `narrative`, `repeat`; empty when none |
| `error` | Provider failure diagnostic including provider name; `null` on success |

Duplicate detection compares the normalised hash against successful entries only, regardless of trigger or provider; a match points to the earlier `<session-id>/A<n>` anywhere in the window. It is not semantic question matching. Two consecutive recorded `error` entries in the selected session block further calls, including `user`, even for a different packet or provider. This guard is deliberately per session: `--new-session` remains the recovery after a provider outage; window guards still apply.

Logging is best-effort: a write failure prints `ask_senior: ledger write failed: ...` without replacing the consultation result. Successful calls and caught provider failures are recorded in the ledger; input rejection, invalid configuration, policy refusal, and provider-selection failure are not ledger entries. Local checks create no ledger records. Disabling writes or losing a write prevents that attempt from contributing to future budget, duplicate, interval, and failure checks; keep logging enabled for enforcement and report write warnings.

Policy refusals (exit `4`) are counted separately in `session.json` as `"refusals": {"count": N, "last": {"at": "<offset-aware ISO timestamp>", "trigger": "<trigger>", "reason": "<code>"}}`. Stable reason codes are `trigger_not_allowed`, `budget_exhausted`, `reserve_only`, `min_interval`, `duplicate`, and `repeated_failure`. Before any refusal, status returns `{"count": 0, "last": null}`. Refusals never add a ledger entry or consume budget; later attempts preserve this metadata. Accounting is best-effort: failure leaves the refusal message and exit `4` unchanged. It records the refusal, not whether the agent obeyed the action line.

## Session lifecycle

An explicit id selects that session without idle rollover. It must match `[A-Za-z0-9._-]{1,64}`, must not be `.`, and must not contain `..`. Otherwise, the broker reads `current.json` and reuses its valid id when the offset-aware `last_activity` is less than `SENIOR_SESSION_IDLE_MINUTES` old (default 240 minutes). Missing or malformed pointer data, an expired idle window, or `--new-session` causes a fresh local-time id of the form `YYYYMMDD-HHMMSS`; occupied ids are skipped one second at a time.

A recorded attempt or policy refusal refreshes `session.json` and `current.json`; local checks do not refresh activity. Refusal activity helps auto-detection find the selected session. It does not extend the window when an entry has a usable `finished` or `started`; when all entries are undatable, refreshed `session.json` metadata can extend the window through the fallback above. On a real call, `--new-session` writes the current pointer after packet validation, before provider selection. `--status --new-session` only previews an id and does not persist it. `--lint`, `--doctor`, and `--dry-run` never start a session on disk.

To force a new session, the operator supplies `--new-session` on the next real consultation with no explicit session id in flags or environment, or chooses an unused `--session-id` (or `SENIOR_SESSION_ID`). `--new-session` conflicts with either explicit-id source even when its value is empty. There is no standalone reset operation. A new session has fresh local history, refusal counters, and repeated-failure state; window budget, duplicate, and interval checks still include other active sessions under the same root. The agent must never use a session change to evade a refusal.

## The three bypass flags

- `--allow-sensitive` disables only the likely-secret guard; it does not relax structure, narrative, size, or policy checks.
- `--allow-narrative` disables the entire packet gate: structure, opening/header rules, prose/evidence/packet budgets, and narrative detection. The separate input-byte limit, empty-packet rejection for a real call, likely-secret guard, and policy guards remain.
- `--allow-repeat` disables only the duplicate guard; policy, budget, interval, and repeated-provider-failure checks remain.

All three exist for a human who has reviewed the packet. The agent must never pass them. Bypasses are recorded on logged attempts; none grants permission to send unsafe material.

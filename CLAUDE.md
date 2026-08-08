# Task Check In — Project Instructions

This project follows the global `AGENTS.md` and `SECURITY_BASELINE.md`.
The notes below cover only what's specific to this repository.

## Version identifiers

This project's version appears in three places — bump whichever
actually changed:

- `public/index.html` — the `<title>` ("Task Check In vX.Y.Z")
- `package.json` — the `version` field
- `api/index.py` — the `APP_VERSION` constant

## Running locally

Requires a `-DEMO` org token, either via:
- A local `.token` file (gitignored) containing a raw Bearer token, or
- `MANHATTAN_PASSWORD` / `MANHATTAN_SECRET` env vars (OAuth).

```
npm install
pip install -r requirements.txt
vercel dev
```

or, running the two processes separately:

```
python api/index.py        # Flask API on :5000
node server.js              # static + proxy on :3012
```

## Task Management domain — confirmed read, unconfirmed write

The real MAWM component is named plainly `task` (confirmed via
`cloudComponent: com-manh-cp-task-1` in live responses), not
`task-management` as first guessed — `mawm_api_library` still doesn't
document this domain, so this app is the first place it's captured.

**Confirmed live** against `SS-DEMO` with `IBPWIBPT0929` (a Putaway
task) — safe, read-only, verified working end to end:
- `search_task()` → `POST task/api/task/task/search` — same
  `{Query,Page,Size}` convention as every other MAWM object; the
  response nests the full line array under `TaskDetail[]`, so one call
  loads a task with its lines (no separate detail call needed).
- `search_task_transactions()` → `POST task/api/task/transaction/search`
  — filter by `TransactionTypeId`, not `TaskType` (the Task object has
  no `TaskType` field at all).
- The Task record carries its own `TransactionId` (e.g. `"Putaway"`)
  directly — `load_task()` returns it as `taskTransactionId`, and
  `preload_task_transactions()` prefers it as the dropdown default
  over the static `DEFAULT_TRANSACTION_BY_TASK_TYPE` guess.

**Still unconfirmed**: `complete_task()` (`task/api/task/task/completeTask`,
URL and payload both guessed). This one was deliberately *not* probed
live like the two calls above — it mutates real state (moves a
container, marks a task detail complete), so a wrong guess risks
corrupting `SS-DEMO` test data rather than just 404ing. Don't call it
against a real environment without either a real RF-session capture to
correct it against first, or explicit sign-off to probe it live.

**Open question, not yet resolved**: `TaskDetail.UomTypeId` (e.g.
`"LPN"`) does not appear to be a literal per-unit UOM for `Quantity`
(240 in the test task) the way `receivingworkbench` resolves ASN line
UOMs via `ItemPackage[]` — showing `"240 LPN"` would misread as "240
LPN containers." The frontend currently shows planned/completed
quantity as a bare number with no unit suffix for this reason;
`uomId` is still threaded through the API response for when this gets
resolved.

## Known-good test Task Id

`IBPWIBPT0929` (`SS-DEMO`, Putaway) — real, live-verified via
`load_task`. Treat it as read-only until `complete_task()` is
confirmed: completing it for real will consume `LPN00076`'s 240 units
out of `DROPR113` in the shared demo org.

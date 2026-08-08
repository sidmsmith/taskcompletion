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

**Confirmed-by-user-capture** (not independently probed live, but taken
from a real captured request/response set the user supplied):
- Putaway full completion (default destination) — `fetch_putaway_move()`
  + `commit_putaway_move()`, from
  `mawm_putaway_api_call_set_with_warning_handling.md`. Sets the fetched
  move's `CompletedQuantity` equal to its `Quantity` for a full
  completion.
- Putaway user-directed completion (operator overrides the destination
  in the grid) — `move_container_user_directed()`, from
  `mawm_user_directed_putaway_with_warnings.md`'s own "optional core API
  alternative" — chosen over that document's actually-proven stateful
  DMM Mobile Facade flow because this app's stateless per-request Flask
  backend has nowhere natural to hold a `workflowVO` session between
  scans. That document is explicit that the core alternative's own
  contract (including its warning shape) isn't confirmed, so treat this
  path as the least-confirmed piece in the app.
- Warning handling (`extract_warning()`) checks both documents' shapes:
  the standard `messages.Message[]` envelope and the DMM
  `workflowVO.header.state.errorVOList` shape (the source of the
  `PTW::119`/`DCI::120`/`PTW::120` examples). Confirming a warning by
  resubmitting with a `{code: code}` `userInputs` map is proven for the
  DMM flow specifically — extending that same mechanism to the two core
  endpoints above is an extrapolation, flagged as such in each
  function's docstring.

**Still unconfirmed**: `complete_task()` (`task/api/task/task/completeTask`,
URL and payload both guessed — the original, generic placeholder for
non-Putaway task types). Deliberately *not* probed live — it mutates
real state, so a wrong guess risks corrupting `SS-DEMO` test data rather
than just 404ing. Don't call it against a real environment without
either a real RF-session capture to correct it against first, or
explicit sign-off to probe it live.

**Reminder — reason codes not implemented.** Neither Putaway completion
document captures a reason-code prompt anywhere in its flow, but a user
overriding a system-directed destination (the editable "To Location"
grid field) is exactly the kind of action MAWM often requires one for.
This is a known, deliberate gap — not addressed until these workflows
have been exercised live and it's clear whether/where MAWM actually asks
for one. Revisit before treating the user-directed flow as complete.

**Open question, not yet resolved**: `TaskDetail.UomTypeId` (e.g.
`"LPN"`) does not appear to be a literal per-unit UOM for `Quantity`
(240 in the test task) the way `receivingworkbench` resolves ASN line
UOMs via `ItemPackage[]` — showing `"240 LPN"` would misread as "240
LPN containers." The frontend currently shows planned/completed
quantity as a bare number with no unit suffix for this reason;
`uomId` is still threaded through the API response for when this gets
resolved.

## Known-good test Task Id

`IBPWIBPT0929` (`SS-DEMO`, Putaway) — already **fully completed**
(`Status: 8000`/Completed) as of this writing, from earlier live
testing of the system-directed flow. It's still useful to confirm
`load_task` and the status badge, but a **fresh, not-yet-completed**
Putaway task is needed to actually exercise Complete Line / Complete
All (system- or user-directed) end to end.

## Status badge

`taskStatusLabel` (Tier 1, `mawm_api_library/_conventions/statuses.json`
domain `task_status`) drives a vasexecution-style soft-chip badge
(`public/app.js`'s `statusBadgeClass()`/`statusBadgeHtml()`, CSS in
`public/index.html`). Current rule, per explicit instruction: Completed
and Canceled render red, everything else green — a placeholder, not a
final design; revisit if the status set needs more granularity later.

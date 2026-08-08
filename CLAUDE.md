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
- Putaway Substitute Location (operator edits the destination in the
  grid) — still the same `fetch_putaway_move()` + `commit_putaway_move()`
  sequence ("directed putaway is still used"), with the fetched move's
  `ToLocationId`/`ReasonCodeId` overridden before committing, per
  `mawm_substitute_location_to_user_directed_putaway.md`. A reason code
  is required — `search_putaway_reason_codes()` backs a
  required-selection modal (`public/app.js`'s `promptReasonCode()`)
  shown before the completion call is made. **CONFIRMED live, but at a
  different URL than the document guessed**: its
  `GET /api/putaway/config/services/reasonCodes/list` (hedged there as
  "typically GET", no method actually captured) 404'd; the real
  endpoint is `POST /putaway/api/putaway/reasonCode/search` — the same
  standard search convention every other confirmed object in this app
  uses. Returns the same four codes the document's DMM-facade lookup
  captured, but under `ReasonCodeId`/`Description`, not the document's
  `key`/`value` (that shape belongs to the DMM facade's own lookup
  response, not this core search endpoint).

  **UNCONFIRMED (the substitution itself)**: the document only captures the DMM
  Mobile Facade's stateful equivalent of this substitution
  (`SubstituteLocation` → `EnterReasonCodeForSubstituteAction` →
  `AcceptLocationForUserDirectedPutaway`, each carrying a `workflowVO`);
  it never captures the core `commitAndFetchNextMove` payload with an
  overridden `ToLocationId`/`ReasonCodeId` directly, so putting a
  `ReasonCodeId` field on `InventoryMove` is this app's own extrapolation
  (see `_complete_putaway_line_system_directed()`'s docstring). If MAWM
  rejects this shape, the DMM stateful flow is the documented fallback.
- Warning handling (`extract_warning()`) checks both documents' shapes:
  the standard `messages.Message[]` envelope and the DMM
  `workflowVO.header.state.errorVOList` shape (the source of the
  `PTW::119`/`DCI::120`/`PTW::120` examples). Confirming a warning by
  resubmitting with a `{code: code}` `userInputs` map is proven for the
  DMM flow specifically — extending that same mechanism to the core
  commit endpoint is an extrapolation, flagged as such in the relevant
  docstrings.

**Superseded, kept commented out for reference** — the earlier
task-independent "user directed putaway" approach
(`mawm_client.move_container_user_directed()`,
`POST putaway/api/putaway/execution/container/move`, from
`mawm_user_directed_putaway_with_warnings.md`'s core API alternative)
worked when tested, but was replaced by the Substitute Location +
reason-code flow above per explicit instruction ("directed putaway is
still used" is the more proper flow). Not deleted — commented out in
both `mawm_client.py` (the function + its URL/transaction-id constants)
and `task_service.py` (`_complete_putaway_line_user_directed()`) in case
it's needed again.

**Still unconfirmed**: `complete_task()` (`task/api/task/task/completeTask`,
URL and payload both guessed — the original, generic placeholder for
non-Putaway task types). Deliberately *not* probed live — it mutates
real state, so a wrong guess risks corrupting `SS-DEMO` test data rather
than just 404ing. Don't call it against a real environment without
either a real RF-session capture to correct it against first, or
explicit sign-off to probe it live.

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
All end to end.

The Substitute Location capture used `IBPWIBPT0105` / `LPN00760`
(`STAGIB0204` → `C1CS0110`, substituted with reason `Damaged Location`)
— also real test data, but from the DMM stateful flow, not this app's
core-API extrapolation; not independently verified as still outstanding
in `SS-DEMO`.

## Status badge

`taskStatusLabel` (Tier 1, `mawm_api_library/_conventions/statuses.json`
domain `task_status`) drives a vasexecution-style soft-chip badge
(`public/app.js`'s `statusBadgeClass()`/`statusBadgeHtml()`, CSS in
`public/index.html`). Current rule, per explicit instruction: Completed
and Canceled render red, everything else green — a placeholder, not a
final design; revisit if the status set needs more granularity later.

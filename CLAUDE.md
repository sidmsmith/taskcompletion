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

  **CONFIRMED live, 2026-08-08**: the substitution itself. The document
  only ever captured the DMM Mobile Facade's stateful equivalent
  (`SubstituteLocation` → `EnterReasonCodeForSubstituteAction` →
  `AcceptLocationForUserDirectedPutaway`), never the core
  `commitAndFetchNextMove` payload with an overridden
  `ToLocationId`/`ReasonCodeId` directly — so putting a `ReasonCodeId`
  field on `InventoryMove` was this app's own extrapolation (see
  `_complete_putaway_line_system_directed()`'s docstring), and it works:
  tested against task `IBPWIBPT0221` (container `LPN000000000315`,
  item `3000223`, qty 50), substituting the planned destination
  `A1AC0114` for `A1AC0119` with reason `Damaged Location`. `success:
  true`, and independently confirmed — not just trusted — by
  re-querying: the task shows `Completed`/`50 of 50`, and a direct
  inventory search for `LocationId ='A1AC0119' and ItemId ='3000223'`
  returns the full 50 units on hand there (the source container itself
  goes empty, `CurrentLocationId: null`, which is expected — its
  contents moved to the destination location, not to a new container).
- Warning handling (`extract_warning()`) checks both documents' shapes:
  the standard `messages.Message[]` envelope and the DMM
  `workflowVO.header.state.errorVOList` shape (the source of the
  `PTW::119`/`DCI::120`/`PTW::120` examples). Confirming a warning by
  resubmitting with a `{code: code}` `userInputs` map is proven for the
  DMM flow specifically — extending that same mechanism to the core
  commit endpoint is an extrapolation, flagged as such in the relevant
  docstrings.
- **Bug fixed 2026-08-08**: `fetch_putaway_move()`/`commit_putaway_move()`/
  `move_container_user_directed()` all raised a hard `RuntimeError` on
  any non-2xx HTTP status *before* the caller ever got to run
  `extract_warning()` on the body — and MAWM returns at least some
  warnings over a non-2xx status (confirmed: `LPN00953`'s no-task
  Substitute-Location test returned `DCI::120`, "Location permanently
  dedicated to a different item," the same code the DMM document
  captured, via the standard `messages.Message[]` envelope — but over a
  non-200 status, so it was surfacing as a raw error instead of the
  Confirm/Cancel modal). All three now always return the parsed body
  and let the caller's existing warning/success check decide, instead
  of raising early. Only a genuinely unparseable response still raises.
  This confirms the core API *does* use the standard envelope for at
  least one of the DMM-documented codes — evidence the extrapolation
  above is on the right track, though only for the read side of that
  flow; the override-and-actually-complete side is still unverified.
- **CONFIRMED WRONG, 2026-08-08**: the `userInputs: {code: code}`
  override, resubmitted against `LPN00953`'s `DCI::120` warning, did
  **not** clear it — MAWM returned the identical warning again. Both
  source documents hedged that the core endpoint's override contract
  might not match the DMM flow's; this settles it for `container/move`
  specifically (untested on `commitAndFetchNextMove`). A dedicated-
  location warning currently has no way to be overridden through this
  app. Real next step: capture what a successful override actually
  looks like from the mobile RF client for this exact scenario — same
  as how every other endpoint in this app got confirmed. Until then,
  `extract_message()` (added the same day) at least surfaces the real
  business message (`"Location permanently dedicated to a different
  item"`) instead of the unhelpful top-level `"error.400"` when this
  happens.

**Superseded a second time, 2026-08-08** — `mawm_client.move_container_user_directed()`
(`POST putaway/api/putaway/execution/container/move`) was briefly
revived for the no-task/iLPN container case, but its own warning
override was confirmed not to work (same as on an existing task), so
it's commented out again. The no-task container case now goes through
the DMM Mobile Facade flow below instead — same mechanism, same
override, already proven live end to end. `task_service
._complete_putaway_line_user_directed()` — the old orchestration that
called it for the on-task case — also remains commented out.

**CONFIRMED live end-to-end, 2026-08-08** — the DMM Mobile Facade
"User Directed Putaway" flow now backs `task_service
.complete_container_putaway()` (the no-task/iLPN container case, see
"iLPN search" below): `mawm_client.workflow_init()` →
`workflow_execute()` (`AcceptContainerForUserDirectedPutaway`, then
`AcceptLocationForUserDirectedPutaway`), captured start-to-finish from
a real mobile RF session's HAR. Two real, non-header findings from
building this:
- **Root cause of an early `serverError`**: the scanned input for each
  step (`scannedContainerBarcode`, `scannedLocationBarcode`) is **not**
  a separate top-level sibling field in the request body — an
  extrapolation from the source document assumed
  `{"workflowVO": ..., "scannedContainerBarcode": "..."}` and the
  server silently ignored it, returning a generic
  `"serverError": "An unexpected system error occurred."` with no
  other diagnostic info. Confirmed via direct HAR body inspection: the
  value lives *inside* `workflowVO.header.state.scannedContainerBarcode`
  (a hidden field on the state, alongside `warningOverrideList`),
  mutated the same way as the warning override, then the whole
  `workflowVO` resubmitted. `workflow_execute()` no longer takes an
  `extra_fields` parameter for this reason — the caller must set the
  field on `workflow_vo["header"]["state"]` before calling it.
- Headers were a dead end for this bug — `workflow_init()`'s headers
  (which already worked) matched `AcceptContainer`'s almost exactly;
  no missing session cookie or auth header was involved. Worth
  remembering for next time a DMM step fails with an opaque
  `serverError`: check the request **body** shape against a real HAR
  capture before suspecting headers/session state.
- Verified with two real round-trip moves of `LPN00953` between
  `R2R40106` and `R2R40105` through `task_service
  .complete_container_putaway()` directly (bypassing the Flask route,
  which is a thin pass-through) — both succeeded and were confirmed by
  re-querying the iLPN's actual current location afterward, not just
  by trusting `success: true`.
- **CONFIRMED live, 2026-08-08 (second session)**: the warning-and-
  override path for this exact flow. `LPN000000000010` (no current
  location — a container that had never been putaway) to `R2R61001`
  returned `DCI::159` ("No item assignment exists for location
  R2R61001") at the `AcceptLocationForUserDirectedPutaway` step on the
  first attempt; resubmitting with `warningOverrideList: ["DCI::159"]`
  returned `success: true`, and the iLPN's current location was
  confirmed (by re-query, not just the response) to have actually
  changed from `null` to `R2R61001`. Same override mechanism already
  proven for Substitute Location, now proven for this flow too.

## iLPN search (2026-08-08)

The search box now accepts a Task Id **or** an iLPN — one field,
auto-detect (`task_service.resolve_search()`):

1. Try the value as a Task Id.
2. Else, try it as a container: resolve its open (not Completed/
   Canceled) Putaway task, if one exists — **CONFIRMED live**:
   `TaskDetail.SourceContainerId ='{id}' or TaskDetail.TargetContainerId
   ='{id}'`, combined with `TransactionTypeId ='Putaway' and Status
   !='8000' and Status !='9000'`, on the same `task/api/task/task/search`
   endpoint (the nested `TaskDetail.<field>` dotted-path filter works on
   the header search, mirroring `mawm_api_library`'s documented
   `AsnLine.PurchaseOrderId` pattern for a different object; `Status not
   in (...)` was tried and rejected with a 400 — chained `!=` works).
   The `TransactionTypeId` filter matters: a real SS-DEMO container had
   both an open Putaway task and an unrelated open "LPN Disposition"
   task on it.
3. Else, if it's at least a real iLPN, return `mode: "no_task"`: a
   synthetic single line with Current Location = the iLPN's real
   `CurrentLocationId` (**CONFIRMED live**, `dcinventory/ilpn/search`)
   and Item/Description/Qty from its actual on-hand inventory
   (**CONFIRMED live**, `dcinventory/inventory/search` — same
   endpoint/shape `receivingworkbench` already uses). To Location starts
   blank; fails cleanly if the LPN holds more than one item (mixed-LPN
   putaway isn't handled).
4. Completing that synthetic line goes through
   `complete_container_putaway()` → the DMM Mobile Facade "User
   Directed Putaway" flow (see the confirmed section above) — **no
   reason code required** here (unlike Substitute Location: there's no
   system-directed default being overridden). **CONFIRMED live,
   2026-08-08**: both the plain-success and the warning-and-override
   paths, with real inventory moves verified by re-query, not just
   `success: true` — see above.

**To Location validation now applies everywhere, always** (per explicit
instruction, not just the no-task case), and is now preloaded rather
than checked live per keystroke (**changed 2026-08-08**, per explicit
instruction): `mawm_client.search_all_storage_locations()` /
`task_service.preload_putaway_locations()` (**CONFIRMED live** —
`dcinventory/location/search`, `LocationTypeId ='STORAGE' and
IsActive=true`, unscoped by LocationId/DisplayLocation, `Size: 5000` —
`SS-DEMO-DM1` returned **3,832** real rows, comfortably under that cap;
if a facility ever needs more than 5000, this will silently truncate
and needs a Page loop added) is called once per session, right after
`authenticate()` succeeds, fire-and-forget
(`public/app.js`'s `preloadStorageLocations()`). It builds a plain
uppercased `Set` of every `LocationId`/`DisplayLocation`, and
`validateLocation()` checks that Set synchronously on every keystroke —
genuinely real-time, no network round trip, no debounce. The original
per-keystroke live call (`validate_storage_location()` /
`task_service.validate_putaway_location()` / `/api
/validate_putaway_location`) is kept as-is, now only as a fallback
inside `validateLocation()` for the brief window before the preload
resolves (or if it fails outright) — still debounced 400ms in that
case, same as before. Gates all 3 completion buttons — Partial/Complete
Line need the *selected* line's destination valid; Complete All needs
every *outstanding* line's destination valid. Partial Complete is also
unconditionally disabled in `no_task` mode — it isn't wired for the
container flow (`complete_container_putaway()` always moves the full
on-hand quantity, no partial-quantity concept).

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

## Multi-LPN search, editable Completed Qty, LPN column (2026-08-08, third session)

Three related UI/API changes, delivered together per explicit instruction:

- **LPN column** moved between Line and Item in both the table header
  and `renderGroups()`'s row markup — purely cosmetic, no API change.
- **Completed Qty is now an editable inline box** (`public/app.js`'s
  `.completed-qty-input`), defaulted to the line's remaining quantity,
  same "only send if changed" override contract as Current Location
  (`getCompletedQtyOverride()` mirrors `getLocationOverride()` exactly)
  and the same client-side validity gating
  (`validateQty()`/`isQtyValid()`, purely arithmetic — `0 < qty <=
  remaining` — no API call needed, unlike location validation). The
  separate **Partial Complete button/modal was removed** — editing this
  box down is now how a partial is expressed, on the same single
  "Complete Line" button. Locked (`disabled`) for `no_task`/container
  rows, which always move the full on-hand quantity (no partial concept
  there — `complete_container_putaway()`'s DMM flow doesn't take an
  Item/Quantity input at all).

  **CONFIRMED NOT to work, live**: booking a genuine partial Putaway
  completion this way. Tested against `IBPWIBPT0929`
  (`CompletedQuantity: 100` of `Quantity: 240`) — MAWM's core
  `commitAndFetchNextMove` endpoint (Path C) rejected it outright:
  *"Quantity entered is less than the system quantity."* This endpoint
  requires the full system quantity; there is currently no known way to
  book a genuine partial Putaway completion. The box/button are kept
  wired anyway (not reverted) so this real rejection reaches the
  frontend instead of silently never being attempted — consistent with
  this app always letting MAWM be the final word rather than guessing
  client-side. See `mawm_client.commit_putaway_move()`'s and
  `task_service._complete_putaway_line_system_directed()`'s docstrings
  for the full story.

- **Multi-LPN search** (`task_service.resolve_search_multi()`): the
  search box now accepts more than one Task Id/iLPN, delimited by `;`,
  `,`, or whitespace. Each token resolves independently via the
  existing `resolve_search()` — one may have an open task, another may
  not, another may not exist at all (collected into `notFound` rather
  than aborting the whole search). Every line is denormalized with its
  owning group's identity (`groupMode`/`groupTaskId`/
  `groupContainerId`/`groupTaskStatusLabel`/etc.) so the frontend can
  treat the combined line list as flat and self-describing — no
  cross-referencing back into a `groups` array anywhere in
  `public/app.js`. `taskDetailId` was already globally unique across
  groups (real GUIDs for tasks, `container:{id}` for no-task
  containers), so row selection/keying just switched from `lineNumber`
  (not unique across groups) to `taskDetailId` — this also simplified
  the single-group case, which is now just `groups.length === 1`
  instead of a separate code path. `/api/load_task` always returns this
  `groups` shape now, even for one match — one response shape for the
  frontend to handle instead of two.

  **Complete All now spans every group on screen**, not just one
  task/container (per explicit instruction). The confirmation modal
  prefixes each line with its owning Task/Container when more than one
  group is loaded.

  **Task/Container column**: shown in the table only when more than one
  group is loaded (`#linesTable`'s `multi-group` CSS class, toggled by
  `renderGroups()`) — single-group results look exactly as before.
  `renderTaskMeta()`'s header does the same: unchanged for one group,
  a plain count summary for multiple. This is an explicitly interim
  default — final design (whether to keep both a header and a column,
  how picking vs. cycle count should differ) is deliberately deferred.

  **CONFIRMED live end-to-end** (2026-08-08, real `SS-DEMO` data, via
  an actual browser session against the local dev server — not just
  direct Python calls): search `LPN00763, LPN000000000010` (a fresh
  task-mode line + a no-task container) loaded both groups correctly,
  the no-task row's Completed Qty was correctly locked, and running
  **Complete All** across both — with a `DCI::120` warning firing
  mid-loop on the no-task line and correctly pausing for Confirm — both
  lines resolved (one success, one real failure, see below), each
  independently verified against live inventory/task state afterward.

  **Two real bugs found and fixed during this browser session** (both
  pre-existing, not introduced today — confirmed via `git log` that the
  first predates this session entirely):
  - `actionStatus` was being cleared immediately after a completion's
    success/error message was set, because `renderLines()`/
    `renderGroups()` unconditionally cleared it on every render,
    including the automatic refresh after a completion
    (`reloadCurrentSearch()`). The message would flash and vanish
    before ever being visible. Fixed by moving the clear into
    `loadTask()` (a genuinely new search) instead of `renderGroups()`
    (which now also runs on post-completion refreshes).
  - A failed `fetch_putaway_move()` call (Path C, Call C1) that comes
    back as a hard MAWM `ERROR` (not a `WARNING` — `extract_warning()`
    correctly lets those through separately) with a real business
    message was being silently replaced by a generic "No putaway move
    returned for this task." Confirmed live: `IBPWIBPT0109` returned
    `FWTSK::019`, *"Task IBPWIBPT0109 cannot be assigned to user
    demoweb@ss-demo"* (a real task-assignment conflict in `SS-DEMO`,
    not a code bug — same class of issue noted for this exact task
    earlier in the project) — the app showed the generic fallback
    instead. Fixed by preferring `extract_message()`'s real text when
    the response actually has one, same pattern already used elsewhere
    in this module.

## Known-good test Task Id

Refreshed 2026-08-08 (third session) — the demo environment's data
drifts as tests consume it (and appears to periodically reset/reseed
independently — several tasks below have flipped status more than once
across sessions without this app changing anything), so treat
"current" state here as a snapshot, not a guarantee; re-check live
before trusting an old note over what MAWM actually returns.

**Fully spent (Completed) as of this writing** — need a fresh task to
exercise Complete Line / Complete All again:
- `IBPWIBPT0929` (`LPN00076`, item `50002217`, 240 units → `R1R20701`)
- `IBPWIBPT0221` (`LPN000000000315`/`A1AC0114`, item `3000223`, 50
  units → `A1AC0119`, Substitute Location)

**Known live data conflict, not a code bug**: `IBPWIBPT0109`
(`LPN00763`, 10 units remaining) reads fine (`Status: Ready For
Assignment` via `resolve_search`), but its Path C fetch
(`fetchNextPutawayMoveAndStartLaborActivity`) returns `FWTSK::019`,
*"Task IBPWIBPT0109 cannot be assigned to user demoweb@ss-demo"* —
confirmed live, 2026-08-08. Don't use this task for write-path testing
until that clears; it's exactly the kind of thing
`extract_message()`'s fallback fix (above) was for, not something this
app can work around.

**No open task (`mode: "no_task"`), useful for the container/DMM
flow**: `LPN00076` (now that `IBPWIBPT0929` is Completed — its task no
longer counts as "open" — current location `R1R20701`, following the
completion above) and `LPN000000000010` (current location `R2R40105`,
following live Complete-All testing above; previously used for the
`DCI::159`/`DCI::120` warning-override tests — has now cleared
whatever dedicated-item conflict `R2R61001`/`R2R40105` had, so it may
no longer reproduce a warning at those specific destinations).

## Status badge

`taskStatusLabel` (Tier 1, `mawm_api_library/_conventions/statuses.json`
domain `task_status`) drives a vasexecution-style soft-chip badge
(`public/app.js`'s `statusBadgeClass()`/`statusBadgeHtml()`, CSS in
`public/index.html`). Current rule, per explicit instruction: Completed
and Canceled render red, everything else green — a placeholder, not a
final design; revisit if the status set needs more granularity later.

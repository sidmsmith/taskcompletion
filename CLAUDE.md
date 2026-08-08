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

**RESOLVED, 2026-08-08 (fifth session)** — see the "UOM display and
conversion" section further down: `TaskDetail.UomTypeId` is a code into
the item's `ItemPackage[]`, not a display label, and `Quantity` really
is base-unit and needed real conversion, not just a label fix.

## Multi-LPN search, editable Completed Qty, LPN column (2026-08-08, third session)

Three related UI/API changes, delivered together per explicit instruction:

- **LPN column** moved between Line and Item in both the table header
  and `renderGroups()`'s row markup — purely cosmetic, no API change.
- **Completed Qty is now an editable inline box** (`public/app.js`'s
  `.completed-qty-input`), defaulted to the line's remaining quantity,
  same "only send if changed" override contract as Current Location
  (`getCompletedQtyOverride()` mirrors `getLocationOverride()` exactly).
  The separate **Partial Complete button/modal was removed** — editing
  this box is now how a different quantity is expressed, on the same
  single "Complete Line" button.

  **Superseded the same day, see the "Modify iLPN" section below**:
  the mechanism this originally shipped with — sending the edited value
  straight through as the Path C commit's `CompletedQuantity` — was
  confirmed live NOT to work (MAWM's core `commitAndFetchNextMove`
  rejects `CompletedQuantity < Quantity` outright: *"Quantity entered is
  less than the system quantity"*) and has been fully reverted, not
  just left disabled. An edited Completed Qty now triggers a real
  inventory correction (Modify iLPN) *before* the normal, unmodified
  completion call, which is a materially different (and — for the
  no-task/container case — confirmed working) mechanism.

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

## Modify iLPN adjustment, MIXED accordion, Task/Container column move (2026-08-08, fourth session)

The real mechanism for a different Completed Qty, replacing the
same-day-earlier (confirmed-broken) direct-`CompletedQuantity` attempt
described above. Source: `mawm_modify_ilpn_query_and_adjustment.md`
(a real captured Postman test set) plus `modifyLPN-single.har`
(reason-code lookup only — the DMM ModifyIlpn mobile workflow itself
was explicitly out of scope, per instruction).

**The mechanism** — `task_service.adjust_ilpn_quantities()`:
correct the LPN's actual on-hand inventory *first* via
`POST inventory-management/api/inventory-management/adjust/endIlpn`,
*then* run the normal, unmodified completion, which re-fetches fresh
and so picks up whatever quantity MAWM now considers correct.
- `ScannedQuantity` on the wire is a *relative* adjustment
  (`New OnHand = Current + (ScannedQuantity - ExpectedOnHandQuantity)`),
  but since this always re-queries live inventory immediately before
  building the payload, `Original == Expected == current OnHand` here,
  which collapses `ScannedQuantity` to simply the desired new total —
  see the function's docstring for the full reasoning.
- Unchanged items are omitted from the payload entirely (per the
  document — "don't include unrelated lines"), not sent with a
  no-op adjustment.
- A quantity of exactly 0 is sent as a normal line
  (`ScannedQuantity: 0`), **not** through the document's
  `DeletedInventory` path — per explicit instruction, based on the
  document author's own live testing that this removes the line the
  same way. `adjust_ilpn_inventory()` keeps `DeletedInventory` as an
  unused parameter in case that ever needs wiring in instead.
- **CONFIRMED WRONG, live**: the assumption (stated in the original
  request) that a null `InventoryReadTimestamp` only comes up when
  adding a brand-new line. Checked two real, pre-existing (nothing
  being added) containers live: one had a real timestamp, the other —
  `LPN000000000010` — came back null. So `adjust_ilpn_quantities()`
  always checks for this and calls `refresh_ilpn_read_timestamp()` +
  re-queries once whenever a targeted line needs it, rather than
  skipping that step. Adding a genuinely new line (a row that doesn't
  exist in inventory at all yet) is still out of scope and correctly
  refused with a clear error — there's no existing row to read *any*
  timestamp from, refreshed or not; the document's own approach for
  that case is to send a literal null timestamp directly, which this
  function deliberately doesn't attempt.
- `endIlpn` is asynchronous (may return HTTP 200 with an empty body) —
  `adjust_ilpn_quantities()` waits ~1.5s then re-queries once to verify
  the new on-hand actually matches what was requested, returning
  `success: False` with the mismatched item ids if it doesn't yet
  (treat as "try again shortly," not a hard failure).

**CONFIRMED live end-to-end**, all against real `SS-DEMO` data,
independently re-verified by direct re-query (not just trusting a
`success: true`):
- Basic increase: `0000099999000008672` item `6000106`, 9 → 10 units.
- Qty-0 deletion: same container, item `6000105`, 5 → 0 — the line was
  fully removed from inventory search results, confirming the
  qty-0-instead-of-`DeletedInventory` approach works exactly as the
  document's author found.
- Null-timestamp refresh path: `LPN000000000010` item `5000225`, real
  pre-existing line, null timestamp → refresh → retry succeeded, 200 →
  195 units, timestamp now populated.
- **No-task/container completion with an adjustment, full click-driven
  browser test** (not just direct Python calls): `LPN000000000010`
  edited to 190 units + destination `R2R40106` in the actual UI,
  Complete Line clicked, a real `DCI::120` warning appeared and was
  confirmed, and the completion succeeded — independently verified
  both the on-hand quantity (195 → 190) and the container's current
  location actually changed.
- **MIXED container, adjustment down to a single item, then full
  completion**: `0000099999100000772` (real mixed inventory found live,
  not fabricated — items `4000042`/`4000043`) — zeroed out `4000043`,
  left `4000042` unchanged (correctly skipped as a no-op), container
  correctly reduced to one item, then completed putaway to `R2R40105`
  (after confirming the same `DCI::120` warning) — confirmed both the
  adjustment and the final location live.

**UNCONFIRMED, explicitly deferred to the user to test live**: this
whole sequence against an LPN **already allocated to an open task**
(the `complete_putaway_line()` task-mode path). Everything above was
tested against no-task/container LPNs only, matching the source
document's own Postman testing scope. Whether
`fetchNextPutawayMoveAndStartLaborActivity` re-syncs its `Quantity` to
an adjusted on-hand, or still expects the task's original planned
amount, is genuinely unknown — see `complete_putaway_line()`'s and
`adjust_ilpn_quantities()`'s docstrings.

**Reason codes**: `mawm_client.ADJUSTMENT_REASON_CODES` — a static
list of 6 (`Charity/CH`, `Inventory Adjustment/IA`,
`Inventory Damaged/DM`, `Inventory Delete/ID`,
`Lost in cycle count/LC`, `Mass Inventory Movement/MM`), extracted from
a real captured lookup response, not a live search — that lookup
(`adjustmentReasonCodes/list`) only ever appeared nested inside an
active DMM ModifyIlpn workflow session in the HAR, and building that
whole workflow just to fetch six stable codes was out of scope.
Preloaded once per session via `/api/preload_adjustment_reason_codes`
(single source of truth in `mawm_client.py`, not duplicated in
`public/app.js` — mirrors the `preloadStorageLocations()` pattern).
Hardcoded default `IA`, always changeable. The dropdown
(`.reason-code-select`) only becomes visible next to a row once its
Completed Qty box is actually edited away from its default — same
"only show/send when it matters" pattern as everything else here.

**MIXED containers now expand into an accordion**
(`resolve_search()`'s no_task branch now returns `mixedItems:
[{itemId, description, quantity}, ...]` on the summary line instead of
just a total). `MIXED` itself moved from the Description column into
the Item column (Description now blank for that row), per explicit
instruction. Each item gets its own sub-row (`.mixed-item-row`,
`.mixed-qty-input`) with an independent Completed Qty box and reason
dropdown, defaulted to that item's own on-hand — collapsed by default,
toggled via the `▶ MIXED` button. The summary row keeps the *shared*
To Location input (one container, one destination) and shows the
aggregate Planned Qty read-only. Submitting sends every item's current
box value as one multi-entry `itemAdjustments` array (unchanged items
safely no-op server-side, no client-side "did it change" detection
needed for this case, unlike the single-item box).

**Multi-item putaway completion is now CONFIRMED supported, 2026-08-08
(fifth session)** — the "must resolve to one item first" restriction
described here in the fourth session was wrong and has been removed;
see `complete_container_putaway()`'s docstring for the full story (a
real HAR capture of the mobile RF client, `userdirectedmultiple.har`,
showed MAWM's own workflow state carries a `multiItemContainer: true`
flag and moves every item on a container to the same destination in
one call — no per-item ItemId/Quantity input was ever needed).
`item_adjustments` is still useful for correcting quantities beforehand,
just no longer required to complete a MIXED container. **Confirmed
live** against the exact real container from the original bug report,
`0000099999000008672` (items `6000106`/10 units, `8000145`/2 units) —
completed to `R2R40105` after confirming the same `DCI::120` warning
the mobile capture hit, both items independently verified at the new
location afterward.

**Completed Qty no longer disabled for no-task/container rows** — the
original disable-for-no-task reasoning ("the DMM AcceptContainer step
has no quantity input") is now moot: a different quantity is corrected
via Modify iLPN *before* AcceptContainer ever runs, so
`complete_container_putaway()` needed zero changes itself to support
this — it already re-queries live on-hand at call time regardless.

**Validity gating relaxed**: Completed Qty no longer has an upper bound
(`validateQty()`/`isQtyValid()`) — before this change, editing the box
could only ever express a *smaller* quantity on the same move, so it
was capped at "remaining." Now it triggers a real inventory correction
that can go either direction — finding *more* units than expected is
just as valid a correction as finding fewer.

**Column reorder**: Task/Container moved from last to right after LPN
(`Line, LPN, Task/Container, Item, Description, ...`), per explicit
instruction. A small UOM column was added between Planned Qty and
Completed Qty (no header text) — cosmetic only, `uomId`'s own
resolution/threading is unchanged (still blank for no-task lines, still
the raw `UomTypeId` value for task lines — see the "Open question"
above).

**One more real bug found and fixed** while live-testing the no-task
completion path in the browser: `complete_container_putaway()`'s
success response never included a `quantity` field at all (unlike the
task-mode path), so a successful completion showed "Completed
undefined  on line N" in the frontend instead of the real amount.
Fixed by including the actual post-adjustment on-hand quantity
(`rows[0]["OnHand"]`, already fetched for the single-item check) in the
response.

## UOM display and conversion, full-width results table (2026-08-08, fifth session)

**UOM display and conversion** — `task_service._package_conversion_factor()`,
ported from `receivingworkbench`'s already-confirmed `rw_service.py`
`_package_conversion_factor()`, per explicit instruction to reference
that app's logic rather than re-derive it. Resolves the real story
behind the "Open question" above: `TaskDetail.UomTypeId` (e.g. `"LPN"`)
is a *code*, not a label — `Quantity`/`CompletedQuantity` are always
base-unit, and the item's `ItemPackage[]` (now fetched by
`mawm_client.search_items()`, Template extended to match
receivingworkbench's exactly) holds the real conversion factor and
human label for that code. **CONFIRMED live**: item `50002217`
(`IBPWIBPT0929`) — raw 240 base units, `UomTypeId="LPN"` resolves to
`ItemPackage` entry `{Quantity: 24, UomId: "units"}` → displays as
`10 Units`, not the misleading raw `"240 LPN"`. Item `3000223`
similarly resolves its own "LPN" code to `Case`/factor 50 — its earlier
50-unit Substitute Location test line (this session's history) really
was exactly 1 Case.

This is a **real, bidirectional conversion**, not just a display fix —
matching how receivingworkbench's own `receive_line()` converts a
user-entered display-unit quantity back to base units before ever
calling MAWM. Every line now carries `uomFactor` alongside the already-
converted `uomId`/`plannedQuantity`/`completedQuantity`/
`remainingQuantity`; `public/app.js`'s Completed Qty inputs
(`.completed-qty-input`/`.mixed-qty-input`) carry `data-uom-factor`, and
`collectItemAdjustments()` multiplies the box's display-unit value back
up by that factor before it's ever sent as `desiredQty` — MAWM and
`adjust_ilpn_quantities()` only ever see base units. No-task/container
lines resolve their factor from the item's own `DisplayUomId` instead
of a per-line code (there's no "shipped as X" concept for on-hand
inventory) — including per-item for a MIXED container's accordion rows.

The UOM label itself shows in its own column between Planned Qty and
Completed Qty (no header text) — `public/index.html`'s `.col-uom`,
added in the fourth session but was rendering blank until this
conversion actually resolved a label to put there.

**Full-width results table**: `.app-shell`'s `max-width: 1200px` /
`margin: 0 auto` were removed (per explicit instruction — "running out
of real estate"). The filters/auth screen keeps its own narrower
centered width via its `.card-panel`'s own inline `max-width: 620px`,
so login/search still reads comfortably narrow while the results
table now uses the full viewport width minus the shell's existing
padding.

**Complete Line / Complete All buttons** moved from centered to
left-aligned (`.action-toolbar`'s `justify-content: center` →
`flex-start`), per explicit instruction.

## Required reason code, "Scan Another" retains search, mixed header UOM (2026-08-08, sixth session)

- **"Scan Another" (`showFilters()`) no longer clears the search box** —
  per explicit instruction ("especially for testing!"). Also selects
  the retained text so typing/scanning still immediately replaces it.
- **MIXED accordion summary row now shows a UOM** in its own column
  when every real item underneath happens to share the same one (blank
  if they differ, same as before) — `renderLineRow()` computes this
  from `line.mixedItems[].uomId` at render time, no backend change.
- **Reason code is now required, not defaulted** — `.reason-code-select`
  starts on a "Select Reason" placeholder (`value=""`), styled red via
  the same `.invalid` class/CSS every other input here already uses,
  instead of silently pre-picking `IA`. `isReasonValid()` (mirrors
  `isQtyValid()`) gates `Complete Line`/`Complete All` alongside
  location and quantity validity: a reason is only required once its
  row's Completed Qty is actually overridden (the select is invisible
  otherwise), but once shown, the placeholder blocks submission until a
  real code is chosen — checked independently per item for a MIXED
  container's accordion. A `change` listener toggles `.invalid` off
  once a real value is picked and re-evaluates button state.

  **Real bug caught live while testing this**: the `.mixed-qty-input`
  input handler toggled the reason select's visibility/styling but
  never called `updateLineActionButtons()` afterward (unlike the
  single-item box, where `validateQty()` already does) — so editing a
  MIXED item's quantity revealed an invalid reason select without
  actually disabling the buttons until something unrelated happened to
  re-evaluate them. Fixed by calling `updateLineActionButtons()`
  directly in that branch too. Confirmed live (buttons disabled on
  reveal, re-enabled the moment a real reason was picked) before
  shipping.

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
longer counts as "open" — current location `R1R20701`) and
`LPN000000000010` (item `5000225`, now 190 units at `R2R40106`,
following the Modify iLPN + Complete Line browser test above). Note
`R2R40105`/`R2R40106`/`R2R61001` all appear to be *destination
locations* dedicated to a specific item (each reliably reproduces
`DCI::120`, "Location permanently dedicated to a different item," for
these test items) — that warning is a property of the destination, not
something that "clears" on the source LPN over time.

**Modify iLPN / multi-item test containers**: `0000099999000008672`
(the source document's own test iLPN) — `6000105` was deleted via the
qty-0 test (fourth session), then item `8000145` reappeared on it
(added back via the user's own Postman testing, matching the
document's own add-line example) making it genuinely MIXED again; used
in the fifth session to confirm multi-item putaway completion works —
now single-item again, `6000106` (10 units) + `8000145` (2 units) both
at `R2R40105`. `0000099999100000772` was a real MIXED container (items
`4000042`/`4000043`) used to prove the accordion end-to-end — now
single-item (`4000042`, 3 units) at `R2R40105`, `4000043` zeroed out.
`0000099999000005596` is still genuinely MIXED (3 items —
`6000102`/`6000103`/`6000104`) and untouched, if another real MIXED
container is needed for testing.

## Status badge

`taskStatusLabel` (Tier 1, `mawm_api_library/_conventions/statuses.json`
domain `task_status`) drives a vasexecution-style soft-chip badge
(`public/app.js`'s `statusBadgeClass()`/`statusBadgeHtml()`, CSS in
`public/index.html`). Current rule, per explicit instruction: Completed
and Canceled render red, everything else green — a placeholder, not a
final design; revisit if the status set needs more granularity later.

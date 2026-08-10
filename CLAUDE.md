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

**The mechanism** — `task_service.adjust_ilpn_quantities()`: correct
the LPN's actual on-hand inventory via
`POST inventory-management/api/inventory-management/adjust/endIlpn`,
around the normal, unmodified completion (which re-fetches fresh and so
picks up whatever quantity MAWM now considers correct). **Superseded
for the task-mode path in the sixth session** — see "Task-mode quantity
correction: sequence reversed" further down: adjust-before-complete
only works for a no-task/container LPN; a task-mode line now adjusts
*after* completing, because an LPN still allocated to an open task
can't be adjusted at all. Everything below in this section (the
adjustment mechanism itself, its confirmed live tests, the reason
codes, the MIXED accordion) is unchanged and still accurate — only the
task-mode *ordering* moved.
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

## Task-mode quantity correction: sequence reversed, two-API branch (2026-08-08, sixth session)

**Per explicit domain-expertise instruction**: an LPN still allocated
to an open task cannot be adjusted via Modify iLPN at all. It only
works once the task releases the LPN, which for Putaway means *after*
it's been put away, not before. This is why the allocated-LPN test kept
failing/wasn't reachable — `adjust_ilpn_quantities()` was being called
on an LPN the task still owned.

**`complete_putaway_line()`'s sequence is reversed**: complete the
full, unmodified putaway first (`desired_qty` plays no part in that
call), and only *after* it succeeds, correct the quantity. If putaway
fails or hits a warning, that's returned exactly as before (no
adjustment ever runs). If putaway succeeds but the *adjustment*
afterward fails, the response is `"success": True` (the putaway really
happened, can't be hidden) plus a separate `"adjustmentSuccess": False`
/ `"adjustmentError"` pair (and `"adjustmentTarget"`, `"lpn"` or
`"location"` — see below) — `public/app.js`'s
`completeLine()`/`confirmAllLines()` surface that as its own distinct
message ("completed, but the quantity correction failed") rather than
either a misleading plain success or failure.

**Which of two APIs to use is decided automatically — CONFIRMED live,
correcting an earlier wrong assumption**: `LocationTypeId='STORAGE'`
(the only destination type `validate_storage_location()` allows) does
**not** by itself mean the destination keeps LPN-level inventory — an
earlier note here claimed "every destination reachable through this
app today is in-scope" for Modify iLPN, which was wrong.
`Location.InventoryReservationTypeId` ("LPN" vs "LOCATION") is a
genuinely independent property from `LocationTypeId`; confirmed live
that `A1AC0114`/`C1CS0110` are both `LocationTypeId='STORAGE'` but
`InventoryReservationTypeId='LOCATION'` — putaway there consumes the
LPN into the location's own inventory record, same as a Pick location
would.

Rather than pre-checking the destination location's config, the app
checks the *LPN itself* after putaway — confirmed live as a clean,
reliable signal:

| LPN | Destination `InventoryReservationTypeId` | Post-putaway `Ilpn.Status` |
|---|---|---|
| `LPN000000000315` → `A1AC0114` | `LOCATION` | **9000 ("Consumed")**, `CurrentLocationId` → `null` |
| `LPN000000000010` → `R2R40105` | `LPN` | 3000 ("Not Allocated"), still a live container |
| `LPN00076` → `R1R20701` | `LPN` | 3000 |
| `0000099999000008672` → `R2R40105` | `LPN` | 3000 |

`search_ilpn_current_location()`'s Template now also fetches `Status`
(`mawm_api_library`'s `ilpn_dc_inventory_status` domain — `9000` =
"Consumed", `3000` = "Not Allocated"; that library documents the status
ladder but has no prose anywhere explaining `IsClosed`,
`ConsumeOnLocate`, or PICK/STORAGE/RESERVE tracking behavior — this
table is net-new, from live testing, not something already written
down). `complete_putaway_line()` checks this right after putaway
succeeds: `Status == ILPN_CONSUMED_STATUS` routes to
`adjust_location_quantities()` (new — see below) against the actual
destination used (`putaway_result["toLocationId"]`, correct whether
default or Substitute-Location-overridden); otherwise
`adjust_ilpn_quantities()` (unchanged) against the LPN, same as before.

**`adjust_location_quantities()` / `mawm_client.adjust_location_inventory()`**
— per `mawm_adjust_location_api.md`, a **third** distinct MAWM API in
this app (`POST dcinventory/api/dcinventory/inventory/adjustInventory`,
a raw JSON array body — an object wrapper causes a
`NullPointerException`, confirmed in the document). Unlike Modify
iLPN's `ScannedQuantity`, this endpoint's `Quantity` is a genuine
signed **delta** (`New OnHand = Current + Quantity`) with no
absolute-value shortcut, so the function computes
`delta = desired − current` itself. **CONFIRMED live, both directions,
against two different locations**, independently verified by re-query
each time:
- `A1AC0114` item `3000223` (a single clean inventory record): 50 → 51
  (add) → 49 (subtract) → 50 (restored).
- `C1CS0110` item `50002236` — a real example of the "multiple
  inventory records for the same item at one location" case the
  document warns about: 4 records, all identical attributes (batch,
  product status, country of origin, `InventoryAttribute1` all null —
  nothing to distinguish them by), `OnHand` 0/10/10/10. This app's
  code doesn't disambiguate (takes the first match — see
  `adjust_location_quantities()`'s docstring), and it worked correctly
  anyway: the same record (by `InventoryId`) came back first across
  three separate queries (baseline, post-add, post-subtract), so
  0 → 1 (add) → 0 (subtract) landed on that one record consistently,
  confirmed by checking every record's `InventoryId` individually, not
  just the location's total. **This is empirical, not a documented
  guarantee** — MAWM's search result ordering happened to be stable
  across this test, but nothing confirms it's *always* stable, so a
  location with genuinely ambiguous duplicate records is still a real
  risk for a future call, just a lower one than initially assumed.
  `DCI::313` (stale record) handling is best-effort/**UNCONFIRMED** —
  the document only describes it in prose, no captured error response
  body exists, so the retry trigger is a text-match on
  `LOCATION_ADJUSTMENT_STALE_RECORD_CODE` rather than a confirmed
  envelope shape (not actually triggered in either test above — both
  succeeded on the first attempt).

**CONFIRMED live, 2026-08-08 (eighth session)** — the full *integrated*
chain inside `complete_putaway_line()`, first successful end-to-end
test: `IBPWIBPT0105` (item `50002236`, `LPN00760`, planned qty 10,
destination `C1CS0110`), completed with `desired_qty=9`
(`item_id`/`lpn_id` from the loaded line, `adjustment_reason_code:
"IA"`). Result: `{"success": true, "quantity": 10, "adjustmentTarget":
"lpn", "adjustmentSuccess": true}`. Independently re-verified, not just
trusted: the task itself shows `Completed`/`completedQuantity: 10` (its
own unmodified work-order record — the full putaway that ran first),
while `LPN00760`'s own container inventory genuinely holds **9** units
(a direct re-query, not the response body) — the correction landed on
the LPN itself, exactly as `adjustmentTarget: "lpn"` reported.

**This corrected a wrong assumption, not just confirmed the mechanism**:
`C1CS0110` is `InventoryReservationTypeId='LOCATION'` — the earlier
(no-task/DMM-flow) testing predicted this destination would consume the
LPN, routing to `adjust_location_quantities()`. It didn't happen here —
the LPN stayed a live, independently-adjustable container
(`Ilpn.Status` came back `"3000"` / "Not Allocated," not `"9000"` /
"Consumed"). Best current read: **task-mode completion (Path C,
`fetchNextPutawayMoveAndStartLaborActivity`/`commitAndFetchNextMove`)
and no-task completion (the DMM Mobile Facade's "User Directed
Putaway," `AcceptContainer`/`AcceptLocation`) are different MAWM code
paths and may follow different LPN-consumption rules for the very same
destination** — one data point isn't proof of a general rule, but it's
a direct, live contradiction of the earlier assumption, so don't trust
`InventoryReservationTypeId` alone to predict task-mode behavior. This
is exactly why the auto-detection checks the *LPN's actual post-putaway
status* rather than pre-guessing from the destination's config — that
design choice is what made this test succeed correctly despite the
wrong assumption underneath it. **Still not live-tested**: a task-mode
completion that *does* get auto-routed to
`adjust_location_quantities()` (i.e., a real allocated task whose LPN
genuinely comes back `Status: "9000"` after Path C putaway) — not yet
observed even once; whether that's rare, or task-mode Path C simply
never consumes the LPN at all, is still an open question.

Also revealed: `search_ilpn_current_location()`'s `CurrentLocationId`
came back `null` for this LPN too, same as a consumed LPN — but its
`Status` was `"3000"`, not `"9000"`. So `CurrentLocationId: null` alone
does **not** reliably mean "consumed" (`_ilpn_display_fields()`'s `*`-
marked-previous-location fallback already only fires on `Status ==
ILPN_CONSUMED_STATUS`, not on a blank `CurrentLocationId` by itself, so
this doesn't change that logic — but it does mean a task-mode line
whose LPN ends up in this state shows a genuinely blank Current
Location with no `*` fallback, which is accurate, just worth knowing
isn't the same situation as a truly consumed LPN).

**Repeated with a second, independent task/LPN — same result.**
`IBPWIBPT0110` (item `50002236`, `LPN00764`, planned qty 10, same
destination `C1CS0110`), `desired_qty=9`: `adjustmentTarget: "lpn"`
again, and re-queried directly (not just trusted): `LPN00764` stayed
`Status: "3000"` (not consumed), its own container inventory shows
`OnHand: 9.0` (the correction landed on the LPN), and `C1CS0110`'s
location inventory for `50002236` was untouched by the adjustment —
still the same 0/10/10/10 rows (one row's `UpdatedTimestamp` lines up
with this putaway move and stayed at `0.0`, so that's the move itself
touching a location-level shadow record, not the adjustment logic
firing). Two different tasks/LPNs to the same `LOCATION`-typed
destination now agree, which weighs against this being a one-off
bad-data artifact — but per the user, still worth confirming by hand
in WM after a refresh, since two data points from the same test
environment/config could still share a root cause. To re-check
manually: find a fresh allocated task headed to `C1CS0110` (or another
`InventoryReservationTypeId='LOCATION'` destination), complete the
putaway there, and see whether WM shows the LPN as consumed (no longer
independently adjustable) or still live. `IBPWIBPT0105`/`LPN00760` and
`IBPWIBPT0110`/`LPN00764` are both already used up, so this needs a
*different* fresh task.

**Third test (`IBPWIBPT0052`) found a real bug, not bad data** — this
task was structurally different from the first two: `taskTransactionId:
"Storage Putaway"` (not `"Putaway"`), `lpnId` equal to `toLocationId`
itself (`A1AC0212`), `ilpnStatus` blank, `uomId: "Units"` not `"LPN"`.
`search_ilpn_current_location('A1AC0212', ...)` returned `null` — there
was never a real ILPN container for this line at all; it's a loose/
location-direct putaway. The routing logic's `consumed` check
(`bool(ilpn) and Status == "9000"`) treated "no record found" the same
as "not consumed," so it wrongly went down the LPN-adjustment branch —
which then threw an **uncaught `RuntimeError`** trying to refresh a
read-timestamp on a container that doesn't exist (MAWM returned a raw
500, "Could not commit JPA transaction"). The putaway itself had
already committed by that point (task went to `Completed`,
`completedQuantity: 10`) — only the follow-up adjustment crashed, and
it crashed the whole call instead of degrading gracefully.

Fixed in `complete_putaway_line()`: `ilpn is None` now routes to the
location branch (renamed `consumed` → `route_to_location`), on the
reasoning that no ILPN record at all means there's no LPN-level
inventory to adjust — same effective situation as a consumed LPN, just
arrived at differently. Also wrapped both adjustment calls in
`try/except` so a crash there degrades to `adjustmentSuccess: False` +
`adjustmentError` instead of taking down the whole response, since the
putaway has already committed by that point regardless. **Not yet
re-verified live** — `A1AC0212`'s inventory is already muddled by this
test (two existing rows, 40.0/20.0, now including this task's
uncorrected 10 units merged in somewhere) and the task itself is
already `Completed`, so a clean re-test needs a fresh task, which lines
up with the user's own plan to create new manual putaway tasks after a
data refresh rather than keep testing against old data.

## LPN status badge, consumed-location display (2026-08-08, seventh session)

- **`mawm_client.ILPN_STATUS_LABELS`/`ilpn_status_description()`** — the
  confirmed `ilpn_dc_inventory_status` domain (1000 In Transit, 2000
  Pre-Receipt Allocated, 3000 Not Allocated, 4000 Partially Allocated,
  5000 Allocated, 9000 Consumed, 10000 Lost, 11000 Canceled), mirroring
  `TASK_STATUS_LABELS`/`task_status_description()`'s existing pattern.
  A genuinely different ladder from `TASK_STATUS_LABELS` and from
  receivingworkbench's own `LPN_STATUS_LABELS` (that one's the
  *receiving-component* ASN-nested `LpnStatus` domain — same numeric
  codes, different meanings; `mawm_api_library` is explicit the two
  must not be merged).
- **Always shown now, both modes** — per explicit instruction ("let's
  always display LPN status so it's consistent"). No-task/container
  lines already did an iLPN lookup (`search_ilpn_current_location()`,
  Template now also fetches `PreviousLocationId`); task-mode lines
  didn't do one at all before this, so `_build_task_response()` now
  batches one `search_ilpn_statuses()` call (new — the same `IlpnId in
  (...)` convention `search_items()` already uses for items) covering
  every line's own LPN in one round trip, not one call per line.
  `_ilpn_display_fields()` is the shared helper both code paths call.
- **Consumed-location display, confirmed live on two real LPNs**:
  `LPN000000000006` (the user's own test case) and `LPN000000000315`
  (this session's own earlier test) — both `Status: 9000`,
  `CurrentLocationId: null`, and `PreviousLocationId` holding exactly
  the location the LPN was consumed into (`A1AC0401` and `A1AC0114`
  respectively — the latter matching exactly where this app put it
  away earlier). When `Status == ILPN_CONSUMED_STATUS` and
  `CurrentLocationId` is blank, the no-task line's Current Location
  shows `PreviousLocationId` with a trailing `*` (e.g. `A1AC0401*`) —
  the user's own suggested notation — so there's still something
  useful to show instead of blank, while staying visually distinct
  from a real current location. **Task-mode lines' own Current
  Location is deliberately NOT touched by this** — that column there
  is `TaskDetail.SourceLocationId` (where the task expects to pick
  from), a different concept from the iLPN's own tracked location;
  only the status badge applies to task-mode lines, not the `*`-marked
  location fallback.
- **Placement — the LPN column itself, not a new column**: the
  existing "Task/Container" column was the obvious other candidate
  (it already shows a status badge), but it's hidden by default for
  the common single-group case (`multi-group` CSS class), which would
  have silently broken "always display" for the majority of searches.
  `lpnCellHtml()` puts the badge directly under the LPN id instead,
  which is always visible regardless of group count.
- **Color rule — its own, distinct from the task-status badge's
  red/green split**: `ilpnStatusBadgeClass()` — Consumed is grey
  (`.badge.status-grey`, new CSS), Lost/Canceled stay red, everything
  else green. Per explicit instruction: Consumed is the *expected*
  outcome after putaway, not a success or a problem, so it doesn't fit
  either end of the existing red/green rule.
- Confirmed live via both `resolve_search_multi()` output and a
  browser render: `LPN000000000006` → grey "Consumed" badge +
  `A1AC0401*`; `IBPWIBPT0109`'s line (`LPN00763`) → green "Allocated"
  badge, `STAGIB0204` (its real TaskDetail source location) unchanged.

## Consumed LPNs are read-only (2026-08-08, seventh session)

Per explicit instruction: a consumed LPN's inventory has already moved
to a location record — there's nothing left on the LPN to adjust or
putaway, so it's now blocked at every layer rather than relying on any
single check:

- **Frontend inputs disabled outright** — `toLocationCellHtml()`'s To
  Location input and the Completed Qty input (both the single-item box
  and every mixed-item sub-row's box, keyed off the *parent* line's
  status since only the container carries its own iLPN status) get
  `disabled` + an explanatory `title` tooltip when `isConsumedLine(line)`
  (`line.ilpnStatus === "9000"`, mirroring `mawm_client
  .ILPN_CONSUMED_STATUS`). `validateLocation()` also skips a disabled
  input rather than showing it red/"invalid" — a disabled empty box
  reading as an error would be misleading when there's nothing to fix.
- **Buttons gated explicitly** — `updateLineActionButtons()`/
  `allOutstandingLinesValid()` both add `!isConsumedLine(...)` alongside
  the existing location/qty/reason checks, and `completeLine()` shows
  its own clear message ("already been consumed and can no longer be
  updated") if a consumed line is ever selected, mirroring the existing
  "already complete" pattern.
- **Backend guard, defense in depth** — `complete_container_putaway()`
  now looks up the container's iLPN status itself and refuses outright
  (before touching anything else — adjustment, workflow_init, all of
  it) if `Status == ILPN_CONSUMED_STATUS`, so a direct API call can't
  bypass the frontend's own restriction. Confirmed live against
  `LPN000000000006`: `{"success": false, "error": "This LPN has
  already been consumed — its inventory moved to a location and it can
  no longer be updated."}`.
- Confirmed via browser render too: both inputs correctly greyed out,
  Complete Line correctly disabled, no red "invalid" flash on the
  disabled To Location.
## Consumed LPN item/qty history (2026-08-09, tenth session)

The gap flagged above — a consumed LPN's own inventory/location come
back empty, so there was nothing to show for what it actually held —
is now filled in, via a genuinely different MAWM component (DMUI
Search's Activity Tracking view, not dcinventory/task/inventory-
management like everything else in this app).

- **`mawm_client.search_ilpn_activity_history(container_id, ...)`** —
  `POST dmui-search/api/dmui-search/entity/search`, filtered only by
  `ContainerId` (no item/transaction/date filter, per the source
  document — deliberately returns the LPN's complete event history so
  the selection logic below can pick the right one). A different shape
  from every other call in this app: `Filters`/`ViewName` instead of
  `Query`/`Template`, and the real rows live under `data.Results`, not
  `data` directly.
- **`task_service.select_terminal_ilpn_activity(records)`** — the
  confirmed-by-user-capture selection logic
  (`mawm_lpn_history_api_and_selection_logic.md`): group rows
  representing the same business event (same `TraceId` +
  `ActivityDateTime` + `TransactionTypeId` + `TransactionId` — a single
  Modify iLPN adjustment can be split across two published rows, each
  carrying different fields, merged here so nothing gets lost to
  whichever row got picked); prefer a row whose transaction explicitly
  represents consumption if one exists (not confirmed to ever actually
  occur — the source document's own example never had one); otherwise
  the latest `ActivityDateTime` row that moved the LPN to
  `ILPN_CONSUMED_STATUS`.
- **Wired into `resolve_search()`'s no_task branch, per explicit
  instruction to reuse the existing Item/Qty columns** — the same way
  Current Location already gets a `*`-marked `PreviousLocationId`
  fallback, not new dedicated fields. `itemId`/`plannedQuantity`/
  `remainingQuantity` all get a trailing `*` when populated this way
  (`"5000221*"`, `"6*"`) — the user's own words: "we can do the same
  for item and qty for now until I see it working and then perhaps we
  can remove the * indicator." `plannedQuantity`/`remainingQuantity`
  become **strings** in this case (`_num("6*")` would silently
  collapse to 0 — `_dec()` swallows the parse failure — so the marked
  value bypasses `_num()` entirely) — confirmed this doesn't break
  anything downstream: a consumed line is already excluded from
  `remainingQty()`-based gating via `isConsumedLine()` checks
  everywhere that matters, and the one place `remainingQty()` still
  runs unconditionally (`Number("6*") → NaN → 0`) degrades to exactly
  the right answer anyway ("nothing remaining" is correct for an
  already-consumed line).
- **CONFIRMED live, twice, plus a clean negative case**:
  - `LPN000000000006` (the source document's own example) — live API
    call, not the document's cached response — returned exactly
    `itemId: "5000221*"`, `description: "Floral Print Dress"`,
    `fromLocationId: "A1AC0401*"`, `plannedQuantity: "6*"`, matching
    the document's derivation precisely. Confirmed rendering correctly
    through the real browser UI too.
  - Most *other* consumed LPNs in this org (`LPN00479`,
    `LPN000000000496`, `LPN01007`, etc.) have **no**
    `PreviousLocationId` at all, unlike `LPN000000000006` — a real
    stress test for whether activity history could find something the
    old location-only fallback couldn't. It didn't: `LPN00479` has
    **zero** Activity Tracking records (confirmed directly — real
    seed/imported demo data has no transactional history, not a bug).
    Correctly degrades to the same blank fields as before this
    feature existed, no crash, no regression.
  - A normal non-consumed LPN with real inventory (`LPN00760`)
    confirmed completely unaffected — plain numeric quantity, no `*`,
    exactly as before.

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

## Ad hoc Cycle Count (2026-08-08, ninth session)

First feature outside Putaway. Ported from the sibling `cyclecount` app
(`C:\Users\ssmith\Personal\Development\work\cyclecount`, v1.3.1) —
same host/auth, a different MAWM component
(`inventory-management`/`dcinventory`, not `putaway`/`task`).

**Search-box integration**: the existing "Task Id or iLPN" box now also
accepts one or more Storage locations (same `;`/`,`/whitespace
delimiter as the existing multi-search). `public/app.js`'s
`classifySearchInput()` checks each token against the already-preloaded
`state.storageLocations` set (see `preloadStorageLocations()`) — all
tokens recognized as locations → `cycle_count` mode; all
unrecognized → `task` mode; a mix of both → **Load Task is disabled**
with an explanatory hint (per explicit instruction: mixing is not
allowed, not auto-resolved by guessing intent). `state.searchMode`
drives this at type-time; `state.lastSearchMode` remembers which mode
the *last successful load* used, so `reloadCurrentSearch()` calls the
right endpoint.

**Separate results table** (`#cycleCountLinesTable`/`#cycleCountLinesBody`,
per explicit instruction over conditional columns on the putaway table
— confirmed live in the browser): columns are Line/Location/Item
(editable text input)/Description/Qty/Result. The Qty box starts
genuinely blank (not pre-filled with current on-hand) and is **forced**
— `isCycleCountQtyValid()` checks the raw string is non-empty before
`Number()`, so an untouched blank box doesn't silently pass as "0" the
way `Number("") === 0` would otherwise allow; `0` typed explicitly is
valid. A location with more than one genuinely distinct item (after
`resolve_cycle_count_location()`'s ItemId-dedup, see below) renders as
the same MIXED accordion pattern already used for multi-item no-task
containers — confirmed live for the single-item case; the accordion
path for a real multi-item location is unconfirmed (no live example
found yet).

**Backend**: `task_service.resolve_cycle_count_search_multi()`
(parallel to `resolve_search_multi()`) → `resolve_cycle_count_location()`
per location, using `search_location_inventory()` (already existing,
same `dcinventory` endpoint the old app's `getInventory` called
directly) for item-from-location lookup.

**Critical fix, confirmed live 2026-08-08**: `resolve_cycle_count_location()`
**deduplicates by ItemId** — a location can carry more than one raw
inventory *record* for the same item (`C1CS0110` came back as 4 rows
all for item `50002236`; same duplicate-record pattern already
documented for `adjust_location_quantities()`). Without this, the
first cut of this feature rendered 4 identical duplicate lines with
colliding keys. One line per distinct ItemId, not one line per row.

### The six-call completion chain, and why the first cut of it was wrong

`task_service.complete_cycle_count_line()` runs `initiateCount` →
`validateItemAndGetItemDetails` → `acceptQuantity` →
`persistCountDetails` → `endCount` (`mawm_client.py`'s Cycle Count
section has each call's payload shape, ported from the old app).

**The first implementation auto-overrode a quantity-mismatch WARNING
from `acceptQuantity` and reported success** — this looked plausible
(HTTP 200/201 the whole way through) but was **directly contradicted
by live testing**: counting `A1AC0114` at 5 when the real on-hand was
50 reported "Completed" while on-hand silently stayed 50. Same result
at two more locations, including one that never had any inventory at
all (`A1AC0117`, counted for the first time — stayed empty). The
reference `cyclecount` app has the identical payload shape and almost
certainly shares this exact gap; it apparently was never caught because
its own success reporting never independently re-verifies the write —
exactly the trap this whole project's live-verification-over-trusting-
`success:true` methodology exists to catch.

**Explained by the user, then confirmed live against a real 3-location
sample file** (`C:\Users\ssmith\OneDrive - Manhattan
Associates\Desktop\cyc.txt`: `A1AC0123`→240, `A1AC1201`→39,
`A2AC1201`→10) and a document the user captured
(`mawm_cycle_count_location_investigation.md`) — this isn't a bug in
the six-call chain at all. None of those six calls' own HTTP status or
messages reveal whether a count actually gets applied; MAWM books (or
doesn't) **asynchronously**, and the real outcome is only observable
via three separate read-only endpoints, none previously known to any
app in this workspace:

- `search_location_count_info()` → `POST dcinventory/api/dcinventory/locationCountInfo/search`
  — the location-level lock flag, `CycleCountPending` (`true` while a
  count is unresolved, `false` once resolved — "resolved" isn't the
  same as "succeeded").
- `search_inventory_count_runs()` → `POST inventory-management/api/inventory-management/inventoryCountRun/search`
  — every count run ever attempted for a location; each row nests its
  own `ContainerCount[]`/`InventoryCount[]` detail (the same data
  `inventoryCountResult` below returns flat).
- `search_inventory_count_results()` → `POST inventory-management/api/inventory-management/inventoryCountResult/search`
  — the flat, item-level detail for one `CountRunId`:
  `OriginalQuantity`/`CountQuantity`/`VarianceQuantity`/`Status` (a
  human label, not just a code)/`BookingFailureReason`. This is what
  `complete_cycle_count_line()` polls.

Tested with and without a `Template` on all three — identical full row
either way; the document's templated field list is the complete set.

**CONFIRMED live, three real outcomes** (the sample file's three
locations, then reproduced again through the corrected production code
path on fresh, never-before-touched locations):

1. **Perfect match** (`A1AC0123`: counted 240 = on-hand 240;
   `A1AC0122`: counted 996 = on-hand 996) — every call clean 2xx, no
   warning anywhere, books synchronously. `inventoryCountResult.Status:
   "Booked"`, `StatusKey: "80"`.
2. **Within tolerance** (`A1AC1201`: counted 39 vs on-hand 41, -2
   variance) — `acceptQuantity` returns HTTP 400 with `INM::227`
   ("Quantity mismatch") as a `WARNING` only (no `ERROR`). The count
   still books, but **asynchronously** — an immediate re-query after
   `endCount` returns can still show the pre-count value;
   `complete_cycle_count_line()` polls `inventoryCountResult` up to 5
   times, 0.6s apart, same posture as Modify iLPN's `endIlpn`
   elsewhere in this app.
3. **Out of tolerance** (`A2AC1201`: counted 10 vs on-hand 20, -10
   variance; `A1AC0127`: counted 890 vs on-hand 898, -8 variance) —
   `acceptQuantity` returns the same `INM::227` WARNING **plus**
   `INM::411` ("Recount required") as an `ERROR`. `persistCountDetails`/
   `endCount` still both report clean success, but the count run parks
   at `Status: "Pending Booking"` (`StatusKey: "35"`) indefinitely — not
   an async delay, confirmed by re-checking after a 6-second wait and
   again minutes later. `locationCountInfo.CycleCountPending` flips
   `true` and stays there — the location is genuinely locked pending a
   real human/supervisor booking decision outside this app's reach.

Exact tolerance thresholds are MAWM-side configuration, not something
this app determines or should try to reverse-engineer — the -8/898
(~0.9%) case went out-of-tolerance while the -2/41 (~4.9%) case stayed
within it, so it is **not** a simple percentage cutoff.

`complete_cycle_count_line()` now runs the full six-call chain to
completion regardless of what any individual step reports (a WARNING
or ERROR message at validate/accept is not, by itself, treated as
failure — confirmed live that both still let the chain proceed all the
way to `endCount` returning 200), then polls for the real outcome.
`success` is only `True` when the poll finds `Status == "Booked"`;
`"Pending Booking"` and any other non-Booked status come back as
`success: False` with the real status, variance, and
`bookingFailureReason` attached — distinct from an actual chain
exception (network failure, unparseable response), which still stops
the chain early and returns a plain error.

The **frontend** (`cycleCountResultText()`/`cycleCountResultKind()`
in `app.js`) shows three distinct Result-cell states matching this:
green "Booked: X → Y" (done, inputs disabled), muted/italic "Pending
supervisor booking… Location locked — not yet applied" (left editable,
not marked done — a corrected recount is plausible), red for a genuine
failure. `locationLockedBefore`/`locationLockedAfter` are returned by
`complete_cycle_count_line()` but not yet surfaced in the UI.

**Progressive UI, added same session per explicit instruction** ("it
sometimes takes the system a few seconds to update to booked... first
display the [in-flight] status and then poll a few times to update
that field once its booked... show the previous/counted qty and the
variances... so the user can see the full result"):

- `complete_cycle_count_line()` no longer blocks synchronously waiting
  for booking — it runs the six-call chain, checks
  `inventoryCountResult` **once** immediately, and returns whatever
  status that shows right then (often still `"Count Initiated"` or
  `"Pending Booking"`), instead of the earlier version's ~3s blocking
  poll loop before responding.
- New `check_cycle_count_status()` (`POST /api/check_cycle_count_status`,
  `{locationId, itemId, countRunId}`) is a lightweight re-check —
  same `inventoryCountResult` lookup, no chain re-run. `app.js`'s
  `pollCycleCountLineStatus()` calls it after any non-`"Booked"`
  completion result, live-updating the row each time. There's no way
  to tell an eventually-resolving in-flight status apart from a
  genuinely stuck out-of-tolerance one from the status text alone, so
  it always polls the full window regardless — confirmed live for
  both: a within-tolerance count visibly flipped from red "Count not
  booked (status: Count Initiated)" to green "Booked: 10 → 9
  (variance -1)" a few seconds later; an out-of-tolerance count
  settled into the muted "Pending supervisor booking…" state and
  correctly stopped polling rather than spinning forever.
- Table gained two new columns, **Previous Qty** and **Variance**
  (`.cc-previous-qty`/`.cc-variance` cells, populated by the shared
  `applyCycleCountResultToRow()` used by both the initial completion
  and every subsequent poll tick) — both empty until a real
  `inventoryCountResult` row exists.
- Polling is fire-and-forget (not awaited by the caller) so **Complete
  All** isn't blocked waiting for each line's booking to resolve
  before moving to the next line — each line's poll runs independently
  in the background.

**Real near-miss found and fixed the same session** — the poll window
started at 8 attempts × 1.8s (~14.4s), sized from a single isolated
test. Running Complete All across 3 real sample locations
(`A1AC0123`/`A1AC1201`/`A2AC1201`) exposed it as too tight: measured
live via `inventoryCountRun`'s `Created`/`UpdatedTimestamp`,
`A1AC1201`'s real booking took **~14.7 seconds** — past the old
cutoff by about 300ms, so polling gave up moments before the
resolution landed, leaving the row stuck on "Count not booked" even
though the count had, in fact, booked (confirmed by re-querying
`inventoryCountRun`/`search_location_inventory` directly afterward —
`Status: 80` "Booked", `OnHand` matching the counted quantity).
Widened to **30 attempts × 2s = 60s** — `check_cycle_count_status()`
is a cheap read, so the wider window costs little even for the
out-of-tolerance case that never resolves and just polls uselessly
until giving up. Re-confirmed live afterward: Complete All across
`A1AC0134`/`A1AC0139`/`A1AC0140` (perfect/within-tolerance/out-of-
tolerance) all resolved correctly, including the within-tolerance
line flipping from "Count not booked (status: In Booking)" to
"Booked: 9 → 8 (variance -1)" a few seconds later.

Also fixed in the same pass: Complete All's failure/issues summary
used to read "Line 1: ...; Line 1: ...; Line 1: ..." for a 3-location
batch — every location's own line numbering restarts at 1 (see
`resolve_cycle_count_location()`), so bare `lineNumber` is ambiguous
once more than one location is involved. `cycleCountFailureLabel()`
now always prefixes with the location. The summary banner itself was
also reworded — it used to read as a final tally ("Completed 0 of 3
lines"), which is misleading given booking is asynchronous and a line
counted as an "issue" the instant the loop finishes may still book
moments later via the background poll.

**WM-matching lock icon + terser Pending Booking display (2026-08-08,
same session, per explicit instruction with a reference screenshot of
the real WM UI)**:

- **Location lock icon**: `resolve_cycle_count_location()` now also
  calls `search_location_count_info()` up front and returns
  `locationLocked` on both the group and every line (denormalized the
  same way as `groupLocationId` etc), so the frontend knows a
  location's `CycleCountPending` state **before any count is even
  started** — confirmed live: searching two already-locked locations
  (`A1AC0140`/`A2AC1201`, still pending from earlier same-session
  testing) showed the icon immediately on load, with no completion
  action taken yet. No source asset for the real WM icon (two circular
  arrows) was found anywhere on disk despite a real search — `app.js`'s
  `cycleCountLockIconHtml()` uses Font Awesome's `fa-arrows-rotate`
  (already loaded in this app) as the closest equivalent rather than
  guessing at/fabricating a custom icon. Always rendered in the row's
  HTML but display-toggled (`setCycleCountLockIcon()`) so a later
  completion/poll result can show or hide it live without re-rendering
  the row — "before or after count," per explicit instruction.
- **Terser, red Pending Booking message**: was a full sentence
  ("Pending supervisor booking (out of tolerance): was 20, counted 10
  (variance -10). Location locked — not yet applied.") in muted grey;
  now "Pending Booking 20 → ~~10~~ (variance -10)" in red — the counted
  qty (not the still-current previous qty) struck through, since that's
  the value that didn't actually take effect. `setCycleCountResultCell()`
  switched from `textContent` to `innerHTML` to support the `<s>` tag;
  `cycleCountResultText()` still escapes any free-text MAWM
  error/failure-reason string before it reaches that innerHTML, since
  the numeric qty/variance fields are always trusted API values but
  error text technically isn't.
- **Variance dollar value**: `_cycle_count_result_response()` now also
  returns `varianceValue` (MAWM's `VarianceValue` field, confirmed in
  the user-captured investigation doc). Shown in the Variance column as
  smaller grey text to the right of the quantity variance — "10 ($50)"
  per explicit instruction — rounded to whole dollars,
  accounting-style parens for negative (no explicit minus sign inside,
  matching the requested example literally) via
  `formatVarianceValueHtml()`.
- All three confirmed together live: `A1AC0142` (baseline 574, counted
  200) rendered the red lock icon next to the location, "Pending
  Booking 574 → ~~200~~ (variance -374)" in red, and "-374 ($1870)" in
  the Variance column.

**Consolidated into 3 stacked lines + always-parens variance
(2026-08-08, same session, per explicit follow-up instruction)**:

- The separate Previous Qty and Variance *columns* are gone — folded
  into the Status column (`.col-cc-result`, widened to `16rem`) as
  three stacked `<div class="cc-result-line">` rows: status label /
  `before → after` (counted struck through unless `success`) /
  `variance (variance-value)`. Applies uniformly to every real status
  MAWM returns, not just "Booked"/"Pending Booking" — confirmed live:
  an in-flight "Count Initiated" result already showed all three lines
  ("Booked" / "20 → 15" / "-5 ($0)" once resolved).
  `setCycleCountResultCell()` switched to `innerHTML` for this (see
  above); `cycleCountResultText()` builds the three divs directly.
- `formatVarianceValueHtml()` now **always** parenthesizes the dollar
  value regardless of sign (2026-08-08, revised again per explicit
  instruction — the first cut only did accounting-style parens for
  negative) — confirmed live showing `($0)` for a zero-value variance,
  not a bare `$0`.
- Description column narrowed (`.col-desc-narrow`, `max-width: 12rem`)
  to give the wider Status column room; overflowing text truncates
  with an ellipsis and the full text is still available via a `title`
  tooltip. **Real CSS gotcha found and fixed while verifying this
  live**: `max-width`/`overflow`/`text-overflow` applied directly to
  the `<td>` silently does nothing in a normal (auto-layout) HTML
  table — confirmed via direct DOM measurement
  (`scrollWidth === clientWidth`, i.e. no truncation happening at all)
  before the fix. Wrapping the text in an inner `<div>` and applying
  the truncation styles to *that* div instead fixed it (confirmed via
  the same measurement after the fix, `scrollWidth 497 > clientWidth
  192`, using a synthetic M&M-candy-length description since no real
  item in this demo org has one long enough to trigger it naturally).

**Open, unexplained, and deliberately not chased further**:
`A1AC0124` (an item location with 24+ pre-existing `inventoryCountRun`
rows already on it — clearly reused/heavily-seeded demo data, not a
fresh location) produced a count run stuck at `Status: "Count
Initiated"` (`StatusKey: "20"`, a status not seen anywhere else) with
a GUID-shaped `TaskId` instead of the `CCNTINM0005xx`-style TaskId
every other test produced, and never progressed even after several
minutes. Also seen once: `StatusKey: "95"` on some of that location's
historical runs, meaning unknown. Given the heavy pre-existing count
history specific to that one location, this reads as a location-
specific artifact of the demo data rather than a general behavior —
not reproduced on any genuinely fresh location — but flagged here
rather than silently ignored.

## Multi-item location: atomic completion required (2026-08-08, tenth session)

**Test location found**: `A1AC0924` — `LocationTypeId: STORAGE`,
`InventoryReservationTypeId: LOCATION` (non-LPN-tracked), two genuinely
distinct real merchandise items (`5000001` "10 Pack SD Rice", OnHand
1739; `5000002` "10 Pack SD Wheat", OnHand 750) — found by a broad
`dcinventory/inventory/search` for `InventoryContainerTypeId='LOCATION'`
grouped by `LocationId` in Python to find one with 2+ distinct
`ItemId`s. Most multi-item hits in this org are equipment/asset
records (`GAYLORD`, `CHEP PALLET`, `CAGE`, etc), not real SKUs — this
one was the only clean real-merchandise match. Never locked, 0 prior
count runs, before this session's testing.

**Per-item independent completion does NOT work for a multi-item
location — confirmed live, then fixed.** The original design (each
item runs its own full `initiateCount`→...→`endCount` chain,
independent of its siblings) was based on an untested assumption.
Real behavior, confirmed by completing just one of the two items:

- The count parks at `"Count Initiated"` **indefinitely** — polled for
  30+ seconds straight with zero change, not just "a bit slower."
- Calling `endCount` before every item at the location is addressed
  returns HTTP 400 with `INM::230` ("Not all the Items in the Location
  are counted") — a real `WARNING`, not a silent "unaddressed item
  counts as 0" default. Confirmed safe by direct verification: the
  addressed item's result stays at `"Count Initiated"` (not falsely
  booked), the *unaddressed* item has **no `inventoryCountResult` row
  created at all** (never force-evaluated, no phantom variance/
  tolerance failure), and real on-hand for both items is untouched.
  This directly answers a real concern raised before testing (an
  uncounted item silently defaulting to 0 and triggering a false
  out-of-tolerance/tolerance-check failure) — confirmed live that
  MAWM's own safeguard rules this out; nothing on this app's side had
  to compensate for it.
- A second item's own `initiateCount` call against an already-open
  count for the same location reuses the **same** `CountRunId`/
  `TaskId` (MAWM's own dedup — confirmed live, not something this app
  requests or controls). Once every item has been persisted under that
  shared run, a single `endCount` call succeeds and every item
  transitions together — through a previously unseen intermediate
  status, `"Count Complete"` (`StatusKey: 30`), to `"Booked"`.

**Fixed**: `task_service.complete_cycle_count_location()` (new,
alongside `check_cycle_count_location_status()` for polling) runs the
correct atomic sequence — one `initiateCount`, then
`validateItemAndGetItemDetails`/`acceptQuantity`/`persistCountDetails`
looped per item (all reusing the same `CountRunId`/`TaskId`), then
exactly one `endCount` call at the very end, only once every item has
been persisted. If any single item's own validate/accept/persist call
raises, this stops immediately *without* calling `endCount` — leaving
the run parked at `"Count Initiated"` (confirmed safe above) rather
than calling `endCount` on incomplete data for a different, more
confusing rejection. `complete_cycle_count_line()`/
`check_cycle_count_status()` (the original per-item versions) are kept
for the single-item case — a location with exactly one item has no
"other items" to wait for, so the atomic version isn't strictly
required there, but the frontend now always uses the atomic path
uniformly (see below) since a single-item location is just "a group of
1" and needs no special-casing.

**Frontend reworked to select/complete by *location*, not by line**
(`app.js`) — per explicit instruction ("we probably need to force a
user to enter all lines, with 0 being a valid number"):
- Every cycle-count row (single-item, MIXED summary, MIXED sub-rows)
  now carries `data-group-key`; clicking any of them selects the whole
  group (`selectCycleCountGroup()`), highlighting every row in it.
- `isCycleCountGroupDone()`/`isCycleCountGroupValid()` require **every**
  line in the group to be done/valid — Complete Line stays disabled
  until every item in a MIXED location has a real quantity entered (0
  valid, matching `isCycleCountQtyValid()`'s existing empty-string
  check from earlier this session).
- `completeCycleCountGroupAction()` calls the new
  `/api/complete_cycle_count_location` with every item's current
  itemId/quantity in one request; `pollCycleCountGroupStatus()` is the
  group-level counterpart to the earlier per-line poller, calling
  `/api/check_cycle_count_location_status` for all items at once.
- Complete All now iterates *groups*, not flat lines — a MIXED
  location counts as one unit in "N of M" progress and in the
  confirmation modal/failure summary.

**Confirmed live end-to-end through the real UI** (not just direct
Python calls): `A1AC0924`, both items counted at their exact current
on-hand (1739/750, no variance) via one Complete Line click on the
selected group — both resolved to `"Booked"` (`"1739 → 1739"`/
`"750 → 750"`, `"0 ($0)"` variance), the action-status banner read
"Location A1AC0924 booked.", both rows correctly marked done
(qty/item inputs disabled), and real MAWM data independently confirmed
unchanged on-hand (1739/750) with the location unlocked afterward.

**Within-tolerance multi-item test, same session, per explicit
instruction** ("test one line difference within tolerance… before out
of tolerance") — one item off by 1 on its larger on-hand quantity
(`5000001`: 1739 → counted 1738, then a follow-up UI test 1738 →
counted 1737), the other exact (`5000002`: 750 → 750). Confirmed live
via both a direct `complete_cycle_count_location()` call and, in a
follow-up pass, the real UI end to end:

- Immediately after submission both items show
  `"Count Initiated"`/`"In Booking"` (a **third** new intermediate
  status not seen before — the observed progression across all
  multi-item tests so far is `Count Initiated` → `In Booking` →
  `Count Complete` (multi-item only, once every item is persisted) →
  `Booked`; not all stages necessarily show for every case, e.g. a
  perfect match can skip straight to `Booked`).
- The mismatched item's `acceptQuantity` step returns the usual
  `INM::227` "Quantity mismatch" WARNING; polling confirmed both items
  resolve to `"Booked"` together, same as the no-variance case.
- Real on-hand correctly updated for the mismatched item only
  (1739→1738, then 1738→1737 in the follow-up test) — the exact-match
  item stayed untouched (750) both times, confirmed via direct
  `search_location_inventory()` re-query after each test, not just
  trusted from the API response.
- The UI test specifically confirmed the polling path: the row showed
  `"In Booking"` with the not-yet-applied counted qty struck through
  immediately after clicking Complete Line, then flipped to green
  `"Booked: 1738 → 1737 (variance -1)"` a few seconds later via
  `pollCycleCountGroupStatus()`'s background poll, with the
  action-status banner updating to "Location A1AC0924 booked." —
  matching the single-item polling behavior confirmed earlier in this
  session, now proven for the multi-item atomic path too.

**Out-of-tolerance multi-item test — a genuinely important finding**
(same session, tested right after within-tolerance per explicit
instruction): `5000001` counted way off (1737 → 800, variance -937),
`5000002` counted exactly right (750 → 750, variance 0). Confirmed
live via both a direct `complete_cycle_count_location()` call and the
real UI end to end:

- **Both items** ended up at `Status: "Pending Booking"` — including
  `5000002`, the exact match with zero variance. One item's tolerance
  failure blocks the **entire location's** count from booking, not
  just the offending item — this is evaluated at the location/count-
  run level, not purely per item.
- Real on-hand confirmed **unchanged for both items** (`5000001`
  stayed 1737, `5000002` stayed 750) — the correctly-counted item's
  correction does not get applied just because it happened to be
  right; it's held hostage by its sibling until a supervisor resolves
  the whole location.
- UI correctly reflected this: both rows showed red `"Pending Booking"`
  with the counted qty struck through (`"1737 → ~~800~~"` and
  `"750 → ~~750~~"`), the lock icon displayed, and — importantly —
  **neither row was marked done**; both stayed editable/retryable
  (inputs not disabled), unlike the "Booked" case which locks them.
  This is the right behavior (a supervisor might reject the whole
  count, at which point the operator would need to recount and
  resubmit), but it's worth knowing: a single bad count on one SKU in
  a multi-item location holds up every other SKU there too, with no
  partial credit for the ones that were actually right.
- The action-status banner correctly showed the improved summary from
  the same-session fix above ("Still processing (Count Initiated,
  Count Initiated) — status will keep updating.") rather than a bare
  "Complete failed."

## Tasked (non-ad-hoc) Cycle Count — task closure confirmed (2026-08-09, tenth session)

Ad hoc counting confirmed solid, so investigation moved to real WM-
scheduled Cycle Count tasks (`TransactionTypeId ='Cycle Count'` in
`task/api/task/task/search`, same generic Task Management endpoint
Putaway uses). 44 total in `SS-DEMO`, all but 2 already `Status: 8000`
(Completed). Of the 2 open ones, only one was a genuine candidate —
`CCNTINM0000000021` turned out to be leftover byproduct state from
this session's own earlier ad hoc single-item test at `A1AC0139` (its
`TransactionId: "Cycle Count Active-API"` and `AssignedTaskPoolId:
"SystemTaskPool"` match the ad hoc flow exactly, not a real
WM-generated task).

**The real candidate**: `CCNTINM000023`, `Status: 3000` (Ready For
Assignment), `Description: "Recount Forward Picking"`,
`TransactionId: "Recount"`, `AssignedTaskPoolId: "Task Interleaving"`,
pointing at `C1CS0111` (`LocationTypeId: STORAGE`,
`InventoryReservationTypeId: LOCATION` — non-LPN-tracked, confirmed).
`TaskDetail` carries no item (`ItemId: null`, `Quantity: 0`) — per
explicit instruction, this is expected: "when WM creates count tasks,
it is for the entire location," so the app is expected to look the
item(s) up from the location itself, exactly like ad hoc already does
via `search_location_inventory()` — `resolve_cycle_count_location()`
needs no changes for this. `C1CS0111` holds one real item (`123459`,
"Pima Cotton Sweater", OnHand 150) and was already locked
(`CycleCountPending: true`) purely from the task's existence — zero
prior `inventoryCountRun` records, confirming the lock flag can be set
by WM's own task-creation process independent of the ad hoc
`initiateCount` flow entirely.

**Confirmed live: the completion mechanism is shared.** Calling the
exact same ad hoc `initiate_count('C1CS0111', ...)` — no task
parameter, nothing cycle-count-task-specific — auto-detected and
tried to claim the real task by name. First attempt failed with
`FWTSK::019`, *"Task CCNTINM000023 cannot be assigned to user
sdtadmin@ss-demo"* (a live task-assignment conflict, same error class
already documented for Putaway tasks earlier in this project — not a
code bug). After the user manually reassigned the task, the identical
call succeeded and returned `CountRunId: "CNT000800"` tied to
`TaskId: "CCNTINM000023"` — confirming this is genuinely the same
underlying task, not a coincidence.

Ran the full remaining chain (validate/accept/persist/end,
exact-match count, 150 no variance) — all clean 200s. The count itself
resolved correctly: `inventoryCountResult.Status: "Booked"`, real
on-hand confirmed unchanged at 150, `locationCountInfo.CycleCountPending`
cleared to `false`.

**But the task itself never closed via `/count/end`.** `CCNTINM000023`
stayed at `Status: "7000"` (In Progress), its `TaskDetail.Status`
stayed `"1000"` (Created), and `CompletedQuantity`/`Quantity` never
got populated with the real counted values — confirmed genuinely
stuck, not async lag (polled every 3s for 15s straight, zero change).
This meant the ad hoc `initiateCount`→...→`endCount` chain only
handles the inventory-count side (booking the count into inventory);
it does not close out the Task Management record — parallel to how
Putaway needed its own task-completion mechanism beyond just moving
inventory.

**Resolved (2026-08-09): `/count/endCount/trigger` closes the task.**
The user fed the above findings into Glean, which proposed replacing
the final `/count/end` call with:

```
POST {host}/inventory-management/api/inventory-management/count/endCount/trigger
```

```json
{
  "LocationId": "C1CS0111",
  "CountRunId": "CNT000800",
  "TaskId": "CCNTINM000023",
  "CountSequence": 1,
  "CountMode": "USER_DIRECTED",
  "LpnTracking": false,
  "NumberOfLPNs": 0,
  "TransactionId": "Cycle Count Active-API",
  "CriteriaId": "Cycle Count Active-API Mode",
  "CountCriteriaId": "Cycle Count Active-API Mode",
  "TaskIntegrationDTO": {
    "TransactionId": "Cycle Count Active-API",
    "TransactionTypeId": "Cycle Count",
    "LaborActivityId": "Cycle Count",
    "LocationId": "C1CS0111",
    "TaskId": "CCNTINM000023"
  }
}
```

Tested live against `CCNTINM000023`/`CNT000800`, which had already
been fully booked earlier via the old `/count/end` call (count side
was done; only the task was stuck) — called this new endpoint
directly against that existing run instead of starting a fresh count,
since it's a different endpoint, not a replay of the same call.
Response was `200 {"success": true, "data": "OK"}`. **Independently
re-queried task search 6× over 18s afterward** (never trust the
response alone): `Task.Status` had genuinely changed `7000` → `8000`
("Completed" per our own `TASK_STATUS_LABELS`), stable across every
poll, with `ActualEndTime` now populated — not async lag, a real
change.

**Important nuance found in `TaskDetail`**: the task actually carries
**two** `TaskDetail` rows, not one. The original row (`Sequence: 1`,
created when WM first scheduled the task, `ItemId: null`) stayed at
`CompletedQuantity: 0.0` / `Quantity: 0.0` even after closure — it's a
placeholder that never gets touched by the count flow. A **second**
row (`Sequence: 2`, `CreatedTimestamp` matching `initiateCount`'s
timestamp, `ItemId: "123459"`) is the one that actually carries the
real values: `Quantity: 150.0`, `CompletedQuantity: 150.0` — an exact
match, `Status: "8000"`. So checking only the first `TaskDetail` array
element (the natural first instinct) gives a false negative; the
count-relevant row must be identified by `ItemId` being non-null (or
by matching `CreatedTimestamp` to the count run), not by array
position.

Also reconfirmed via the other read-only endpoints, all consistent:
`locationCountInfo.CycleCountPending: false`,
`inventoryCountRun.Status: 80` (Booked), `inventoryCountResult.Status:
"Booked"` with `OriginalQuantity`/`CountQuantity` both `150`,
`VarianceQuantity: 0`.

**Wired into the backend (2026-08-09).** Since the user didn't have an
open real WM-scheduled Cycle Count task to test against yet at the
time, wiring was done conservatively rather than switching the proven
ad hoc path over to unconfirmed behavior:

- `mawm_client.trigger_end_count(location_id, count_run_id, task_id,
  token, org, location=None)` — new function, the confirmed
  `/count/endCount/trigger` call.
- `task_service.complete_cycle_count_line()` and
  `complete_cycle_count_location()` both gained an `is_tasked: bool =
  False` parameter. At the final step, `is_tasked=True` calls
  `trigger_end_count()` instead of `end_count()`; everything else in
  the chain (initiate/validate/accept/persist, atomicity, polling) is
  completely unchanged either way. Default `False` means every
  existing ad hoc caller behaves exactly as before — **confirmed via
  live regression test** after this change: exact-match count at
  `A1AC0123` (item `6000108`, 240 = 240) still books correctly
  (`Count Initiated` → `Booked` after the normal short async delay via
  `check_cycle_count_status()` polling).
- `/api/complete_cycle_count_line` and
  `/api/complete_cycle_count_location` both accept an optional
  `isTasked`/`is_tasked` boolean in the POST body (default `false`,
  forwarded straight through) so the wired-up path can be exercised
  directly once real task data exists, without needing the UI.

**Confirmed end-to-end, fresh, real tasks (2026-08-09).** The user
found a genuine task-creation call —
`POST /inventory-management/api/inventory-management/count/cycleCountTask/create`,
body a bare array of location IDs (e.g. `["A1AC1201"]`) — that creates
a real WM Cycle Count task for a location after a short delay and sets
the location's `CycleCountPending` lock, with a lot of admin
configuration behind it the user set up outside this app. Used it to
create two fresh tasks, `CCNTINM000548` (`A1AC0123`) and `CCNTINM000549`
(`A1AC1201`), then ran `task_service.complete_cycle_count_line(...,
is_tasked=True)` directly (bypassing the UI, per the plan below) for a
single-item exact-match happy-path test on each — **both fully
confirmed**, count and task together, first try:

- `CCNTINM000548`/`A1AC0123`, item `6000108`, counted `240` (matches
  on-hand exactly): count run `CNT000802` reached
  `inventoryCountResult.Status: "Booked"` in ~3s;
  `task/api/task/task/search` on `CCNTINM000548` showed `Status: 8000`
  and the real `TaskDetail` row (`ItemId: "6000108"`)
  `CompletedQuantity: 240.0` = `Quantity: 240.0`, stable across 18s of
  polling. `locationCountInfo.CycleCountPending` cleared to `false`.
- `CCNTINM000549`/`A1AC1201`, item `4000087`, counted `40` (exact
  match): identical result shape — `Booked` in ~3s,
  `Task.Status: 8000`, real `TaskDetail.CompletedQuantity: 40.0` =
  `Quantity: 40.0`.

**Worth noting**: these two tasks are a different flavor from
`CCNTINM000023` tested earlier — `TransactionId: "Cycle Count"` (not
`"Recount"`) and `AssignedTaskPoolId: "SystemTaskPool"` (not `"Task
Interleaving"`), which is the exact same `AssignedTaskPoolId` value
previously used as the signature of an ad hoc *byproduct* task
(`CCNTINM0000000021`). The user flagged in advance that this
`TransactionId` "may or may not work as expected" — it worked cleanly
for both single-item happy-path tests, but this is a second, distinct
task-creation path from `CCNTINM000023`'s, so it's not yet proof every
task-creation flavor behaves identically. **Per explicit instruction,
did not chase or "fix" anything speculative here** — both tests were
clean, so there was nothing to react to.

Both tests above are single-item, exact-match only.

**Within-tolerance and out-of-tolerance also confirmed (2026-08-09),
same session**, each via a *freshly created* task (using
`cycleCountTask/create` per above, confirming after a few seconds'
wait that a new open task actually appeared before counting against
it — not just reusing an old one):

- **Within tolerance**: created `CCNTINM000550` for `A1AC1201`
  (on-hand `40`), counted `39` (off by 1). `acceptQuantity`-level
  behavior matched ad hoc exactly — count passed through an
  intermediate `"In Booking"` stage before reaching
  `inventoryCountResult.Status: "Booked"` at ~9s, `varianceQty: -1`.
  **The task closed to `Status: 8000` almost immediately (~3s) — before
  the count itself finished booking.** Real `TaskDetail`
  `CompletedQuantity: 39.0` = `Quantity: 39.0` (the *counted* value,
  not the original on-hand). Task closure and count booking are
  evidently independent/asynchronous of each other, not sequenced.
- **Out of tolerance**: created `CCNTINM000551` for `A2AC1201`
  (on-hand `20`), counted `10` (-50% variance). Immediately came back
  `Status: "Pending Booking"`, exactly like ad hoc. **Confirmed stuck
  there across 18s of polling** — `inventoryCountRun.Status: 35`,
  `BookedDateTime: null`, `locationCountInfo.CycleCountPending`
  stayed `true` throughout, real inventory unchanged at `20`.

  **But the task still closed anyway** — `Task.Status: 8000` from the
  very first poll (~3s), real `TaskDetail.CompletedQuantity: 10.0` =
  `Quantity: 10.0` (the counted, *not-yet-applied* quantity), stable
  across every subsequent poll. **This answers the open question from
  the section above, and it's worth flagging clearly rather than
  smoothing over**: `trigger_end_count()` closes the Task Management
  record regardless of whether the underlying inventory count ever
  actually books. A supervisor looking at Task Management alone would
  see this Cycle Count task as "Completed" even though the real
  inventory correction is still sitting unresolved, pending their own
  manual booking decision, exactly the scenario `Pending Booking`
  exists to gate. Task status is **not** a reliable signal for
  "the count was actually applied" — only `inventoryCountResult.Status
  == "Booked"` (or `locationCountInfo.CycleCountPending == false`) is.
  This app's own UI must keep surfacing count/booking status
  independently of task status once tasked cycle count is wired into
  it — never treat a closed task as proof the count succeeded.

Multi-item locations under a real task are now confirmed too — see
the section immediately below.

**Superseded — both since resolved, noted here only to avoid confusing
a future read-through.** At the time this was written, neither gap was
closed yet. Both were: the search-routing gap was closed later this
session (see "Tasked Cycle Count UI wiring"), and the `is_tasked` flag
itself was later retired entirely — see "Ad hoc completion now always
closes the real task too — is_tasked retired," near the end of this
document. `trigger_end_count()` is now called unconditionally for
every cycle count completion, so there's no flag left to wire up.

## Multi-item location under a real task — confirmed, plus a genuine screenflow gotcha (2026-08-09, tenth session)

**Test location**: `A1AC0924` (same one ad hoc proved atomicity on —
item `5000001` "10 Pack SD Rice", item `5000002` "10 Pack SD Wheat").
Each test created a fresh real task via `cycleCountTask/create`,
waited ~6s, and independently confirmed via `task/api/task/task/search`
that a new open task actually existed before counting against it.

**Happy path — fully confirmed, clean.** `CCNTINM000552`, both items
counted at exact on-hand (`5000001`: 1739, `5000002`: 750) via
`complete_cycle_count_location(..., is_tasked=True)`. Both items
reached `inventoryCountResult.Status: "Booked"` within 3s; `Task.Status`
closed to `8000`; **both** items got their own real `TaskDetail` row
(not just one) with `CompletedQuantity == Quantity` matching each
item's own counted value (`1739.0`/`750.0`). Confirms the
per-item-row-created-during-the-count pattern (documented above for
single-item) generalizes correctly to multiple items under one task.

**Out of tolerance — fully confirmed, matches ad hoc exactly.**
`CCNTINM000553`, `5000001` counted `1000` (way off from 1739),
`5000002` counted `750` (exact). Confirmed live: **both** items ended
up `"Pending Booking"` — including `5000002`, which matched exactly —
exactly like ad hoc's own "one item off holds the whole location"
finding. `inventoryCountRun.Status: 35`, never booked, real on-hand
unchanged for both items (1739/750), location stayed locked. **The
task still closed to `Status: 8000` anyway**, same as the single-item
out-of-tolerance case — this generalizes too: task closure is
independent of count booking regardless of item count.

**Within tolerance — confirmed the *intended ad hoc atomicity behavior
holds*, but ran into a genuine screenflow gotcha along the way that's
worth documenting rather than fixing.** Tried to test `5000001` off by
15 (0.9%, expected within tolerance) with `5000002` exact, expecting a
clean parallel to ad hoc's within-tolerance case. Created a third task,
`CCNTINM000554`, and confirmed it existed before testing — but because
`A1AC0924` was **still locked from the unresolved out-of-tolerance run
above** (`CNT000807` never got booked or otherwise resolved — nothing
ever clears a genuinely out-of-tolerance `Pending Booking` run except a
supervisor), `initiateCount` did not start an independent fresh count
for the new task. Instead:

- `CountRunId` stayed `CNT000807` — the *same* stuck run from the
  out-of-tolerance test — while `TaskId` came back as a **brand new**
  ID (`CCNTINM000554`, then — confirmed by calling `initiateCount`
  again standalone afterward purely to inspect this — `CCNTINM000555`
  the very next call). **Every `initiateCount` call against a location
  stuck in unresolved Pending Booking appears to mint a fresh TaskId
  every time**, while reusing the same underlying stuck `CountRunId`.
- Completing the chain with the new "within tolerance" quantities
  (`persistCountDetails`) overwrote `CNT000807`'s stored counted
  values with the new attempt's numbers — `inventoryCountResult`
  now shows `1724`/`-15 variance` for `5000001`, not the original
  `1000`/`-739` — but **the run's own status stayed `"Pending
  Booking"`** (it didn't re-evaluate to "within tolerance" and book,
  it just kept the same terminal status with newer numbers sitting
  under it).
- **`trigger_end_count()` still closed the new task (`CCNTINM000554`)
  to `Status: 8000` anyway**, with its own `TaskDetail` showing the
  new attempt's values (`1724.0`/`750.0`) as if genuinely completed.
  Meanwhile `CCNTINM000553` (the *original* out-of-tolerance task)
  independently still shows `Status: 8000` too, but with **its own**
  `TaskDetail` frozen at **its** attempt's values (`1000.0`/`750.0`) —
  two different "Completed" tasks now exist for the same location,
  each showing different, both-stale counted quantities, neither of
  which matches the real underlying count state.
- `CCNTINM000555` (created purely by the standalone inspection
  `initiateCount` call, nothing else run against it) never appeared in
  `task/api/task/task/search` even after a further 5s wait — left
  alone, not investigated further or completed, per explicit
  instruction not to chase or fix screenflow oddities reactively.

**Net assessment, stated plainly**: this is not a bug introduced by
this app's wiring — `is_tasked=True` did exactly what it's supposed to
at each individual call. It's a real characteristic of how MAWM's
`cycleCountTask/create` + `initiateCount` behave when repeatedly
invoked against a location that's already stuck in unresolved Pending
Booking: each attempt spins up a new, independently-"completable" Task
Management record layered on top of the same broken underlying count,
and `trigger_end_count()` will happily mark each one "Completed" even
though none of them reflects whether the actual inventory was ever
corrected. **A supervisor (or this app, once built) relying on Task
Management status to know "is this location's count resolved" would be
badly misled** by a stuck location — multiple "Completed" tasks would
exist while the real count sits permanently unresolved. This sharpens
the caveat already recorded above (task status isn't proof a count
booked) into something with a concrete, reproducible multi-task
trail attached. Genuinely testing a *clean* multi-item within-tolerance
scenario needs either a location that hasn't already been driven into
Pending Booking, or the existing stuck run resolved by a supervisor
first — neither attempted here, per instruction not to react
reflexively.

**Follow-up (2026-08-09, same session)**: the user rejected the
Pending Booking runs in the real WM UI (a supervisor-level action, not
something this app exposes), confirmed live by `CycleCountPending`
clearing to `false` and `inventoryCountRun.Status` moving to `70`
("Booking Rejected"). Created a fourth task, `CCNTINM000556`, on the
now-clean location and re-ran the same attempt (`5000001` at `1724`,
15 off from 1739; `5000002` exact). This time `initiateCount` correctly
returned a **genuinely fresh** `CountRunId` (`CNT000808`) — confirming
the earlier TaskId-churn/stale-run behavior really was specific to an
already-stuck location, not a general bug. But **the fresh run also
went straight to `Pending Booking` and stayed there for 24s of
polling**, real inventory untouched. So the -15/1739 (0.86%) variance
that was assumed "within tolerance" (by analogy with `A1AC1201`'s
-1/40 = 2.5% and an older ad hoc test's -8/898 = 0.9%, both of which
did book) turned out to exceed **this item's** actual tolerance
threshold — tolerance is evidently configured per item/location, not
a single global percentage, so past passing examples don't reliably
predict a new item's threshold. The atomic hold (`5000002`, exact
match, held too) and task-closes-anyway (`Task.Status: 8000` despite
the run staying `Pending Booking`) behaviors both reconfirmed cleanly
on this fresh run — so the *mechanism* is solidly proven twice now;
only the specific "land inside this item's tolerance band" outcome is
still unachieved. Location `A1AC0924` is now stuck again
(`CNT000808`, `Pending Booking`) pending the same manual
rejection/booking decision.

**Within tolerance, finally clean (2026-08-09, same session)**: the
user rejected `CNT000808` too (same manual WM UI action — confirmed
live, `CycleCountPending` back to `false`), then asked to retry with
just 1 unit off, "to ensure that it works as expected." Created a
fifth task, `CCNTINM000557`, confirmed open, and counted `5000001` at
`1738` (-1/1739, 0.06%) with `5000002` exact. **Fully clean this
time**: `complete_cycle_count_location()`'s immediate response showed
`"Count Complete"` (`StatusKey: 30`) for both items — the same
previously-documented ad hoc multi-item intermediate status, not
`Pending Booking` — and within ~3s both items reached
`inventoryCountResult.Status: "Booked"`, `varianceQty: -1`/`0`.
Independently confirmed: real on-hand for `5000001` actually moved to
`1738` (5000002 stayed `750`), `inventoryCountRun.Status: 80`
(Booked) with `BookedDateTime` populated, `Task.Status: 8000`, and
**both** `TaskDetail` rows correctly show `CompletedQuantity ==
Quantity` matching each item's own counted value. Location unlocked
afterward. This closes out the last open piece — **all three
tolerance outcomes (happy path, within-tolerance, out-of-tolerance)
are now confirmed at both the single-item and multi-item level**,
matching ad hoc's own behavior exactly in every case except the one
documented caveat: task closure is independent of count booking, so
task status alone is never proof a count was actually applied.

**Status as of end of tenth session — both items below explicitly
deprioritized by the user** ("my demo environment has stale data and
there could be some bad tasks... we can always lock down the UI"),
not because they were resolved:
1. A task created via a real WM scheduling flow (not
   `cycleCountTask/create`) with a different `TransactionId`/
   `AssignedTaskPoolId` than both flavors tested so far — still only
   two flavors confirmed. Low priority; revisit if one turns up.
2. The stuck-location multi-task-trail guard (surfacing an existing
   `Pending Booking` state up front, before minting another task) —
   still not built. Deliberately not blocking anything else on it.

UI/search-routing work proceeded anyway (per explicit instruction to
build it despite the above) and is now complete — see "Tasked Cycle
Count UI wiring" and the two follow-up fix sections below, plus
"Ad hoc completion now always closes the real task too" (which also
made the `is_tasked` distinction this whole section was originally
about moot — every completion, ad hoc or tasked, now goes through the
same call). **As of the end of the tenth session, Cycle Count (ad hoc
and tasked, single-item and multi-item, all three tolerance outcomes,
full UI) is considered done** — only the two explicitly-deprioritized
items above remain, both by the user's own choice, not as unresolved
bugs.

## Tasked Cycle Count UI wiring (2026-08-09, tenth session)

Per explicit instruction, items 1 and 2 above (the stuck-location
multi-task-trail guard, and testing a different task-creation flavor)
were deliberately deprioritized — "my demo environment has stale data
and there could be some bad tasks... we can always lock down the UI to
only allow TransactionId: 'Cycle Count', SystemTaskPool to ensure that
we dont even try to process bad tasks." That's exactly what got built.

**Search routing** (`task_service.py`): the existing "Task Id or iLPN"
search box (`resolve_search()`/`resolve_search_multi()`, behind
`/api/load_task`) previously found a real Cycle Count task fine via
`search_task()` but rendered it through the generic Putaway-style
`_build_task_response()` path — exactly the gap flagged earlier this
session ("it would fall into the generic Putaway-style task path
instead"), since a Cycle Count task's own `TaskDetail` carries no item
until counted. Fixed: `resolve_search()` now checks
`TransactionTypeId` right after `search_task()` succeeds; if it's
`"Cycle Count"`, control passes to the new `_resolve_cycle_count_task()`
instead of `_build_task_response()`.

- `_cycle_count_task_signature_ok(raw_task)` — the lockdown itself:
  `TransactionId == "Cycle Count"` and `AssignedTaskPoolId ==
  "SystemTaskPool"`, exactly the one confirmed-safe flavor from
  `cycleCountTask/create`. Anything else (including the earlier
  `"Recount"`/`"Task Interleaving"` task, and genuine ad hoc byproduct
  tasks that share `SystemTaskPool` but not `TransactionId`) is
  refused with a specific error rather than guessed at.
- **Important gotcha found and worked around**: `TransactionId` on a
  Task record is **not stable** — it gets overwritten by whichever
  transaction last processed the task. A task created with
  `TransactionId: "Cycle Count"` reads back as `"Cycle Count
  Active-API"` (this app's own constant) once *this app* has completed
  it. So the signature check is only meaningful for a task that hasn't
  been run through this app's completion chain yet — which is exactly
  the real use case (searching for an open task before running it).
  Confirmed live against a genuinely fresh, untouched task
  (`CCNTINM000558`) to verify this, rather than reusing an
  already-completed one (which would have shown the overwritten
  value and given a false read on the check).
- `_resolve_cycle_count_task()` — once the signature passes, extracts
  the task's location (`SourceLocationId`, falling back to
  `BeginLocationId` — confirmed live `SourceLocationId` is always the
  populated one), delegates to the existing `resolve_cycle_count_location()`
  (unchanged, exactly what ad hoc already uses), then tags the result
  and every line with `taskId`/`isTasked: true` and the task's real
  `taskStatus`/`taskStatusLabel`.
- `resolve_search_multi()` needed **zero changes** — it already treats
  whatever `resolve_search()` returns generically (keys off
  `result['mode']`/`result.get('taskId')`), so a `mode: "cycle_count"`
  result slots into its existing group-wrapping loop without any
  special-casing. Confirmed live via both `resolve_search()` directly
  and the actual `resolve_search_multi()` entry point `/api/load_task`
  uses.
- **Error message fix**: `resolve_search_multi()` originally collapsed
  every per-token failure into a generic "No task or iLPN found for:
  X" — which would have made the lockdown refusal read exactly like a
  typo, even though the task genuinely exists and was deliberately
  refused. Fixed: for a single-token search (the common case), the
  specific failure reason (e.g. `_resolve_cycle_count_task()`'s
  "unsupported configuration" message) is surfaced directly instead of
  the generic fallback. Multi-token searches still collapse into the
  generic list message, unchanged.

**Frontend** (`app.js`): the "Task Id or iLPN" search box's existing
`fetchAndRenderTask()` now detects when every returned group has
`mode: "cycle_count"` and routes to the same `renderCycleCountGroups()`
table ad hoc already uses, instead of the generic task table — a
single-item location is "a group of 1" either way, so no new rendering
logic was needed, just routing. Three supporting changes:
- `state.lastSearchIsTaskedCycleCount` (new) — both ad hoc and tasked
  cycle count share `state.lastSearchMode: "cycle_count"` (so every
  existing button/completion check, e.g. Complete Line/Complete All
  gating, keeps working unchanged for both), but `reloadCurrentSearch()`
  needs to know which endpoint to re-fetch from (`/api/search_cycle_count`
  for ad hoc, `/api/load_task` for tasked) — this flag is the only
  place that distinction still matters.
- `completeCycleCountGroupAction()` now sends `isTasked: !!group.isTasked`
  in the `/api/complete_cycle_count_location` payload — the backend
  flag wired in earlier this session (`trigger_end_count()` vs
  `end_count()`), now actually reachable from the UI for the first
  time.
- `renderCycleCountTaskMeta()` shows the real `Task <id>` / `Status
  <badge>` header for a tasked group (matching the generic task view's
  own header), instead of ad hoc's plain Location-only header.

**Mixed-mode multi-search not specially handled** — if a batch search
mixes a Cycle Count TaskId with a regular Putaway TaskId/iLPN, it falls
back to the generic task table (since not every group is `cycle_count`
mode), which would misrender the cycle-count group. Realistic usage is
one type at a time; not solved preemptively per "no premature
abstraction" — flagged as a known limitation, not fixed.

**Confirmed live end-to-end through the actual browser UI** (not just
direct Python calls), using a genuinely fresh task created for this
test, `CCNTINM000558` (`A1AC0123`, item `6000108`, on-hand `240`):
1. Typed `CCNTINM000558` into the real search box — header correctly
   showed `Task CCNTINM000558` / `Location A1AC0123` / `Status Ready
   For Assignment`, rendered through the cycle-count table (item
   `6000108` pre-filled, description "Dockside Oxford Shirt").
2. Entered `240` (exact match), clicked **Complete Line** — UI showed
   "Still processing (Count Initiated)", then resolved to "Location
   A1AC0123 booked." within ~5s, matching ad hoc's own progressive
   polling UX exactly.
3. **Independently verified** (never trust the UI banner alone):
   `Task.Status: 8000`, real `TaskDetail.CompletedQuantity: 240.0 ==
   Quantity: 240.0`, real on-hand still `240` (correct, exact match).
4. Searched the non-conforming `CCNTINM000023` (`"Recount"`/`"Task
   Interleaving"`) in the same box — correctly refused with the
   specific message (`"Cycle Count task CCNTINM000023 has an
   unsupported configuration (TransactionId='Recount',
   TaskPool='Task Interleaving') — not processed here yet."`), not a
   crash or a misleading generic "not found."
5. Regression-checked a known real Putaway TaskId (`IBPWIBPT0929`)
   still resolves `mode: "task"` exactly as before — the new branch is
   additive, existing task types are untouched.

Version bumped to `v0.13.0` for this change (real new user-facing
capability, not just a doc/investigation update).

## Tasked Cycle Count: live header status + view-only completed tasks (2026-08-09, tenth session)

Real user testing against the v0.13.0 UI wiring above surfaced two
issues, both fixed the same session:

**Bug 1 — header Task Status never updated after completion.** The
header (`renderCycleCountTaskMeta()`) only rendered once at search
time, from that moment's `taskStatus`. Nothing refreshed it
afterward, so it stayed frozen at e.g. "Ready For Assignment" forever
even after the task actually closed — misleading, since (per the
already-documented caveat) task closure can happen well before or
independent of the count itself booking.

Fixed: `check_cycle_count_status()`/`check_cycle_count_location_status()`
(the poll targets `pollCycleCountGroupStatus()` already calls every
2s) gained an optional `task_id` parameter — when present (only for a
tasked group; ad hoc passes none and pays no extra cost), a shared new
`_attach_polled_task_status()` helper re-queries `search_task()` and
attaches fresh `taskStatus`/`taskStatusLabel` to the response. The
frontend now reads that off every poll tick and calls
`renderCycleCountTaskMeta()` again — confirmed live: header correctly
flipped from "Ready For Assignment" to "Completed" a few seconds after
Complete Line, in step with the count itself reaching "Booked."

**Bug 2 — re-searching a completed task threw a confusing hard
error.** Exactly the mechanism flagged as a risk when the lockdown was
first built: `TransactionId` on a Task record isn't stable — it gets
overwritten to this app's own `CYCLE_COUNT_TRANSACTION_ID`
("Cycle Count Active-API") once *this app* processes the task via
`trigger_end_count()`. So re-searching an already-completed task hit
the original `_cycle_count_task_signature_ok()` check (which only
recognized the original creation-time `TransactionId: "Cycle Count"`)
and got refused with "unsupported configuration" — technically true
but deeply misleading, since the task was genuinely supported and had
simply finished.

Fixed: replaced the boolean signature check with
`_classify_cycle_count_task(raw_task) -> "actionable" | "view_only" |
"unsupported"`. `AssignedTaskPoolId` must still be `"SystemTaskPool"`
either way (the actual lockdown boundary); beyond that, `Status ==
"8000"` (Completed) alone is treated as `"view_only"` — deliberately
**not** conditioned on `TransactionId`, since a completed task poses
zero risk of "processing a bad task" regardless of what changed its
`TransactionId` (there's nothing left to process). An open task still
requires the original `TransactionId: "Cycle Count"` to be
`"actionable"`; anything else stays `"unsupported"` and refused as
before.

**View-only rendering — and the "show what the task did" ask, done
via a genuinely small amount of new code.** For `"view_only"`, a new
`_cycle_count_task_history_result()` looks up the specific
`inventoryCountRun` whose own `TaskId` matches (a location can carry
several runs over time — filters to the right one, not just "the
latest for the location"), pulls its `inventoryCountResult` rows, and
builds each item's result via the **already-existing**
`_cycle_count_result_response()` — the exact same shape used for a
live in-progress result. Each line gets `line.result` (falls back to
a plain `{success: true, status: "Completed"}` if no history is found,
so view-only always holds even without history) and
`line.quantity = <the real counted amount>`.

On the frontend, `renderCycleCountGroups()` now applies any
`line.result` immediately after rendering by calling the **already-
existing** `setCycleCountResultCell(..., true)` — which already had
the side effect (built for the live-completion case, unrelated to this
feature) of disabling the item/qty inputs and marking a line "done."
That one existing side effect is the *entire* read-only mechanism —
`isCycleCountGroupDone()` already keeps a done group out of both
Complete Line and Complete All (confirmed live: Complete Line disabled
immediately; Complete All stays clickable but correctly reports "No
outstanding lines to complete" since `allOutstandingCycleCountGroups()`
already filters out done groups — pre-existing behavior, not
something new this needed). `cycleCountLineRowHtml()`'s qty input now
seeds its `value` from `line.quantity` when present (previously always
blank) so it displays the counted amount instead of an empty box
before getting disabled. No new gating logic, no new CSS, no new
"read-only" concept in the frontend at all — entirely reused machinery
from live completion.

**Confirmed live**: re-searched `CCNTINM000560` (completed moments
earlier in the same test) — header showed `Status Completed`
immediately, item (`4000087`) and qty (`20`) inputs both correctly
`disabled` with the real counted values, result cell showed
`Booked` / `20 → 20` / `0 ($0)` (the real historical outcome, not a
placeholder), Complete Line `disabled`. Complete All was initially
left enabled at this point (functionally harmless, per
`openAllCycleCountLinesModal()`'s own emptiness check — see the next
section for the follow-up fix once the user flagged it looking wrong).

## Two more polish fixes from real use (2026-08-09, tenth session)

**Complete All stayed visually enabled on an all-done batch.**
`allOutstandingCycleCountGroupsValid()` deliberately returns `true`
when there's nothing outstanding ("let the click through to show
'nothing to do'" — a real, intentional 2026-08-08 design choice, not a
bug in itself), but `updateCycleCountLineActionButtons()` used that
return value directly to gate the button, so a fully-completed batch
(e.g. a re-searched already-`Completed` task, or any location that's
already fully done) left Complete All looking clickable — confirmed
functionally harmless (`openAllCycleCountLinesModal()`'s own
emptiness check catches it and just shows "No outstanding lines to
complete") but visually confusing, per the user. Fixed:
`updateCycleCountLineActionButtons()` now separately checks
`allOutstandingCycleCountGroups().length` before trusting
`allOutstandingCycleCountGroupsValid()`'s own "true when empty"
return — greys the button out whenever there's nothing outstanding,
while `allOutstandingCycleCountGroupsValid()` itself is unchanged
(still used, and still correct, for the "some things outstanding but
not yet filled in" case). Confirmed live: re-searching the completed
`CCNTINM000560` now shows both Complete Line and Complete All
`disabled`.

**"Still processing... status will keep updating" was misleading for
Pending Booking specifically.** `cycleCountGroupResponseSummary()`
builds the action-status banner shown right after Complete
Line/Complete All. Across every out-of-tolerance test this session —
single-item and multi-item, ad hoc and tasked, no exceptions — a
`Pending Booking` result has been a **permanent** terminal state, not
a transient one: MAWM never resolves it on its own, it just sits
locked until a supervisor manually books or rejects it. The generic
"status will keep updating" phrasing was actively wrong for this
specific case. Fixed: when every item in the group's response already
shows `Pending Booking`, the banner now reads "Out of tolerance —
pending supervisor booking. Won't resolve on its own." instead.
**Scope note**: only the *message* changed — `pollCycleCountGroupStatus()`
itself still polls up to the full 60s window regardless of status,
deliberately left alone (matching the existing docstring's own
caution that there's "no way to tell [transient vs. stuck] apart from
the status text alone" — confirmed strongly enough now to fix the
message, not confirmed strongly enough to want to silently stop
watching a row that's supposedly stuck, in case a real exception ever
turns up). Confirmed live: counting `A1AC0123` (on-hand `240`) at `50`
under a fresh task showed the new banner text immediately, with the
row itself still correctly showing `Pending Booking` / `240 → 50` /
`-190 ($950)`.

## Ad hoc completion now always closes the real task too — is_tasked retired (2026-08-09, tenth session)

**The user found this live**: scanned a location by mistake instead of
the real Cycle Count TaskId (the ad hoc path, `is_tasked` defaulting
to `False`) — the count itself booked fine, but the real WM task
stayed stuck "In Progress" forever, since ad hoc still called
`end_count()`, not `trigger_end_count()`. They then completed the
*same* location on the real mobile RF device, which showed `"Cycle
count already exists for the location (INM::207)"`, and — after
confirming — correctly booked the count **and** closed the task. The
ask: figure out what `INM::207` tells us and use it to close the task
regardless of how the count was reached.

**Investigated live before writing any code** (per explicit
instruction to summarize first):

- Called our own ad hoc `initiateCount()` (exactly what the location-
  search path already does) against a location with a real
  pre-existing task. **No `INM::207` or any warning came back in the
  API response at all** — `messages.Message: []`. That warning is
  apparently mobile-device-UI-specific, not visible through any of the
  endpoints this app already calls. But the response's own `TaskId`
  **was** the real pre-existing task's id — MAWM's own dedup, with no
  extra signal needed, confirming (yet again, first found with
  `CCNTINM000023` much earlier this session) that ad hoc `initiateCount()`
  auto-attaches to a real task whenever one exists for the location.
- Tested whether `trigger_end_count()` — until now only called when
  `is_tasked=True` — is actually safe to call **unconditionally**:
  - Against that same real-task location: closed the task correctly
    (`Status: 8000`).
  - Against a genuinely task-less, never-counted location
    (`A1AC0405`, confirmed zero prior `inventoryCountRun` rows):
    `trigger_end_count()` still returned `200 success: true` and
    booked the count normally — no task-related error, no side
    effect. (Also confirmed *why* this is safe: `search_task()` finds
    **nothing** for a pure ad hoc count's synthesized `TaskId` — it's
    not a real, searchable Task record at all, so there's nothing for
    `trigger_end_count()` to break.)
  - Also found, incidentally: the task's own `TransactionId` gets
    overwritten to this app's `CYCLE_COUNT_TRANSACTION_ID` at the very
    first call, `initiateCount()` itself — not later at persist/trigger
    time as previously assumed.

**This resolved the open "is trigger_end_count safe universally?"
question from earlier this session, and made the fix simpler than
detecting `INM::207` at all**: since `complete_cycle_count_line()`/
`complete_cycle_count_location()` already extract `task_id` from
`initiateCount()`'s own response regardless of caller intent, and
`trigger_end_count()` is confirmed safe either way, both functions now
**always** call `trigger_end_count()` — the `end_count()`/`is_tasked`
branch is gone entirely. This retired `is_tasked` at the completion-call
level end to end: removed from both `task_service.py` functions'
signatures, both `/api/complete_cycle_count_line` /
`/api/complete_cycle_count_location` routes, and the frontend's
`completeCycleCountGroupAction()` payload. `end_count()` itself is now
unused (removed from `task_service.py`'s imports) — `mawm_client.end_count()`
is left in place since it's still a real, working, confirmed API
wrapper, just no longer called from this app.

**What did *not* change**: the search-time lockdown/classification
(`_classify_cycle_count_task()`, the `"actionable"`/`"view_only"`/
`"unsupported"` split) — that's a separate concern about what's safe
to search by TaskId *directly*, unrelated to what an ad hoc/location
search's own `initiateCount()` auto-attaches to internally.

**Confirmed live, three ways**:
1. Regression: multi-item ad hoc completion (`A1AC0924`, both items
   exact match) still books correctly with the new unconditional
   `trigger_end_count()` — no regression from removing `end_count()`.
2. **The actual reported bug, reproduced and fixed**: created a fresh
   real task (`CCNTINM000567`, `A1AC1201`), completed it via
   `complete_cycle_count_line()` **by location**, exactly like the
   user's mistaken search — count booked (`Booked`, real on-hand
   updated) **and** the task closed (`Status: 8000`), both without any
   `is_tasked`/TaskId involvement at all.
3. Same thing through the actual browser UI: searched `A1AC0312` by
   **location** (header showed the plain ad hoc "Location A1AC0312",
   confirming the ad hoc path was genuinely taken, not the tasked
   one), entered an out-of-tolerance quantity, completed — banner
   correctly progressed from "Still processing (Count Initiated)" to
   the new "Out of tolerance — pending supervisor booking. Won't
   resolve on its own." message (confirming the polling-banner fix
   above works together with this one), and independently confirmed
   the real task (`CCNTINM000573`) still closed to `Status: 8000`
   despite the count itself landing in `Pending Booking` — the same
   "task closes regardless of whether the count actually books"
   behavior documented earlier, now reachable from the ad hoc path
   too.

**Verification methodology note**: while investigating, found that a
real task reused across multiple count attempts on the same location
can accumulate **more than one** item-carrying `TaskDetail` row (not
just one placeholder + one real, as documented earlier) — e.g.
`CCNTINM000567` ended up with three rows total, two carrying the same
`ItemId` from different count attempts. The correct one to check is
whichever has the latest `CreatedTimestamp`, not just "the first row
with a non-null `ItemId`." This only affects manual live-verification
scripts (grepping `TaskDetail` by hand) — nothing in the app itself
reads `TaskDetail` this way.

**Not retroactively fixed**: any task already left stuck "In Progress"
from before this fix (like the one the user manually closed via
mobile) needs the same manual resolution as before — this only
prevents it from happening again going forward.

## Picking — v1 built and confirmed live (2026-08-10, eleventh session)

Real `TransactionTypeId` is `"Pick"`, not `"Picking"` (this app's
earlier `TASK_TYPES` constant was never live-verified — a guess). Real
API surface confirmed live this session, via a mix of a Glean-sourced
starting point and this app's own investigation once the API itself
disagreed with the documentation (see below):

- `POST {host}/pickpack/api/pickpack/pick/commitPickMove` — commits
  one task-detail line. **One call does everything**: inventory move,
  task-detail completion, and task auto-close when it's the last open
  line — confirmed live, no separate "end"/"trigger" call needed
  (unlike this app's Cycle Count feature). Also confirmed
  **synchronous** — the real outcome is visible on an immediate
  re-query right after the call, no polling needed (unlike Cycle
  Count's async booking).
- `POST {host}/pickpack/api/pickpack/olpn/search` — the oLPN header +
  `OlpnDetail` array (used for this app's own independent
  verification, not part of the commit chain).
- `pickpack/api/pickpack/pick/byTask/fetchNextMove` — the
  fetch-before-commit "enriched move" call (Putaway's own equivalent
  is `fetchNextPutawayMoveAndStartLaborActivity`). **Confirmed live
  this session that the actual contract differs from what Glean's
  documentation search initially returned**: it's `POST` with a
  PascalCase JSON body (`{"TaskId": ..., "TransactionId": ...}`), not
  `GET` with query parameters — confirmed by reading the real Java
  stack trace a wrong-shaped request returned
  (`PickingServicesController.fetchAndProcessNextMoveByTask(Context,
  PickRequestDTO)`), not by guessing. **Not currently used by this
  app** — see below.

**The confirmed-safe scope this app actually supports (v1)**: a plain
minimal `commitPickMove` payload (`SourceContainerId`,
`SourceContainerType`, `TaskId`, `CurrentTaskDetailId`, `OlpnId`,
`TransactionId`, `CompletedQuantity`) — no `fetchNextMove` step needed
first — confirmed live and correct for every `LOCATION`-sourced line
tested this session (single-line and multi-line tasks, `UNIT` and
`PACK` UOM both, real inventory/task/oLPN-detail verified after each).

**What's excluded, and why — a real, unresolved MAWM issue, not a
guess.** An iLPN-sourced / `FullContainerAllocated: true` line
(`PICK0597`'s line 2, `TaskDetailId
d27a009a-1f6c-4026-bd04-7f09a9e60443`) reliably fails with `PPK::0513
"Quantity exceeds order line quantity"` — tried **four distinct
payload shapes**, all against a freshly-fetched, never-modified state:
a plain hand-built payload with `CompletedQuantity` at both `24`
(matching planned `Quantity`) and `1` (matching the reported UOM
conversion factor); the fully-enriched `fetchNextMove` response
verbatim with only `CompletedQuantity` changed to `24`; and the same
enriched response with `CompletedQuantity` changed to `1` (Glean's own
specific recommended combination, tested verbatim to its Python
sketch). **All four failed identically.** The fetched move consistently
shows `AllocatedQuantity: 0` despite `Quantity: 24.0` and the source
iLPN independently confirmed to hold all 24 units, fully allocated —
the working theory is that `PPK::0513` validates against
`AllocatedQuantity`, not `Quantity`, and this line's allocation is
broken or stale server-side in a way no client-side payload can work
around. Full investigation, live test transcripts, and the specific
open questions for Glean are saved in
`mawm_picking_commitpickmove_full_container_issue.md` /
`_updated.md` and `mawm_picking_fetch_enriched_commit_test.md` (the
user's Downloads folder — not checked into this repo).

Also found, separately: `PICK_INTO_CART` (cart picking) is a real,
valid MAWM execution mode the user explicitly wants to support
*later* — not excluded because it's wrong, just not yet built.
Confirmed cart-mode tasks (e.g. `PICK0637`) can have **multiple
different oLPNs on one single task** (5 lines split across 3 oLPNs) —
a structurally different complexity than the "one oLPN split across
multiple separate tasks" case found earlier (`PICK0490`/`PICK0492`,
which occurs even in the supported `PICK_INTO_OLPN` mode). Two other
execution modes exist too, also excluded for now: `PICK_INTO_TOTE` and
`PICK_INTO_ILPN`.

**Lockdown** (`task_service._classify_pick_task()`, mirroring the
Cycle Count lockdown pattern): a task is only processed when
`TaskExecutionMode == "PICK_INTO_OLPN"` **and** every one of its lines
has `SourceContainerTypeId == "LOCATION"` — anything else is refused
with a clear, specific reason rather than guessed at, per explicit
instruction ("if you know exactly the scenario that causes the issue,
you can restrict those tasks for now"). Refusal happens at the whole-
task level (not per-line) for v1 simplicity — a task with 2 fine lines
and 1 iLPN-sourced line is refused entirely, not partially rendered.

**Search**: extended the existing "Task Id or iLPN" box
(`resolve_search()`) — a real Pick TaskId routes to `_resolve_pick_task()`
exactly like a Cycle Count TaskId already did; a real oLPN routes
through a new `search_task_id_for_olpn()` (same `TaskDetail.<field>`
dotted-path query pattern as Putaway's `search_task_id_for_container()`,
confirmed live it also works for `TaskDetail.OlpnId`). **Confirmed
live**: an oLPN split across more than one task is refused with a
clear message (`"oLPN X is split across N tasks... search by TaskId
directly instead"`) rather than guessed at — matches the earlier
finding that this can happen even for supported `PICK_INTO_OLPN`
tasks, and the user's own note that it "would never" happen in a real
paper-based environment, so it's being treated the same way as the
excluded execution modes: refused clearly, not solved.

**Completion**: `complete_pick_line()` (one line) and
`complete_pick_task()` ("submit all in one shot," per explicit
instruction) — both call `commitPickMove()` and then independently
re-verify via `search_task()` (never trust the commit response alone
— confirmed live early on that a genuine success can return a
mostly-null echo). Unlike Cycle Count, **lines are independent** —
confirmed live that completing one line doesn't require the others to
be addressed first, so `complete_pick_task()` loops per line and
continues past an individual failure rather than stopping the whole
batch, and the frontend's per-line selection/completion model mirrors
Putaway's (one line at a time), not Cycle Count's atomic per-group
model.

**Frontend**: new `#pickLinesTable` (Line / Source Location / Item /
Description / Planned Qty / UOM / Completed Qty / Status), its own
render/select/complete function set (`pickLineRowHtml()`,
`selectPickLine()`, `completePickLineAction()`,
`openAllPickLinesModal()`/`confirmAllPickLines()`), reusing
`state.selectedTaskDetailId`/`getLineByTaskDetailId()` rather than a
new state field (already documented as globally unique across every
group). Completed Qty pre-fills with the planned quantity (matching
Putaway's convention) — editable for exceptions later, per explicit
instruction ("we can figure out exceptions later and start with happy
path"). Header shows the real Task Id, live status (updates after a
completion, since a line can auto-close the task), and every distinct
oLPN across the task's lines.

**Confirmed live end-to-end through the actual browser UI**
(`PICK0593`, 2 lines): searched by TaskId, both lines correctly
rendered with pre-filled planned quantities; completed line 1 alone
(banner "Completed line 1.", header status live-updated to "In
Progress"); Complete All correctly showed only the one remaining
outstanding line in its confirmation modal, completed it, and the
header updated to "Completed." Independently re-verified via direct
API: `Task.Status: 8000`, both `TaskDetail.CompletedQuantity` matching
planned exactly, real source on-hand decreased correctly at both
locations. Also confirmed live: the lockdown correctly refuses a
`PICK_INTO_TOTE` task with a clear message through the actual UI, and
searching by oLPN correctly finds and loads its task (tested against
`PICK0295`, which — an incidental discovery — turned out to itself
span 4 different oLPNs across its own 4 lines; handled correctly
without any special-casing since completion already keys off each
line's own `olpnId`, not a task-level one).

**Known limitation, not yet handled**: a multi-search batch mixing
Pick with Cycle Count or generic task/iLPN results falls back to the
generic table (same limitation already accepted for Cycle Count's own
multi-search case) — realistic usage is one type at a time.

**Remaining for later, explicitly deferred**: exceptions (short
picks, item substitution, etc. — happy path only for now, per explicit
instruction), `PICK_INTO_CART`/`PICK_INTO_TOTE`/`PICK_INTO_ILPN`
support, the iLPN/full-container `PPK::0513` mystery, the
`commitAllPickMoves` (byOlpn) taskless/paper-based path Glean
originally proposed as the eventual production target (not yet tested
at all — this session's scope stayed within the current tasked
SS-DEMO environment), and closing/`EndTargetContainer` behavior beyond
what's already been observed (every task tested so far auto-closed on
its own without it being set).

**UOM display bug, found and fixed same session**: the Pick table's
column header literally read "UOM" — inconsistent with this app's own
established convention (Putaway's equivalent column header is blank;
the unit code displays inline instead). Fixed. The *quantity/label*
itself (e.g. `PICK1907` showing `UomTypeId: "PACK"` with the raw
`Quantity` value, 3 and 2) was flagged as a possible bug but turned
out not to be one — the user captured a real mobile RF session (HAR
file, `PICK1907.har` in Downloads, not checked into this repo) for the
same task and confirmed the real WM mobile app shows the identical
"3 Packs"/"2 Packs" — i.e. no real UOM conversion is happening on
either side for this item/task combination, both correctly show the
raw base quantity with a `PACK` label. Confirmed from the mobile
session's own captured response:
`UniqueAttributeCaptureUOM: {uomConversionFactor: 1, standardQuantityUomId: "PACK"}`
— conversion factor 1, i.e. no real scaling either way. (Root cause,
for reference: this item's "PACK" `ItemPackage` entry has
`Standard: false` — this app's own existing `_package_conversion_factor()`
helper, already used for Putaway/Cycle Count display, only matches
`Standard: true` entries, so it would have fallen back to the exact
same unconverted-raw-value behavior anyway had it been applied here.)

**Reference find, not acted on**: the same HAR capture reveals the
real mobile RF app does *not* use `commitPickMove` at all — it uses
the much heavier `dmmobile-facade/services/rest/workflow/execute/...`
state-machine pattern (`EnterTask` → `Pick/AcceptItem` →
`Pick/AcceptQuantity`, repeated per line → `OutboundPutaway/AcceptLocation`
at the end — the "location scan at the end" the user described,
confirmed real, explicitly deferred: "not too worried about that
just yet"). This mirrors Putaway's own two parallel families in this
app — the workflow/state-machine one ("Path A") is commented out and
unused there in favor of the simpler execution/task REST pair ("Path
C") this app actually uses. Reassuring, not alarming: dug one level
into the mobile workflow's own request body and found
`currentMove.InventoryMove.Quantity`/`TaskDetailEaches[0].Quantity` —
the *same* `InventoryMove`/`TaskDetailEaches` shape `commitPickMove`/
`fetchNextMove` already use, just wrapped in the bigger state-machine
envelope. Confirms `commitPickMove` is the real, canonical mechanism
under the hood, not a workaround — this app's simpler REST-only
approach reaches the same underlying data model through a more direct
door, matching the same architectural choice already made for
Putaway. Worth revisiting **only** if the iLPN/full-container
`PPK::0513` mystery above is still unresolved and everything else is
exhausted — a completely different code path might not hit the same
bug — but not pursued now.

## Picking: grouped by oLPN, live oLPN status (2026-08-10, eleventh session)

Real example (`PICK1907`, from the mobile HAR capture) confirmed a
`PICK_INTO_OLPN` task can span **multiple distinct oLPNs across its
own lines** — not just a `PICK_INTO_CART` concern as first assumed.
Per explicit instruction, the Pick table now groups lines by oLPN
instead of showing one flat list — a full-width divider row (oLPN id +
status badge) precedes each oLPN's own lines, mirroring Cycle Count's
existing MIXED-row pattern (one table, grouped rows) rather than
building genuinely separate `<table>` elements. Confirmed live:
`PICK0295` (4 lines, 4 distinct oLPNs) correctly rendered as 4
separate one-line groups.

**No confirmed oLPN status-code label mapping exists** (unlike
Task/iLPN, which both already have one) — the badge shows the raw
code (e.g. `"1000"` before picking, `"7200"` once fully picked,
confirmed live) rather than guessing at a translation.

**Live status bug found and fixed the same session**: the oLPN status
badge only reflected its value at search time — after completing a
line, the real oLPN status changes (confirmed live, `"1000"` →
`"7200"`) but the badge stayed stale until a manual reload, same class
of bug as the task-status live-update fix from the tenth session.
Fixed: `complete_pick_line()` now also re-fetches the completed line's
own oLPN status (`search_olpn()`, best-effort — a lookup failure
doesn't fail the completion result) and returns it as `olpnId`/
`olpnStatus`; the frontend's new `updatePickOlpnStatus()` applies it
in place to both the DOM badge and `state.groups[...].olpnStatuses`,
called from both `completePickLineAction()` and `confirmAllPickLines()`
(the latter via each line result in `complete_pick_task()`'s own
`results` array, which already threads through `complete_pick_line()`
per line).

**Debugging note for future reference**: the first live test of this
fix appeared to fail (badge stayed frozen) — turned out to be a stale
browser tab still running the previous `app.js` from before the fix
was saved to disk (static files don't hot-reload an already-open
tab). A full page reload before retesting resolved it; not a real bug
in the fix itself. Worth remembering next time a live-tested frontend
change appears not to take effect.

**Complete All modal now shows UOM**, per explicit instruction — was
previously just `Line N — item: qty`, now `Line N — item: qty UOM`
(e.g. `"Line 1 — 4000052: 8 PACK"`), confirmed live.

**Also confirmed live, incidentally, while testing**: a genuine
business-rule exception — attempting to pick more than the real
on-hand at the source location correctly surfaced MAWM's own real
error ("Pick will drive the inventory to negative") through this
app's existing error-handling path, rather than crashing or showing a
misleading success. Not a new code path — just confirms the
already-built error surfacing works correctly for a real exception,
even though exceptions are still explicitly out of scope for this
feature's happy-path v1.

## Picking: short-pick exception mechanism confirmed (2026-08-10, eleventh session)

**The `ExceptionMove` field — already sent on every `commitPickMove`
call, hardcoded `false` — is the short-pick mechanism**, confirmed
live. The user's specific concern: if the required quantity is 8 and
the picker only picks 5, the task/oLPN must not still expect the
remaining 3.

- **Naive short pick, `ExceptionMove: false`, `CompletedQuantity`
  less than planned** (`PICK0110`, required `10`, submitted `5`):
  confirmed this does exactly what the user was worried about. Real
  inventory decremented by 5 (partial, real), but
  `TaskDetail.Status` stayed `"7000"` (In Progress), `CompletedQuantity:
  5.0` next to unchanged `Quantity: 10.0` — the system is still
  waiting for the remaining 5. Task also stayed open.
- **The fix, confirmed in a single call**: same line, but
  `ExceptionMove: true` submitted *with* the actual short quantity in
  one call (not the naive call followed by a second closing call —
  tried both, the single-call version is sufficient and simpler).
  Response includes an `INFO`-level message, `PPK::0045 "End of
  oLPN(s)"` — the same message the real mobile app's own HAR capture
  showed at the natural end of a normal (non-short) pick, i.e. this is
  MAWM's own "this line/oLPN is done" signal, not an error.
  Independently verified (`PICK0206`, required `10`, submitted `5`
  with `ExceptionMove: true`): `Task.Status: "8000"`,
  `TaskDetail.Status: "8000"`, and — the important part —
  **`TaskDetail.Quantity` itself was retroactively rewritten from
  `10.0` down to `5.0`**, so nothing is left "expecting" the original
  amount. Real on-hand decremented by exactly 5, not 10. The
  destination oLPN closed the same way: `Status: "7200"` (Packed),
  `OlpnDetail.InitialQuantity` also retroactively adjusted from `10.0`
  to `5.0`, matching `PickedQuantity: 5.0` exactly.
- **`ShortedQuantity`** (present on both `TaskDetail` and
  `OlpnDetail`) stayed `0.0`/`null` in every test — MAWM records the
  shortage by *retroactively resizing the requirement*, not by
  populating a separate shortage-quantity field. Worth knowing if a
  future reporting need wants to distinguish "this line was originally
  smaller" from "this line was shorted" — that distinction isn't
  preserved in these fields; only order/allocation-level records
  further upstream would show the original ask, if anywhere.

**Reason code — confirmed optional, not required.** Every short-pick
test above succeeded with no reason code at all. Found the real reason
code list anyway (`POST {host}/pickpack/api/pickpack/reasonCode/search`,
`Query: ""` — a *different* endpoint from Putaway's own
`PUTAWAY_REASON_CODE_SEARCH_URL`, confirmed via live 404 that
`pickpack/api/pickpack/pick/reasonCode/search` — the naive guess
mirroring Putaway's path shape — is wrong; the real one has no `pick/`
segment). 18 real codes exist, several genuinely relevant to this
scenario: `PickCancel` ("Cancel Pick Shortage Requirement"), `Short
Pick and Lock`, `Short Pick and Lock Location`, `Short pick carton`,
`Cancel` ("Cancel Shortage Requirement") — plus others clearly meant
for different scenarios (`Damaged Item`, `Substitute LPN`, `Unpick`,
etc.), not investigated further. Tested passing `"ReasonCodeId":
"PickCancel"` as a top-level sibling field (alongside `InventoryMove`/
`ExceptionMove`/`EndTargetContainer`) — accepted without error,
identical closing behavior — but the completed `TaskDetail`'s own
`ReasonCodeId` field read back `None` afterward, so **it's not yet
confirmed where (or whether) the reason code actually gets recorded**
for later reporting; only confirmed that passing one doesn't break
anything.

**Not yet decided or built**: whether/how to wire this into the UI —
options include auto-detecting a short entry (`quantity <
plannedQuantity`) and silently setting `ExceptionMove: true`, or
requiring an explicit user action (a "Short Pick" button/checkbox,
optionally with a reason-code dropdown from the confirmed list above)
before allowing a short quantity to submit at all. Flagged for
explicit discussion before implementing, per this app's own established
practice of confirming the UX shape before writing it.

## Picking: 3 reason codes live-tested clean; one location-lock side effect confirmed (2026-08-10)

Per explicit instruction, manually re-tested 3 of the 10 real
`PICK_EXCEPTION` codes through a real short pick each, checking
specifically for any unexpected WM warning or blocking prompt (not
downstream config side effects in general — those are explicitly out
of scope for now per the user's own instruction). All 3 came back
clean — same benign `PPK::0045 "End of oLPN(s)"` INFO message every
other short pick already showed, no warnings, no extra prompts:

- **`Short Pick and Lock Location`** (`PICK0101`, required `10`,
  submitted `6`) — clean completion, but has a real, confirmed side
  effect: it set `CycleCountPending: True` on the source location
  (`A1AC0924`). A later, unrelated test against that same
  location/item then failed with `PPK::0522 "iLPN/Location/Inventory
  has Condition Code [LW] and cannot be Picked"` — confirmed via retest
  against a clean, different location that this was a cascading
  side effect of the lock, not a flaw in whatever reason code was used
  the second time.
- **`PickCancel`** (`PICK0149`, required `5`, submitted `3`) — clean
  completion, no location-lock side effect (`CycleCountPending` stayed
  `None`).
- **`Short pick carton`** (`PICK0161`, required `15`, submitted `9`,
  a fresh untouched location) — clean completion, same benign INFO
  message. (An earlier attempt on the same reason code against the
  location `Short Pick and Lock Location` had just tainted failed with
  `PPK::0522` — that failure belonged to the tainted location, not this
  code; the clean retest on a fresh location confirms it.)

**Reason-code dropdown UI built** (`public/index.html`/`app.js`,
`task_service.preload_pick_reason_codes()`,
`mawm_client.search_pick_reason_codes()`) — mirrors Putaway's existing
`.reason-code-select`/`toggleReasonSelect()`/`isReasonValid()` pattern
almost exactly, scoped to `el.pickLinesBody` instead of `el.linesBody`.
Per explicit instruction, the dropdown lists **all 10** real
`PICK_EXCEPTION` codes (not just the 3 tested so far) — a `TESTED_PICK_REASON_CODES`
constant in `app.js` marks confirmed-clean codes with a trailing `*` in
the option label; update that set as more codes get manually tested
and reported back. A short pick (`Completed Qty` entered below
`Planned Qty`) reveals the dropdown and requires a real selection
(starts on an invalid "Select Reason Code" placeholder) before either
Complete Line or Complete All will submit that line; a full-quantity
pick never shows or requires it. `commit_pick_move()` now accepts
`exception_move`/`reason_code_id` and sends `ExceptionMove: true` +
`ReasonCodeId` accordingly; `complete_pick_line()`/`complete_pick_task()`
and both `/api/complete_pick_*` routes thread these through.

Live-tested end-to-end through the real browser UI (`PICK0100`,
required `10`, submitted `9`, reason code `Short pick carton`) —
completed cleanly, `Task.Status: "8000"`, `TaskDetail.Quantity`
retroactively resized `10.0` → `9.0` matching `CompletedQuantity`, same
as the earlier direct-API short-pick tests. Also re-confirmed the
plain full-quantity path (`exceptionMove: false`, no reason code) still
works unchanged after these edits.

**One transient anomaly observed once, not reproduced**: during the
first browser test (`PICK0062`), the app's own post-commit re-verify
briefly read back the line's *old* status (`"7000"`) immediately after
a successful `ExceptionMove: true` commit, so the UI reported "Line not
completed" even though the pick had actually succeeded (independently
confirmed via a follow-up query: `Status: "8000"`,
`CompletedQuantity == Quantity`, correctly resized). Every other test
this session — including a second full end-to-end UI run — saw the
correct status immediately, no delay needed. Given this app already
never trusts the commit response alone and always re-verifies (see
`complete_pick_line()`'s docstring), the only consequence of hitting
this again would be a false "failed" message on an actually-successful
short pick; worth a reload/re-search to confirm before assuming a real
failure if it's ever seen again. Not chased further — couldn't
reproduce it after several more attempts, and it may be the same class
of transient backend read lag as the `.token` auth flakiness noted
below, not something specific to the exception-move path.

**Unrelated but worth remembering**: while chasing what first looked
like a live authentication problem (`.token` genuinely valid and
unexpired by its own `exp` claim, byte-for-byte clean, yet
intermittently rejected by MAWM with `invalid_token` / "Cannot convert
access token to JSON"), the real cause turned out to be a bug in the
**diagnostic script**, not the environment — an accidental
`resolve_search(search_value, token, org, ...)` call when the real
signature is `resolve_search(token, org, search_value, ...)`. Passing
a 15-line JWT into the `org` header/query position obviously fails,
and misread as token flakiness at first because a *different*,
correctly-ordered raw call kept succeeding in between attempts. Worth
remembering next time an "auth" failure doesn't reproduce consistently
across two superficially similar calls — check argument order before
assuming the server is flaky.

## Picking: cart-picking (`PICK_INTO_CART`) supported (2026-08-10, twelfth session)

**Research first, live-tested before building anything.** `PICK0637` (a
real 5-line/3-oLPN/3-slot cart task) was fetched and inspected for its
raw `TaskDetail` shape, then `commit_pick_move()` was called directly
against all 5 of its lines — **unmodified, no new fields** — before
any code changed. Every line committed correctly, each oLPN closed
once its own lines were done (`Status: "7200"`), and the task
auto-closed on the last line — identical behavior to a plain
`PICK_INTO_OLPN` task. One thing confirmed worth remembering: the
first commit's response body echoed a **completely different line's**
`InventoryMove` data than the one actually submitted (not just
misleadingly empty, as already documented) — the real state, re-verified
independently, showed the correct line had in fact completed. Never
trust the commit response alone; this app already didn't.

**What actually changed, once the backend was proven:**
- `task_service._classify_pick_task()` — `TaskExecutionMode` allowlist
  widened from `PICK_INTO_OLPN` only to `{PICK_INTO_OLPN,
  PICK_INTO_CART}`. The per-line `SourceContainerTypeId == "LOCATION"`
  check is unchanged — still excludes the unresolved iLPN bug case.
  `PICK_INTO_TOTE` deliberately **not** added — confirmed live
  (`PICK0540`) it also carries a real per-line `OlpnId` and is
  `LOCATION`-sourced, so it would likely also "just work," but its
  destination container during picking is a `TOTE`, not an `OLPN` —
  untested, left for a future decision rather than silently folded in.
- `task_service._resolve_pick_task()` — each `olpnStatuses[olpnId]`
  entry gained `slotId`, `containerTypeId`, `containerSizeId`.
  `slotId` comes from that oLPN's own lines'
  `TaskDetail.PlannedSlotId` (confirmed live: every line sharing an
  oLPN shares the same slot value, so the first non-empty one found is
  authoritative) — **not** derived from `TaskExecutionMode` or
  `TransactionId` text, per explicit instruction, since other task
  types could plausibly use the same "pick multiple oLPNs at once"
  mechanism without being cart tasks. `containerTypeId`/
  `containerSizeId` come off the same `search_olpn()` call already
  made for status — no extra API call.
- `app.js` `pickOlpnHeaderRowHtml()` — oLPN Type/Size (e.g. `"BOX /
  MED"`) now always shown; `Slot ...` shown only when `slotId` is
  populated, i.e. purely data-driven per oLPN, matching the backend.
  `renderPickGroups()` — oLPN groups sort by slot number (extracted
  from the slot text, see below) when *any* oLPN in the result has a
  slot; a task with no slots at all keeps the original
  first-line-appearance order, unchanged.

**`PlannedSlotId`'s own text format is NOT consistent across real cart
plans** — confirmed live against 3 different tasks/cart-plan
configurations:
- `PICK0637`, plan `"Pick Cart-4 Slots"` → plain numbers (`"1"`,
  `"2"`, `"3"`)
- `PICK0184`, plan `"Hybrid Tote oLPN Pick Cart 3 Slots"` → pre-labeled
  strings (`"Slot 1"`, `"Slot 2"`, `"Slot 3"`) — **and** two of its 4
  oLPNs both landed on `"Slot 1"`, so the 1-oLPN-per-slot mapping
  confirmed on `PICK0637` is not universal; also 3 of its 4 lines had
  `Quantity: 0.0` and their destination oLPNs didn't exist yet as
  queryable `search_olpn()` records (`None` returned) — this task
  looked like degenerate/placeholder demo data rather than a real
  cart-picking scenario, so it wasn't used for the full completion
  test.
- `PICKPICK0008` → terse lowercase form (`"slot1"`, `"slot2"`), clean
  data (real quantities, both oLPNs pre-existing), used for the actual
  end-to-end completion test below.

Two real bugs this caused, both fixed before the final test: (1) the
UI naively prepended `"Slot "` to the raw value, producing `"Slot Slot
1"` on the `PICK0184`-style data — fixed with `pickOlpnSlotLabel()`,
which only prepends when the raw text doesn't already start with
"slot" (case-insensitive); (2) the sort comparator did `Number(slotId)`
directly, which is `NaN` for `"Slot 1"`/`"slot1"` and silently
disabled sorting — fixed by extracting the first digit run out of the
string instead (`String(slotId).match(/\d+/)`).

**Confirmed end-to-end through the real browser UI**, `PICKPICK0008`
(2 oLPNs, `slot1`/`slot2`, 4 lines total) via "Complete All": both
oLPN groups rendered correctly ordered by slot with Type/Size and
status shown on each header, all 4 lines completed cleanly in one
shot, task auto-closed (`Status: "8000"`), both oLPNs closed
(`Status: "7200"`) — independently re-verified via a fresh
`search_task()`/`search_olpn()` query, not just the UI's own success
message.

**Also noticed, not chased further**: SS-DEMO's task data appears to
reset periodically — `PICK0094` and `PICK0100`, both fully completed
earlier this same session, came back with their original
untouched quantities on a later re-query. Convenient for testing
(never permanently runs out of fresh tasks), but means task state
observed in this file may not persist indefinitely in the live
environment.

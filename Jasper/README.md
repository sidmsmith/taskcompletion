# Jasper Reports — Cycle Count Sheet

A JasperReports report for warehouse personnel to manually cycle-count
locations: it lists items per storage location with a blank line to
hand-write the counted quantity, and a QR code (all location IDs,
`;`-delimited) meant to be scanned into this app's own multi-location
search box.

This folder exists because getting one working report from "Glean
generated a `.jrxml`" to "successfully uploaded and running in the WMS"
required working around several real, non-obvious compatibility issues
between three different things that all needed to agree: Jaspersoft
Studio's bundled JasperReports version, the actual WMS's (older) embedded
JasperReports version, and the JRXML file format itself. The notes below
exist so the next report (or the next change to this one) doesn't have to
rediscover all of this from scratch.

## Current status (resolved 2026-08-10)

**Root-caused and fixed by the user's R&D team.** The `Data.*` vs `*`
question documented in earlier versions of this section was never the
real issue — it was a red herring created by trying to fix the wrong
layer. The actual bug, per R&D: **JasperReports 6.4.0's top-level
`<detail>` band iteration cannot correctly consume a JSON object
wrapping an array** (our real payload shape, `{"Data": [...]}`) — it
expects a bare array (`[{...}, {...}]`) to drive per-record iteration.
No root `queryString` suffix fixes this, because the problem isn't the
suffix — it's that the *root query* was the wrong place to do
per-location iteration at all, on this specific engine version. R&D
could not reproduce the bug on a newer JasperReports version, and it's
unclear whether it's a genuine 6.4.0 bug or an intentional behavior
difference — but since the WMS can't be upgraded, this app has to work
around it either way, not chase the older assumption further.

**The fix**: stop using the root query to drive per-location iteration.
- Root `<queryString language="jsonql">` is now **empty** — an empty
  JsonQL selectExpression just returns the root node as a single
  record, so the `<detail>` band fires exactly once per report, not
  once per location.
- A `jr:list` component, wrapped inside that single detail-band firing,
  does the *real* per-location iteration via its own `datasetRun`/
  `dataSourceExpression`: `subDataSource("Data")` — **no `.*` suffix**.
  This mirrors what `jr:table`'s `subDataSource("Items")` (also no
  `.*`) was already doing correctly for per-item iteration — `jr:list`/
  `jr:table` components apparently handle the object-wrapping-an-array
  shape correctly where the top-level detail-band query never could.
  **Both `subDataSource()` calls need the bare name — `.*` on either
  breaks it again** (confirmed: the `.*`-suffixed form was tried and
  failed).

This is a structural change, not a one-line query-string flip: the
location header rectangle/text and the item `jr:table` moved from being
direct children of `<detail><band>` to being children of the new
`jr:list`'s `<jr:listContents>`, bound through a new `Dataset1`
subDataset (fields `DisplayLocation`/`LocationBarcode`/`LocationId`/
`Items`) instead of the report's own top-level fields.

**Confirmed via a working file** the user's R&D team produced and
handed back (`cyclecountsheet_WORKING.jrxml`), which was diffed against
the committed version to derive the above and then ported in — see
`git log --oneline -p -- Jasper/cyclecountsheet.jrxml` for the exact
change. QR code is still removed in this version (unrelated to this
fix — see "QR code temporarily removed" below); re-confirm the location/
item table renders correctly with real data before revisiting that.

**A pattern worth remembering for any future investigation like this**:
several earlier rounds involved proposing a JRXML change, it failing in
production, and a later round proposing to revert to a combination
already tried and already failed — without new evidence anything about
the runtime environment had changed. The eventual fix only emerged once
an independent team (R&D) actually diffed a known-working file against
the committed one, rather than continuing to hypothesize about query
suffixes from first principles. Worth trying that approach earlier next
time a similar investigation stalls.

## Files

- **`location_inventory_report.jrxml`** — the **Studio-editable** copy.
  Open, edit, and Preview this one in Jaspersoft Studio. Uses
  `net.sf.jasperreports.json.data.JsonDataSource` (JasperReports 7.x's
  package location).
- **`cyclecountsheet.jrxml`** — the **WMS deployment** copy. Same report,
  translated to classic JRXML syntax for the WMS's older engine
  (confirmed to be **JasperReports 6.4.0**, per MAWM's own supported-Jasper
  guidance), using `language="jsonql"` and
  `net.sf.jasperreports.engine.data.JsonQLDataSource` — see "JsonQL vs
  plain JSON" below. **Never open/save this one in Jaspersoft Studio** —
  Studio will silently rewrite it back into the newer format and break it
  again. It carries an inline XML comment at the top saying the same
  thing. **QR code lives in the Title band, upper-right corner**, using
  `evaluationTime="Report"` — schema-validated locally but not yet
  confirmed live; see "QR code re-added in the Title band" below before
  changing it.
- **`location_inventory_sample.json`** — sample payload used by Studio's
  "Location Inventory JSON" data adapter for Preview. Root shape:
  `{ "Locations": [ { LocationId, DisplayLocation, LocationBarcode,
  Items: [ { ItemId, ItemDescription, OnHandQty, OnHandDisplay } ] } ] }`.
- **`archive/location_inventory_report_COMPACT_FORMAT_BACKUP.jrxml`** — a
  snapshot of the report in the newer "compact" JRXML format, kept from
  before the classic-format conversion, in case it's ever useful again.
- **`local-640-harness/`** — a real, working local copy of JasperReports
  6.4.0 (MAWM's actual version, downloaded from Maven Central — not
  Studio's bundled 7.0.6) that can compile/fill this report from the
  command line and validate query/field-mapping changes in seconds,
  instead of burning a WMS deployment round-trip per hypothesis. Run
  `local-640-harness/setup.sh` once, then see its own `README.md` for
  usage — including a critical, easy-to-miss gotcha about which
  `fillReport()` overload actually reflects what MAWM does at runtime.

## The core problem: two JasperReports versions, two file formats

Jaspersoft Studio installed on this machine bundles **JasperReports
7.0.6**. The WMS runs **JasperReports 6.4.0** (confirmed via MAWM's own
supported-Jasper documentation — see "JsonQL vs plain JSON" below for how
this was pinned down after initially just knowing it was "older"). These
two engines disagree on:

1. **JRXML syntax itself.** Studio 7.0.6 introduced a newer "compact"
   JRXML format (`<element kind="textField" ...>`, bare `<expression>`
   tags, no XML namespace declaration on the root element) and — this is
   the trap — **silently rewrites any file into this format the first
   time you save it in Studio**, even a file that started out in the
   older "classic" format (`<textField><reportElement/><textElement/>
   <textFieldExpression/></textField>`, namespaced root element). The
   WMS's older engine only understands the classic format.
2. **Where `JsonDataSource` lives, and which data-source class MAWM
   actually wants.** JasperReports relocated JSON data source support
   from `net.sf.jasperreports.engine.data.JsonDataSource` to
   `net.sf.jasperreports.json.data.JsonDataSource` as part of the 7.0
   modularization — Studio 7.0.6 only has the new location. But MAWM's
   own supported-Jasper guidance additionally specifies that JasperReports
   6.4.0 should use **`JsonQLDataSource`** (`language="jsonql"`), not the
   plain `JsonDataSource` (`language="json"`), for list/subreport data —
   see "JsonQL vs plain JSON" below. **This is why the two `.jrxml` files
   must permanently differ on both the query language and the
   data-source class** — there is no single combination that satisfies
   both engines at once.

Evidence the WMS is on an older version (in case this ever needs
re-confirming, or matters for a future report): its schema validator
rejects a `uuid` attribute on the root `<jasperReport>` element (a
Studio-only convenience attribute, safe to just omit) and enforces
`isBold`/`isForPrompting`-style naming rather than the newer
`bold`/`forPrompting` shorthand — both consistent with a schema that
predates several 7.x-era conventions. (This was later confirmed precisely
as JasperReports 6.4.0 via MAWM's own documentation, rather than left as
"some older version.")

## JsonQL vs plain JSON (`cyclecountsheet.jrxml` only)

MAWM's supported-Jasper guidance for 6.4.0 says list/subreport data
should use **JsonQL**, not the plain JSON data adapter this report
originally used. Two-line change:

- `<queryString language="json">` → `<queryString language="jsonql">`
- `((net.sf.jasperreports.engine.data.JsonDataSource)$P{REPORT_DATA_SOURCE}).subDataSource("Items")`
  → `((net.sf.jasperreports.engine.data.JsonQLDataSource)$P{REPORT_DATA_SOURCE}).subDataSource("Items")`

**Update — the two-line change alone was not enough.** The report
uploaded and printed successfully (proving the JRXML structure and the
`JsonQLDataSource` class reference were both fine), but every field came
back **blank** — not garbled or duplicated, just empty. The `.*` question
above was never actually the cause; the real cause was that
**`JsonQLDataSource` does not auto-map field names to same-named JSON
keys the way the plain `JsonDataSource` does.** Every `<field>` needs an
explicit property naming the JSON key it should read, even when the
field name and the key are identical:

```xml
<field name="LocationId" class="java.lang.String">
    <property name="net.sf.jasperreports.jsonql.field.expression" value="LocationId"/>
</field>
```

**Get this exact property name right — `jsonql`, not `json`.** Glean's
first pass at this fix used `net.sf.jasperreports.json.field.expression`
(no "ql"). That property name is real, but it belongs to the *plain*
`JsonDataSource`, not `JsonQLDataSource` — confirmed directly from
JasperReports' own source (`JsonQLDataSource.java`:
`PROPERTY_FIELD_EXPRESSION = JRPropertiesUtil.PROPERTY_PREFIX +
"jsonql.field.expression"`, i.e. `net.sf.jasperreports.jsonql.field.expression`).
Since this file uses `JsonQLDataSource` throughout, every field mapping
must use the `jsonql` variant. The `<property>` element goes as the
*first* child inside `<field>`, before any `<propertyExpression>`/
`<fieldDescription>` (confirmed against JasperReports' own bundled
classic `jasperreport.xsd`).

**Update — the `.*` suffix theory below turned out to be a dead end;
kept for the historical record, corrected in "Current status" above.**
At the time, `Data.*`/`Items.*` seemed to explain a report that deployed
but kept coming back with no data. It wasn't actually the fix — see
"Current status" at the top of this document for the real root cause
(root-level `<detail>`-band iteration can't consume an object-wrapped
array on 6.4.0 at all, `.*` or not) and the real fix (empty root query,
`jr:list`/`jr:table` driving iteration via bare, non-`.*`
`subDataSource()` calls). The reasoning below was self-consistent and
carefully checked against JasperReports' own source at the time, but
the underlying premise — that the root query and `subDataSource()`
follow the same rules and just needed the same suffix — was wrong; they
turned out to behave differently on this engine version specifically
for the object-wrapping-an-array shape.

After the field-mapping fix, the QR removal, and the `Locations`→`Data`/
`OnHandQty`→`OnHandSum` payload-shape fixes (see later sections), the
report finally deployed and rendered a PDF — but kept excluding data.
The hypothesis at the time was that **both**
`<queryString language="jsonql">Data</queryString>` **and**
`subDataSource("Items")` needed a `.*` suffix — `Data.*` and `Items.*`.

Glean's own diagnosis at this point only proposed fixing the root query
(`Data` → `Data.*`) and explicitly said to leave `subDataSource("Items")`
unchanged. That second half was checked against `JsonQLDataSource`'s
actual source before applying anything (fetched directly from
`TIBCOSoftware/jasperreports` on GitHub) — `subDataSource(String)`
constructs a new `JsonQLDataSource` using the exact same
`jsonQLExecuter.selectNodes(root, selectExpression)` call, with
`next()`/`recordCount()` just iterating whatever list that returns, as
the root query — which seemed to mean no special-casing existed between
the two. Both were changed together at the time: `Data.*` and
`Items.*`. **This is exactly the combination now known to be wrong**
(root-level iteration doesn't work regardless of suffix; `subDataSource()`
wants no suffix at all) — the source-code check was real, but it
answered "are these two calls mechanically similar," not "does either
of them actually work for this payload shape on this engine," which
only a real upload (or R&D's own environment) could answer.

Since Studio 7.0.6 doesn't have `JsonQLDataSource` at all (JsonQL support
was restructured again in the 7.x line), none of this can be verified
in Studio's own Preview. **It can be verified locally another way,
though** — see "How to verify a change before uploading to the WMS"
further down: `local-640-harness/` uses the real open-source
JasperReports 6.4.0 engine directly (not Studio), which does have
`JsonQLDataSource`, and can render an actual PDF from
`cyclecountsheet.jrxml` in seconds. This section's own findings
(`Data.*`/`Items.*` vs the eventual no-suffix `subDataSource()` fix)
predate that tooling existing — a future case like this should use it
first, rather than reasoning from source code and burning a WMS upload
to find out.

## Classic JRXML syntax notes (for the WMS-bound file)

These are the specific translation rules discovered by trial, confirmed
either against JasperReports' own bundled classic-format XSDs or by the
WMS's own validator error messages:

- Root `<jasperReport>` needs the namespace declarations back:
  `xmlns="http://jasperreports.sourceforge.net/jasperreports"`,
  `xmlns:xsi=...`, and `xmlns:jr="http://jasperreports.sourceforge.net/jasperreports/components"`
  (this last one is used by both `jr:table` and the QR code component —
  see below). **No `uuid` attribute on the root element.**
- `bold="true"` → `isBold="true"` (classic format keeps the `is` prefix
  on boolean attributes generally).
- `forPrompting="false"` → `isForPrompting="false"` on `<parameter>`.
- `<query language="json">` → `<queryString language="json">`.
- `<dataset name="...">` → `<subDataset name="...">`.
- Every visual element needs the full classic wrapping:
  `<textField><reportElement x=.. y=.. width=.. height=.. uuid=../>
  <textElement .../><textFieldExpression><![CDATA[...]]></textFieldExpression>
  </textField>` — not the compact `<element kind="textField" ...><expression>`
  shorthand. Same pattern for `staticText`, `rectangle`, etc.
- `hTextAlign`/`vTextAlign` → `textAlignment`/`verticalAlignment` (on
  `<textElement>`, not as flat attributes on the element itself).
- `textAdjust="StretchHeight"` → `isStretchWithOverflow="true"`.
- **`<variable>` child element order matters** in classic format (it's a
  strict XSD sequence, unlike the compact format's more lenient
  Jackson-based binding): `<variableExpression>` must come **before**
  `<initialValueExpression>`, not after. Getting this backwards produces
  `cvc-complex-type.2.4.d: Invalid content was found starting with
  element 'variableExpression'`.
- The QR code component is `<jr:QRCode>` with a `<jr:codeExpression>`
  child, in the **same** `.../jasperreports/components` namespace as
  `jr:table` — confirmed directly from JasperReports' own bundled
  `barcode4j.xsd` (`<element name="QRCode" substitutionGroup="jr:component">`).
  Both live inside a `<componentElement><reportElement .../>...</componentElement>`
  wrapper, same as `jr:table`.
- A rectangle's default border becomes visible against light backgrounds
  (it was already there against the dark navy banner, just not
  noticeable) — suppress it with a nested `<graphicElement><pen
  lineWidth="0.0"/></graphicElement>` inside the `<rectangle>`.

## Design notes worth knowing before changing anything

- **The blind-quantity line is two separate elements, not one** — a
  blank line (always identical across every row, regardless of data)
  plus a separate small `textField` positioned to start right after the
  line ends, left-aligned, holding `(actualQty)` in small grey text.
  **Do not combine these into one text run** — if the line and the
  quantity are one right-aligned block, a wider number (e.g. `1,737`)
  pushes the whole block, including the line itself, further left than a
  narrower number (`750`) would, so the blank lines stop lining up
  between rows. Splitting them was the actual fix for that.
  **Confirmed live, 2026-08-10, real bug found in the first live PDF**:
  the blank line was originally a `staticText` containing 32 literal
  underscore characters, in a box only 95pt wide — too narrow for that
  string at the `Detail` style's 9pt font, so it silently *wrapped onto
  two lines* inside a 22pt-tall cell (JasperReports wraps text to fit
  width regardless of `isStretchWithOverflow`; only the *height* used to
  accommodate overflow is what that flag controls). The real symptom in
  the PDF was a broken, two-segment line overlapping the `(qty)` label —
  not obvious from the JRXML alone, only visible once actually rendered.
  **Fixed by using a real `<line>` graphic element instead of underscore
  text** (`<line><reportElement x="0" y="16" width="90" height="1"/></line>`,
  no `graphicElement` override needed — the default 1pt black pen is
  correct) — a vector line has no font metrics and can never wrap,
  which is the more robust fix over just widening the old text box or
  shrinking the font to force it onto one line. General lesson: prefer
  an actual `<line>`/`<rectangle>` graphic over "fake" glyph-based
  lines (underscores, dashes) for anything that needs to render as a
  single unbroken segment — glyph-based fakes are one font-size or
  locale change away from silently wrapping again.
- **`OnHandQty`'s field class must be a concrete number type**
  (`java.lang.Double`), never the abstract `java.lang.Number` — JSON data
  source's `convertNumber()` only knows how to instantiate specific
  wrapper types (`Byte`, `Short`, `Integer`, `Long`, `Float`, `Double`,
  `BigInteger`, `BigDecimal`) and throws `Unknown number class` on the
  abstract type.
- **Per-location whitespace consistency**: the item table's declared
  height, and the surrounding detail band's declared height, should be
  sized for the **minimum** case (one item row), not a padded/generous
  guess. JasperReports auto-stretches both the table and the band to fit
  extra rows on locations with more items, but it never shrinks them
  below the declared size — so if the declared height assumes more room
  than a typical single-item location needs, that gap becomes visible,
  inconsistent dead space that only shows up on the common case.
- **QR code covers all locations returned by this report's query only.**
  If "all locations in the warehouse" is the intent, confirm the
  data source query actually has that scope — the QR just concatenates
  whatever `Locations[]` the JSON payload contains.
- **Superseded 2026-08-10 — see "QR code re-added in the Title band"
  below.** This subsection originally documented a hard "Summary band
  only, never Title" rule, reasoned from how a plain `<variable>`
  behaves across bands. That reasoning is still correct **for plain
  variables**, but it turned out not to be the only option: barcode/QR
  components support their own `evaluationTime="Report"` attribute
  (schema-confirmed, see below), which defers *evaluation* of the QR's
  content independent of *where* the element physically sits — making a
  Title-band, upper-right-corner QR possible after all. Kept here for
  the historical reasoning; don't re-derive the old Summary-band-only
  design without reading the newer section first.

## QR code re-added in the Title band, upper-right corner (2026-08-10)

The QR code was previously removed after a real WMS upload came back
with a bare, message-less `java.lang.NullPointerException` (see the
superseded section above for the full original investigation — the
"missing runtime library" theory there is still the leading
explanation and is **still unconfirmed**; re-adding the QR does not
resolve that risk, it just means the next WMS upload is the real test
of it again).

**Two real things changed since that removal, both required by the
root-query restructuring earlier in this document** (the `<detail>`
band now fires exactly once, with per-location data living inside the
`jr:list`'s own isolated `Dataset1` subDataset, not the parent report):

1. **The accumulator variable can no longer live at the parent-report
   level and read `$F{DisplayLocation}` directly** — those fields
   belong to `Dataset1`'s own separate fill context now, not the parent
   report's. It's defined *inside* `Dataset1` instead, accumulating
   exactly as before (`resetType="Report"`, `calculation="Nothing"`,
   the same string-concatenation `variableExpression`), and its final
   value is propagated out to a parent-report variable of the same name
   via a `<returnValue fromVariable="AllLocationsCsv"
   toVariable="AllLocationsCsv" calculation="Nothing"/>` on the
   `jr:list`'s `<datasetRun>` — the standard JasperReports mechanism for
   getting a value out of a list/table/crosstab's own subdataset into
   the parent report (schema-confirmed: `<datasetRun>`'s content model
   in `jasperreport.xsd` explicitly allows `<returnValue>` after
   `<dataSourceExpression>`). The parent-level variable is declared
   with `calculation="System"` and no expression of its own — per the
   schema's own documentation, a `returnValue` target "should be a
   variable with `calculation="System"`".

2. **The QR itself moved from the `<summary>` band into the `<title>`
   band, upper-right corner** (`x="446" y="0" width="94" height="94"`,
   the same size and same right-edge position it always had — only the
   band changed), with `<jr:QRCode evaluationTime="Report">` added.
   This is new: `barcode4j.xsd`'s `Barcode` complexType (which `QRCode`
   extends) declares its own `evaluationTime`/`evaluationGroup`
   attributes, separate from — and not blocked by — the fact that the
   generic `<componentElement>` wrapper in the core schema has no such
   attribute itself (confirmed by reading both schemas directly: core
   `jasperreport.xsd`'s `componentElement` complexType is just
   `reportElement` + `component`, no attributes at all; but
   `barcode4j.xsd`'s `Barcode` complexType — the base type for every
   barcode symbology including `QRCode` — separately declares
   `evaluationTime type="jr:basicEvaluationTime"`). `evaluationTime="Report"`
   means: the QR element prints in its normal position (Title band, top
   of page 1) but its `codeExpression` is evaluated only once at the
   very end of the fill, using whatever value `$V{AllLocationsCsv}`
   holds by then — the same delayed-evaluation mechanism JasperReports
   uses for "Page X of Y" totals, just applied to a component instead
   of a text field. By the time the fill ends, the single `<detail>`
   band (which contains the `jr:list` that accumulates and returns
   `AllLocationsCsv`) has already run, so the deferred QR sees the
   fully-accumulated value even though it's positioned at the top of
   the page.

**Locally schema-validated** (not just well-formed-XML-checked) against
the real bundled JasperReports 6.4.0 classic schemas — `jasperreport.xsd`
and `components.xsd` from the actual `jasperreports-6.4.0.jar` in
`local-640-harness/cp/`, plus a `barcode4j.xsd` extracted in an earlier
session from Jaspersoft Studio's own installation — using `lxml` instead
of Java's `SchemaFactory` (no JDK was available in this session), but hitting
the exact same same-namespace-import gotcha already documented in "How
to verify a change" below: importing `components.xsd` before
`barcode4j.xsd` validates the whole file clean; importing them in the
opposite order produces a spurious `jr:list` error (only whichever
same-namespace schema loads first fully registers its substitution
group members — a tooling artifact, not a real error, per the existing
documented rule "only trust an error that shows up in both runs"). The
`components.xsd`-first ordering — the trustworthy one — validated the
whole file, including the new `<returnValue>`, the parent `calculation="System"`
variable, and `<jr:QRCode evaluationTime="Report">`, with **zero**
errors.

**CONFIRMED LIVE, 2026-08-10.** The user uploaded this version and got
back a real PDF with a correctly-rendered QR code in the upper-right
corner of page 1 — this resolves every open question in the paragraph
that used to be here: MAWM's embedded JasperReports 6.4.0 **does** have
the barcode/ZXing/Batik runtime libraries needed to render a QR
(closing out the "Runtime library dependencies" theory below as the
explanation for the *original* NPE, at least as far as barcode support
goes), the `evaluationTime="Report"` deferred-evaluation mechanism
works end to end on the real engine for a component element (not just
per the schema), and the `Dataset1`-scoped accumulator +
`returnValue` propagation correctly delivers the full, real
location list to the parent-report variable. See "QR/layout polish
after first live confirmation" below for what still needed fixing in
that same first PDF (title layout, the blank-line rendering, location
ordering).

For reference, the QR block currently in the `<title>` band looks like
this:

```xml
<componentElement>
    <reportElement x="446" y="0" width="94" height="94" uuid="e5b7c3a2-1f6d-4e89-8a2c-3d9f7b0c4a63"/>
    <jr:QRCode evaluationTime="Report">
        <jr:codeExpression><![CDATA[$V{AllLocationsCsv}]]></jr:codeExpression>
    </jr:QRCode>
</componentElement>
```

(`location_inventory_report.jrxml`, the Studio-editable copy, was never
updated to match this — it still has the QR code in its own
compact-format equivalent, in the position/band this section originally
described before being superseded. Worth revisiting if the Studio copy
needs to stay a faithful preview of the deployment file, but not done
automatically since Studio's compact format doesn't have the same
`evaluationTime` story to port over without its own verification.)

## QR/layout polish after first live confirmation (2026-08-10)

Four fixes made from the user's own reading of the first successful live
PDF (`cyclecountoutput.pdf`), none affecting the data/query mechanisms
above — purely layout and ordering:

- **Title now spans the full page width** (`x="0" width="540"`,
  matching the column width) and is centered/vertically-middled across
  the entire `94`pt-tall title band, **independent of the QR** — it no
  longer shares a narrowed box with the QR the way it did when the
  subtitle still existed. At the enlarged size (see below), the
  rendered text is comfortably narrower than the ~440pt available before
  the QR's `x=446` starting point, so the two don't actually visually
  collide even though their `reportElement` bounding boxes overlap —
  only real rendered glyphs matter for visual overlap, not declared box
  width. If `REPORT_TITLE` is ever set to something much longer than
  "Cycle Count Sheet," this could start visually overlapping the QR;
  not solved preemptively.
- **Title font size increased 14 → 21** (the requested 50% increase).
- **The "Generated from the Location Inventory JSON payload" subtitle
  was removed outright** (`staticText`), per explicit instruction —
  not hidden/commented, actually deleted from the title band.
- **Locations sort by `DisplayLocation` — attempted via `<sortField>`,
  reverted after breaking live rendering. See "sortField regression"
  below**, added right after this list. The location-sequence question
  itself was resolved with the user first (ascending alphanumeric sort
  on `DisplayLocation`, not a dedicated physical walk-sequence field —
  that field isn't in this report's payload today) — only the
  *mechanism* used to implement it turned out to be wrong.
- **Fixed the broken blank-line rendering** — see the "blind-quantity
  line" bullet in "Design notes" above for the full root cause (32
  literal underscore characters silently wrapping onto two lines inside
  a too-narrow, fixed-height cell) and the fix (a real `<line>` graphic
  element instead of underscore text). This was the most important of
  the four fixes per the user's own framing — the line is what a
  warehouse worker actually hand-writes the physical count on, so a
  broken/overlapping render there defeats the report's whole purpose.

**Title/subtitle/line fixes confirmed schema-valid, sort field
reverted** — see below for what actually happened on the real WMS
upload.

## `<sortField>` regression: broke all row data, reverted (2026-08-10)

The user uploaded the four-fix version above. Result: the title/QR
rendered, and the per-location banner header (the dark "Location: X"
rectangle+text) rendered — but the nested item table's rows (Item /
Description / On Hand, the actual data a warehouse worker needs) came
back **completely empty** for every location. This is a real
regression, not a rendering nitpick — worse than the original blank-line
bug, since the report is now non-functional.

**Root cause (reasoned from JasperReports architecture, not directly
confirmed against the failing PDF's own content stream — see caveat
below): `<sortField>` is very likely incompatible with this report's
specific "reach back into the live `$P{REPORT_DATA_SOURCE}` cursor"
idiom.** The nested item `jr:table`'s own `dataSourceExpression` —
`((net.sf.jasperreports.engine.data.JsonQLDataSource)$P{REPORT_DATA_SOURCE}).subDataSource("Items")`
— only works because, throughout `Dataset1`'s unsorted iteration,
`$P{REPORT_DATA_SOURCE}` stays the *same* live `JsonQLDataSource`
object, just advanced record-by-record via `next()`; each location's
`jr:listContents` firing calls `subDataSource("Items")` on that same
object at whatever position its cursor currently sits, pulling that
location's own `Items` array directly, without ever going through
`Dataset1`'s own declared `Items` field. Declaring a `<sortField>`
forces JasperReports to fully buffer the dataset (read every record
into memory, sort it, then serve the sorted, buffered copy) — that
buffering pass is necessarily a *different* runtime object from the
original live `JsonQLDataSource`, so by the time `jr:listContents`
fires per (now-buffered, sorted) record, `$P{REPORT_DATA_SOURCE}`
almost certainly isn't the live, cursor-correct object the item
table's cast/`subDataSource()` call depends on anymore — silently
returning no rows rather than throwing a visible error. This is
architecturally consistent with what the user reported: the *outer*
per-location iteration and banner header kept working (buffering still
correctly replays N sorted location records), while only the *inner*
`subDataSource()`-driven item table — the one piece of this report
built around reaching outside the declared field set into a live
cursor — broke.

**Reverted**: the `<sortField name="DisplayLocation" order="Ascending"/>`
line was removed from `Dataset1`. Locations are back to whatever order
MAWM's own `Data[]` array returns them in (unsorted, matching the
version that was confirmed fully working before this round of layout
fixes) — re-validated locally as schema-valid, zero errors, same as
every other change in this file.

**Caveat on this diagnosis**: the user pasted the failing PDF as a
base64 blob directly in chat; it appears to have been corrupted in
transit (very long single-line pastes are a known fragile case) —
decoding it locally successfully reconstructed the QR-code drawing
object but the file's own trailer/`%%EOF` never appeared, so the
actual page content stream (which would show definitively whether the
item table's `BT`/`Tj` text-drawing operators are present or missing)
couldn't be inspected directly. This diagnosis is reasoned from
JasperReports' own architecture and is the most mechanically plausible
explanation, but isn't independently confirmed against the real broken
bytes the way this project's methodology otherwise insists on. If a
future case needs this kind of forensic PDF inspection, share the file
itself (a path, like `cyclecountoutput.pdf` was) rather than pasting
base64 inline — large inline pastes risk exactly this kind of silent
corruption.

**Location-sequence sorting is still an open problem, not solved.**
Whatever the real mechanism, it needs to avoid forcing JasperReports to
buffer `Dataset1` out from under the live-cursor `subDataSource()`
trick the item table depends on. Two directions worth investigating
before the next attempt, neither tried yet:
1. **Sort inside the JsonQL query itself** (`subDataSource("Data")`'s
   own selectExpression) so the *already-sorted* result is still a
   genuine live `JsonQLDataSource`, not a post-hoc buffered copy. This
   needs `local-640-harness`'s `JsonQLProbe.java` (built for exactly
   this kind of "isolate a pure JsonQL query-syntax question" case) to
   confirm JsonQL's grammar actually supports an order-by/sort
   construct before trying it live again — not yet attempted, and no
   JDK was available in the session that found this regression.
2. **Give up on `subDataSource()`'s live-cursor trick for the item
   table** and rewire it to read from `Dataset1`'s own declared `Items`
   field instead (which sorting is compatible with, since it's a plain
   declared field like any other) — a bigger structural change, since
   `Items` is currently declared `class="java.lang.String"` as a
   pass-through handle, not actually parsed/used as a nested array
   anywhere; would need real investigation into whether `JsonQLDataSource`
   supports a field whose value is itself sub-queried via `<jr:table>`
   the normal way (the way this file's *other* `subDataSource("Items")`
   caller — inside `jr:list`, for `Data` itself — already does at the
   top level, just not yet proven for a field-scoped one).

Neither direction has been attempted or tested — flagged here rather
than guessed at live again, consistent with this document's own
repeated lesson about not re-trying an unconfirmed idea against
production without local verification first.

## The real MAWM payload shape (confirmed, differs from the sample data)

The `Locations`-rooted, simple `{ItemId, ItemDescription, OnHandQty,
OnHandDisplay}` shape that `location_inventory_sample.json` uses (and
that this whole report was originally designed against) turns out to be
a simplified stand-in Glean invented early on — **not** what MAWM
actually sends. A real captured payload from MAWM looks like this
instead (irrelevant fields omitted; the real objects have dozens more,
e.g. full `Inventories[]` detail per item):

```json
{
  "Data": [
    {
      "LocationId": "A1AC0401",
      "DisplayLocation": "A1AC0401",
      "LocationBarcode": "A1AC0401",
      "Items": [
        {
          "ItemId": "5000221",
          "ItemDescription": "Floral Print Dress",
          "OnHandSum": 2,
          "...": "many other fields, unused by this report"
        }
      ]
    }
  ]
}
```

Key real differences from the original assumed shape, both fixed in
`cyclecountsheet.jrxml`:
- Root array is **`Data`**, not `Locations`.
- Each item's on-hand quantity is **`OnHandSum`**, not `OnHandQty`.
- There is no `OnHandDisplay` key at all on the real item object — the
  closest equivalent is `OnHandSumDisplay` (e.g. `"2 UNIT"`). The
  `OnHandDisplay` field is still declared in `ItemDataset` mapped to a
  now-confirmed-nonexistent key, but since nothing in the report
  actually displays that field, it's harmless — it just always
  evaluates to null/empty. Worth fixing (or removing the unused field
  entirely) if it's ever wired into the visible report.

**`location_inventory_sample.json` and `location_inventory_report.jrxml`
(the Studio-editable copy) have NOT been updated to match this real
shape** — they still use the original synthetic `Locations`/`OnHandQty`
structure, so Studio's Preview no longer reflects what MAWM actually
sends. This wasn't done automatically since it's a bigger, deliberate
decision (new sample JSON, re-verifying every field binding in the
Studio copy) rather than a small fix — worth doing before the next
round of design changes in Studio, so Preview stays meaningful, but
flagged here rather than done unprompted.

## Runtime library dependencies (separate from the JRXML file itself)

None of this is fixable by editing the report — it's about what needs to
be present on whatever JasperReports engine actually executes the
report at runtime:

- QR code rendering needs the JasperReports barcode module
  (`jasperreports-barcode4j`), the actual Krysalis Barcode4J library,
  ZXing (QR encoding), Apache Batik (rasterizes the QR's SVG for PDF
  embedding — needs the *full* Batik jar set, ~15 jars), and a PDF export
  backend (OpenPDF or similar). If the WMS ever accepts the file but a
  QR code fails to render at generation time, this is the likely cause —
  ask whoever manages the WMS's JasperReports deployment whether
  barcode/QR support is included.
- Simply *loading* a compact-format `.jrxml` (if that format is ever
  used again) needs Jackson (`jackson-core`/`databind`/`annotations`/
  `dataformat-xml`) and the Eclipse JDT batch compiler, for what it's
  worth — not relevant to the WMS's classic-format file, but relevant if
  a future report is designed and tested outside Studio.

## How to verify a change before uploading to the WMS

**Superseded 2026-08-10 — a full local PDF render of the classic-format
file *is* possible after all.** Everything below about the classic
loader's license gate is still true, but it turned out to only apply to
**Jaspersoft Studio's own bundled jar** (`net.sf.jasperreports_7.0.6.final.jar`)
— the real, open-source `net.sf.jasperreports:jasperreports:6.4.0`
artifact from Maven Central (what `local-640-harness/` actually uses)
has no such gate at all. This was already noted in
`local-640-harness/README.md` for compile+fill (`ProdValidate.java`),
but hadn't been extended to full PDF export (with the QR rendering)
until this session, once the missing barcode4j/Batik/iText jars were
tracked down — see `local-640-harness/README.md`'s "Rendering a real
PDF for local QC" section for the full how-to. **This is now the
preferred first step**, ahead of both schema validation and a live WMS
upload:

1. **Render an actual PDF locally**: `local-640-harness/render.ps1`
   against a JSON payload in `my_test_payload.json` (edit it freely) —
   compiles, fills, and exports a real PDF from `cyclecountsheet.jrxml`
   in seconds, using the same two-argument `fillReport()` path MAWM's
   own runtime uses. This is what would have caught the `<sortField>`
   regression immediately, before it ever reached a WMS upload — see
   "sortField regression" above.
2. Validate it's well-formed XML and schema-valid against JasperReports'
   own bundled classic XSDs (`jasperreport.xsd`, `components.xsd`,
   `barcode4j.xsd`) — cheaper than a full render for catching pure
   structural/naming mistakes, still worth doing first if a local JDK
   genuinely isn't available. (Note: validating two same-namespace
   XSDs — `components.xsd` and `barcode4j.xsd` — as separate `Source`
   objects in one Java `SchemaFactory` call only fully registers
   whichever one loads *first*; the other's top-level elements get
   incorrectly flagged as invalid. Test with both orderings and only
   trust an error that shows up in *both* runs. **Confirmed this same
   technique, and the same ordering quirk, reproduces identically with
   Python's `lxml`** (`etree.XMLSchema`) when no JDK is available in the
   session — `jasperreport.xsd` + `components.xsd` imported before
   `barcode4j.xsd` is the trustworthy ordering; the reverse spuriously
   flags `jr:list` as unexpected.)
3. Actually upload it to the WMS. Still the only *true* ground truth —
   the local render uses the real open-source 6.4.0 engine and has
   matched production closely so far, but it's not a guarantee the
   WMS's actual embedded deployment doesn't differ in some way this
   harness's `cp/` jars don't capture. Treat a local-render pass as
   strong evidence, not a substitute for this step, especially for a
   change nothing has uploaded and confirmed yet.

## Making a future change

1. Edit `location_inventory_report.jrxml` in Jaspersoft Studio, or ask
   Claude to edit it directly (Claude's direct edits verify against the
   real engine via the command-line harness described above before
   handing it back — safer than editing blind).
2. Confirm it looks right in Studio's Preview.
3. Port the same logical change into `cyclecountsheet.jrxml` by hand (or
   ask Claude to), remembering: classic syntax, and the query
   language/data-source line stays `language="jsonql"` +
   `net.sf.jasperreports.engine.data.JsonQLDataSource` — don't copy
   Studio's `language="json"` + `json.data.JsonDataSource` line over.
   **Any new `<field>` needs its own
   `<property name="net.sf.jasperreports.jsonql.field.expression"
   value="TheJsonKeyName"/>`** — `JsonQLDataSource` won't auto-map a
   field to a same-named JSON key the way Studio's plain `JsonDataSource`
   does, so a field added on the Studio side without this property will
   silently come back blank on the WMS side, exactly like the original
   bug this section documents. If you add a *new* subreport/list table
   (not just editing this one), its `dataSourceExpression`/
   `subDataSource()` call needs the same JsonQL treatment — see "JsonQL
   vs plain JSON" above. **Do not drive top-level per-record iteration
   from the root `queryString`** — on this engine, that can't correctly
   consume our object-wrapped-array payload shape (`{"Data": [...]}`)
   regardless of `.*`; leave the root query empty and add a `jr:list`/
   `jr:table` component with its own `subDataSource("KeyName")` call
   (bare key name, **no `.*`**) to iterate a new array — see "Current
   status" at the top of this document for the full explanation.
4. Re-upload `cyclecountsheet.jrxml` to the WMS and treat whatever error
   (if any) comes back as authoritative — that engine's exact version
   and available modules aren't fully known, so each new upload attempt
   can still surface something new, the same way this report's journey
   went through uuid → attribute naming → JsonDataSource package in
   three separate rounds.

## Starting a brand new report

The same two-engine problem will apply to any new report. Fastest path:
design and iterate in Studio (compact format, `json.data` package) until
it looks right, then have Claude do the classic-format conversion pass
in one go at the end, using this document's syntax notes — rather than
discovering each classic-format quirk one WMS upload at a time again.

# Task Sheet (Pick Sheet) — `tasksheet.jrxml`

A second report, added 2026-08-11: prints task detail lines (Location,
Item, Description, Required Qty) for a warehouse picker to hand-write
what they actually picked, with a blank Reason line for short picks —
same purpose as Cycle Count Sheet, different domain (Pick tasks, not
inventory counts). Single self-contained classic-format file, same
conventions as `cyclecountsheet.jrxml` (see everything above this
section — it all applies here too). **Built and locally verified in one
session using `local-640-harness/render.ps1`, never yet uploaded to the
WMS** — this section will need a "confirmed live" pass the same way
Cycle Count Sheet's did.

## Real payload shape (confirmed from two live captures)

Same `Data`-rooted envelope pattern as Cycle Count Sheet:
`{"Data": [ {TaskId, TransactionId, ..., TaskDetail: [ {SourceLocationId,
ItemId, Quantity, UomTypeId, PlannedContainerTypeId, OlpnId,
PlannedSlotId, PickExecutionSequence, Sequence, ...}, ... ]}, ... ]}`.
Also duplicates the whole thing under `TransformedPayload.Data` —
deliberately never referenced, which is the entire de-dup fix (nothing
to de-dup if it's never read).

**Two real fields confirmed absent from this payload, both hardcoded as
placeholders for now, per explicit instruction**: `ItemDescription`
(shown as `"Item " + ItemId + " Description"`) and oLPN Type/Size
(shown as the literal text `"TYPE/SIZE"`). Both need real WMS-side work
to add to the payload before they can be wired up for real — flagged in
an inline JRXML comment at the top of the file too.

**Sort key for pick-line order — confirmed from two real task types,
not assumed**: `PickExecutionSequence` looked like the obvious walk-path
field on a plain `PICK_INTO_OLPN` task (populated, and its order
genuinely differs from the raw `TaskDetail` array order — confirmed
live, one real task had its two lines in *reversed* sequence order
relative to array position). But it's **`null` on every line of a real
`PICK_INTO_CART` task** — that allocation path (`ResourceGroupId:
"Putwall Resource Group"` in the sample) doesn't populate it at all.
Falls back to the plain `Sequence` field (array-position ordinal) when
null. Implemented as a subDataset `<variable name="EffectiveSequence">`
+ `<sortField type="Variable" name="EffectiveSequence">` — confirmed
correct locally against both real task types (the reversed-order case
sorted correctly; the cart task correctly fell back to `Sequence`
order).

## `<break>` inside `jr:list` — confirmed broken, don't retry

The obvious way to force a page break before each task was `<break
type="Page">` inside `jr:listContents` — schema-legal (`jr:list`
explicitly allows `jr:break` as a child), but **throws a real
`NullPointerException` at fill time**:

```
NullPointerException: Cannot invoke "JRFillBand.isPageBreakInhibited()"
because "this.band" is null
	at JRFillBreak.prepare()
	at FillListContents.prepare()
```

Confirmed via the local harness, not a guess — `<break>` apparently
never gets a valid band reference when it's inside a list component on
this JasperReports version, regardless of configuration. Don't re-add
`<break>` inside `jr:listContents`/`jr:table` without new evidence this
was fixed in some way.

**Why the real fix (a `<group isStartNewPage="true">`) isn't a simple
substitute**: `jr:list`/`jr:table` use their subDataset purely as a flat
record source — any `<group>` declared on that subDataset is never
actually rendered (no groupHeader/groupFooter bands fire), because
these components don't run the subDataset "as a report." Native,
guaranteed group-based page breaking only happens in a genuine report —
the main report, or a `<subreport>` (a full nested report execution
with its own real band structure). The main/root report can't do the
per-task iteration itself either (same root-query limitation
`cyclecountsheet.jrxml` already worked around) — only something with
its own `dataSourceExpression` can consume `Data` at all, and of those,
only `<subreport>` gets real pagination.

**Deliberately not pursued — per explicit instruction.** A `<subreport>`
would mean a second, linked compiled-report file, and this app has
**never confirmed MAWM's document-template deployment mechanism
supports a report referencing an external subreport** — every report
built here so far has been single-file. Introducing that risk for a
"nice to have" forced break, in a demo-only report, wasn't worth it —
"this is all demo stuff... just do what you think is right and we can
revisit actual break logic later." **Current behavior: no forced page
break between tasks** — multiple tasks can land on the same physical
page. Each task still gets a bold, unmistakable dark banner and a 20pt
gap before it, so it's visually clear where one task ends and the next
begins, just not a hard page boundary. Revisit with a `<subreport>`
(and confirm MAWM supports it first) if a real forced break is ever
actually needed.

## Container legend — v1 has no de-duplication, deliberately deferred

Each task gets a "Containers for this task:" legend (one line per
distinct Slot/Tote/oLPN) above the pick-line table, driven by its own
independent `jr:list` (`LegendLineDataset`, its own
`subDataSource("TaskDetail")` call — a second, separate read of the
same array the pick-line table also reads, sorted independently by
`PlannedSlotId`).

**A real de-dup design was worked out (sort by a composite
Slot/Type/OlpnId key, suppress a row via `printWhenExpression` when it
matches the immediately preceding row's key) but deliberately not
built** — it depends on whether a suppressed row inside `jr:list`
actually collapses its height to zero or leaves a visible blank gap,
which is unconfirmed, and stacking another unverified mechanism onto a
report that had *just* failed once already (`<break>`) wasn't worth it
in the same pass. **Current behavior**: two pick lines sharing one
slot/tote print two identical legend lines, not one — confirmed live,
acceptable for now per explicit instruction to keep this simple and
revisit later. Legend shows the **full** oLPN id (unlike the pick-line
table's own Destination column, which truncates a long oLPN as
`"00...1234"` — oLPNs run ~20 characters, too long for that narrow
column, per explicit instruction only the legend needed the full id).

## Confirmed working locally (2026-08-11), via `render.ps1`

Real 2-task sample (`local-640-harness/tasksheet_test_payload.json` —
one plain `PICK_INTO_OLPN` task, one `PICK_INTO_CART` hybrid task with
2 totes sharing a slot + 1 oLPN-destined line) rendered correctly in one
pass:
- Both tasks' pick lines sorted correctly (including the
  `PickExecutionSequence`-reversed case and the `Sequence`-fallback
  case).
- Destination column showed `Slot N` / `TOTE` / truncated oLPN
  correctly per line.
- QR, banner, Item/Description-placeholder, Required Qty + UOM, blank
  Picked Qty + Reason lines all rendered correctly.
- One cosmetic issue found and fixed the same session: the per-task
  `jr:listContents` height was declared generously enough (280) that
  the *shorter* task didn't shrink to fit, pushing a nearly-empty
  second page — same lesson already documented for Cycle Count Sheet's
  own item table ("size for the minimum case, JasperReports stretches
  up but never shrinks down"). Reduced to 185; re-confirmed single-page
  output with both tasks landing correctly on page 1.

**Not yet confirmed**: an actual WMS upload of this file. Everything
above is proven against the real open-source JasperReports 6.4.0 engine
locally, which has matched production closely for Cycle Count Sheet —
but this is a first pass for a structurally different report (multiple
independent nested `subDataSource()` calls per task, a coalesce-based
sort variable), so treat it as unconfirmed until it's actually printed
from MAWM.

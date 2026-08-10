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
  thing. **Currently has no QR code** — temporarily removed to isolate a
  NullPointerException; see "QR code temporarily removed" below before
  re-adding it.
- **`location_inventory_sample.json`** — sample payload used by Studio's
  "Location Inventory JSON" data adapter for Preview. Root shape:
  `{ "Locations": [ { LocationId, DisplayLocation, LocationBarcode,
  Items: [ { ItemId, ItemDescription, OnHandQty, OnHandDisplay } ] } ] }`.
- **`archive/location_inventory_report_COMPACT_FORMAT_BACKUP.jrxml`** — a
  snapshot of the report in the newer "compact" JRXML format, kept from
  before the classic-format conversion, in case it's ever useful again.

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

**Update — the `.*` suffix question is now resolved, and it does matter.**
After the field-mapping fix, the QR removal, and the `Locations`→`Data`/
`OnHandQty`→`OnHandSum` payload-shape fixes (see later sections), the
report finally deployed and rendered a PDF — but kept excluding data.
The root cause was exactly the `.*` question flagged above: **both**
`<queryString language="jsonql">Data</queryString>` **and**
`subDataSource("Items")` needed the suffix — `Data.*` and `Items.*`.

Glean's own diagnosis at this point only proposed fixing the root query
(`Data` → `Data.*`) and explicitly said to leave `subDataSource("Items")`
unchanged. **That second half was checked against `JsonQLDataSource`'s
actual source before applying anything** (fetched directly from
`TIBCOSoftware/jasperreports` on GitHub) — `subDataSource(String)`
constructs a new `JsonQLDataSource` using the exact same
`jsonQLExecuter.selectNodes(root, selectExpression)` call, with
`next()`/`recordCount()` just iterating whatever list that returns, as
the root query. There's no special-casing between the two — a
`subDataSource()` call is mechanically just another JsonQL query,
evaluated with the current location object as its root instead of the
whole document. If `Data` needs `.*` to iterate, `Items` needs it for
the identical mechanical reason. Both were changed together:

- `<queryString language="jsonql"><![CDATA[Data.*]]></queryString>`
- `subDataSource("Items.*")`

**General rule for any future JsonQL query in this file (or a new
report)**: a bare field name referring to an array (`Foo`) selects the
array itself as a single node. Appending `.*` (`Foo.*`) expands it into
one node per element — required whenever the intent is "one report
record per array element," which is true for essentially every
list/subreport binding in a report like this one. Apply it consistently
to *every* JsonQL selectExpression that targets an array, not just
whichever one the visible symptom happens to point at first — this
whole detour happened because the two array-selecting expressions in
this file were fixed one upload at a time instead of both at once.

Since Studio 7.0.6 doesn't have `JsonQLDataSource` at all (JsonQL support
was restructured again in the 7.x line), none of this can be verified
locally — the WMS upload is the only ground truth, same as everything
else in this document.

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

- **The blind-quantity line is two separate elements, not one.** A
  `staticText` with a fixed position and fixed width (the blank
  underscore line — always identical across every row, regardless of
  data) plus a separate small `textField` positioned to start exactly
  where the line ends, left-aligned, holding `(actualQty)` in small grey
  text. **Do not combine these into one text run** — if the line and the
  quantity are one right-aligned block, a wider number (e.g. `1,737`)
  pushes the whole block, including the line itself, further left than a
  narrower number (`750`) would, so the blank lines stop lining up
  between rows. Splitting them was the actual fix for that.
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
- The QR code is built by accumulating a report `<variable>`
  (`resetType="Report"`, `calculation="Nothing"`) across every detail
  record, then rendering it in the **Summary band only — never the
  Title band.** (An earlier version of this note claimed the title band
  would work too, reasoning that JasperReports evaluates a variable
  "across the whole fill" regardless of which band displays it — **that
  was wrong**, confirmed both by an actual blank-QR symptom on a real
  WMS run and independently by Glean's diagnosis of the same file. A
  `<title>` band is filled and printed *before* any detail records are
  processed at all, so a variable referenced there only ever sees its
  initial value — it is not retroactively updated once later bands fill
  it in. Only a `<summary>` band, which prints after every detail record
  has run, actually sees the fully-accumulated value. If a design
  request ever asks to move the QR back to the title/header area for
  layout reasons, that's a real tension with no clean answer — either
  keep it in the summary band and accept it printing at the end, or
  find a different way to pre-compute all location IDs before the
  report starts filling (e.g. a two-pass query) rather than
  accumulating them during the fill.

## QR code temporarily removed from cyclecountsheet.jrxml (as of this writing)

After the field-mapping fix above, a real upload attempt still failed
with a bare `java.lang.NullPointerException` — no message, no stack
trace at all. Glean's diagnosis was that the self-referencing
`AllLocationsCsv` variable itself was the unsafe part. **That specific
theory is doubtful** — that accumulator pattern is a completely standard
JasperReports idiom, and it was independently verified end-to-end
against the real JasperReports 7.0.6 engine earlier in this project
(full compile → fill → PDF export, no error) — but the *action* taken
was reasonable regardless: both the variable and the entire `<summary>`
band containing the QR component were removed, to isolate whether the
core location/item table renders correctly on its own, deferring the QR
feature until that's confirmed.

**A more likely explanation**, matching the "Runtime library
dependencies" section below: QR rendering needs barcode4j, ZXing, Batik,
and a PDF backend all present on whatever engine executes the report,
separate from anything in the JRXML — none of that has ever been
confirmed present in MAWM's JasperReports 6.4.0 deployment. A bare,
message-less NPE is a plausible symptom of that class of failure. If the
location/item table now renders correctly with this QR-free version,
that's evidence for this theory over the self-referencing-variable one —
worth confirming with whoever manages the WMS's JasperReports deployment
whether barcode/QR support is actually installed, rather than continuing
to tweak the JRXML if it comes back to bite again once the QR is
re-added.

**To re-add the QR code once the core report is confirmed working**, the
variable and summary band removed here were (immediately before removal):

```xml
<variable name="AllLocationsCsv" class="java.lang.String" resetType="Report" calculation="Nothing">
    <variableExpression><![CDATA[($V{AllLocationsCsv} == null || $V{AllLocationsCsv}.equals("")) ? ($F{DisplayLocation} == null ? $F{LocationId} : $F{DisplayLocation}) : $V{AllLocationsCsv} + ";" + ($F{DisplayLocation} == null ? $F{LocationId} : $F{DisplayLocation})]]></variableExpression>
    <initialValueExpression><![CDATA[""]]></initialValueExpression>
</variable>
```

```xml
<summary>
    <band height="98">
        <componentElement>
            <reportElement x="446" y="0" width="94" height="94" uuid="e5b7c3a2-1f6d-4e89-8a2c-3d9f7b0c4a63"/>
            <jr:QRCode>
                <jr:codeExpression><![CDATA[$V{AllLocationsCsv}]]></jr:codeExpression>
            </jr:QRCode>
        </componentElement>
    </band>
</summary>
```

(`location_inventory_report.jrxml`, the Studio-editable copy, still has
the QR code intact in its own compact-format equivalent — this removal
only applies to the WMS deployment copy.)

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

Direct proof was possible for the **compact-format** file: JasperReports
jars extracted straight from Jaspersoft Studio's own installation
(nested inside `net.sf.jasperreports_7.0.6.final.jar`'s `lib/` folder —
digester, beanutils, jackson, barcode4j, zxing, batik, openpdf all
needed adding one at a time) let a real compile → fill → PDF export run
from the command line, independent of the Studio GUI.

**This does not work for the classic-format file.** JasperReports'
classic-format loader (`LegacyXmlLoader`) has a deliberate license gate —
it only proceeds if either a paid JasperReports license is present, or
it detects it's running inside Eclipse's own OSGi classloader with
Jaspersoft Studio's classes reachable. Outside Studio, it always returns
"no loader available," with no way around it. So for `cyclecountsheet.jrxml`:

1. Validate it's well-formed XML and schema-valid against JasperReports'
   own bundled classic XSDs (`jasperreport.xsd`, `components.xsd`,
   `barcode4j.xsd`) — this catches structural/naming mistakes without
   needing the real engine. (Note: validating two same-namespace XSDs —
   `components.xsd` and `barcode4j.xsd` — as separate `Source` objects in
   one Java `SchemaFactory` call only fully registers whichever one
   loads *first*; the other's top-level elements get incorrectly flagged
   as invalid. Test with both orderings and only trust an error that
   shows up in *both* runs.)
2. Actually upload it to the WMS. This is the only real ground truth for
   anything past basic schema validity — expression compilation errors
   (like the `JsonDataSource` package mismatch) and other engine-version
   differences only surface there.

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
   vs plain JSON" above, **including appending `.*` to any
   selectExpression that targets an array** (both the root `queryString`
   and every `subDataSource()` call need it — confirmed, not optional).
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

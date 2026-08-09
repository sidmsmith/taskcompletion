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
  translated to classic JRXML syntax for the WMS's older engine, using
  `net.sf.jasperreports.engine.data.JsonDataSource` (the pre-7.x package
  location). **Never open/save this one in Jaspersoft Studio** — Studio
  will silently rewrite it back into the newer format and break it again.
  It carries an inline XML comment at the top saying the same thing.
- **`location_inventory_sample.json`** — sample payload used by Studio's
  "Location Inventory JSON" data adapter for Preview. Root shape:
  `{ "Locations": [ { LocationId, DisplayLocation, LocationBarcode,
  Items: [ { ItemId, ItemDescription, OnHandQty, OnHandDisplay } ] } ] }`.
- **`archive/location_inventory_report_COMPACT_FORMAT_BACKUP.jrxml`** — a
  snapshot of the report in the newer "compact" JRXML format, kept from
  before the classic-format conversion, in case it's ever useful again.

## The core problem: two JasperReports versions, two file formats

Jaspersoft Studio installed on this machine bundles **JasperReports
7.0.6**. The WMS runs a **noticeably older** JasperReports engine (exact
version unknown, but old enough to predate several 7.0 changes — see
evidence below). These two engines disagree on:

1. **JRXML syntax itself.** Studio 7.0.6 introduced a newer "compact"
   JRXML format (`<element kind="textField" ...>`, bare `<expression>`
   tags, no XML namespace declaration on the root element) and — this is
   the trap — **silently rewrites any file into this format the first
   time you save it in Studio**, even a file that started out in the
   older "classic" format (`<textField><reportElement/><textElement/>
   <textFieldExpression/></textField>`, namespaced root element). The
   WMS's older engine only understands the classic format.
2. **Where `JsonDataSource` lives.** JasperReports relocated JSON data
   source support from `net.sf.jasperreports.engine.data.JsonDataSource`
   to `net.sf.jasperreports.json.data.JsonDataSource` as part of the 7.0
   modularization. Studio 7.0.6 only has the new location; the WMS's
   engine only has the old one. **This is why the two `.jrxml` files
   must permanently differ on that one line** — there is no single class
   reference that satisfies both engines at once.

Evidence the WMS is on an older version (in case this ever needs
re-confirming, or matters for a future report): its schema validator
rejects a `uuid` attribute on the root `<jasperReport>` element (a
Studio-only convenience attribute, safe to just omit) and enforces
`isBold`/`isForPrompting`-style naming rather than the newer
`bold`/`forPrompting` shorthand — both consistent with a schema that
predates several 7.x-era conventions.

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
  record, then only rendering it in a **Summary/Title band** — the one
  place that sees the fully-accumulated value. A `<title>` band prints
  before any records are processed and a `<summary>` band after all of
  them, so which one you use determines whether you need the
  accumulator to already be complete — in this report the QR moved to
  the **Title** band per a later request, which works because
  JasperReports evaluates the variable across the *whole* fill and the
  title/summary text field just reads its final value at render time
  regardless of which band displays it.

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
   ask Claude to), remembering: classic syntax, and the
   `net.sf.jasperreports.engine.data.JsonDataSource` package on that one
   line stays as the pre-7.0 path — don't copy Studio's version of that
   line over.
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

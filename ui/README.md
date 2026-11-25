# Cairn explorer

React UI for the permanent-artifact API (protocol 4). Artifact IDs are plain hex;
finished artifacts retain their IDs and receive a `vN` label. History lists finished
artifacts sharing a name, without a separate revision entity.

Install `cairn2[ui]` and run `cairn --repo PATH server`. The UI is served on the same
port. `--no-ui` disables it; `--no-auth` enables development access without a token.
Normal sessions accept the server token or the one-time login URL printed at startup
and cache authentication in a 12-hour HttpOnly cookie.

For development, run the server on port 18765 with `--no-auth`, then:

```sh
npm ci --prefix ui
npm run dev --prefix ui -- --host 0.0.0.0
```

`CAIRN_DEV_API` overrides the Vite API proxy. `npm run build --prefix ui` typechecks
and writes the packaged assets into `cairn/ui_static`.

Build the media demo with:

```sh
cd examples/showcase
uv run --extra all python run.py
uv run --extra all cairn server --no-auth --port 18765
```

The script records a run with figure, video and audio captures in the
`neural-rendering` project. Rerun it to add a run version.

Against that running server, `npm test --prefix ui` checks lists, plain IDs, tags,
media zoom, video, lineage, live runs and the workspace. `CAIRN_UI_URL` overrides the default test URL `http://localhost:18765`.
Python integration tests are in `tests/test_webui.py`.

## Shared components

Use `src/components` for buttons, fields, selects, cards, status, tables, metrics,
and charts. `/palette` previews the base components. Use CSS tokens
from Cairn Report (`--paper`, `--card`, `--ink`, `--muted`, `--line`, `--accent`) rather
than introducing colors per viewer. Project card structure comes from Prism2.

`ZoomImage` and `MediaPlayback` compose one `MediaLightbox`, using shared
`MediaIconButton` controls. The modal is portaled outside its page to prevent
page styles from leaking into media. Wheel/pinch zoom, bounded pan, fit, fullscreen,
frame stepping, clip marking, region selection and reference copying share this viewer.
Frame stepping uses the selected FPS and browser time-based seeking.

A workspace Viewer card is YAML read by `readView`; `ViewerFrame` runs its HTML file
sandboxed from a signed `/ui-api/viewers/` link that serves the artifact's other files by
relative path, and passes the settings as JSON in the URL fragment with each
`cairn:REF#file` replaced by such a link. Large chunked files stream through the same route. Project and record views refresh every five seconds, with a global pause.

`VideoTimeline` supports pointer-drag interval selection and keyboard-adjustable
endpoints. The lightbox can combine that interval with a pixel region in one copied
reference. `PlotlyFigure` renders Plotly MIME output with local, lazy-loaded Plotly,
Cairn colors and typography, and interactive zoom/legend/export controls.

Artifact and run pages include editable tag chips stored as `annotations.tags`.
Project lists offer matching-all tag filters, kept in the URL, and tag chips in
artifact/run rows. Annotation ETags protect against overwriting concurrent edits.
Displayed IDs copy their full value; code blocks copy the original code with a
brief tick confirmation.

Lightbox zoom controls and gesture hints occupy a separate row below the media.

## Run workspace

`/p/:project/workspace` displays run records as they arrive. Cards
use the existing record-query, resolution, and blob APIs. Scalar cards use uPlot, as in Cairn 1, and select X,
Y, split fields, point order, and independent legend order. Splitting preserves
individual observations by default. Optional aggregation draws a mean, median,
minimum, or maximum per group, with a min–max, sample-standard-deviation, or
standard-error band and optional faint member lines. Each run contributes its
last record at or before X, bounded by its recorded range; duplicate X values
use its last record, and explicit nulls interrupt contributions. These optional
settings are saved on cards; older layouts remain unaggregated. Missing fields are skipped,
explicit null/invalid numeric values break lines, and log axes exclude nonpositive
values. Home/reset ranges include padding around the data. Near-horizontal
drags zoom X only, near-vertical drags zoom Y only (20 px tolerance), and box
drags zoom both axes. Click points or media record numbers to inspect the original record.

Image, video and Plotly cards offer ordered, paginated galleries, grouped comparisons,
and sequences with a slider over any scalar field. Exact matching is the default;
previous and nearest-numeric matching are explicit settings. All duplicates at the
chosen axis value remain visible. Captions report the actual observation axis.
Moving the slider never blanks a tile: each tile keeps its frame until the next has
loaded and decoded, neighbouring steps are prefetched, and fetched blobs are shared
up to 256 MiB.
Plotly figures retain their own specifications; optional linked 2D zoom assumes
matching units. Image and video bytes are fetched from existing blob references; a
video is a record field holding `cairn.File` with a `video/*` media type.

Field dropdowns list keys and types discovered in the selected runs, with numeric
fields for scalar axes and the corresponding media types for image, video and Plotly cards.
Cards default to grouping by run. The compact **Group by** picker adds fields
immediately; its chips remove them. Each combination identifies one series.
**Combine filter conditions** is shown when there are multiple record filters.
All workspace dropdowns use the palette’s shared Select component. Cards are
created in their section and moved by dragging, without a placement setting.
Valid settings apply and save immediately; Done or Escape closes the dialog.
Open settings lock background scrolling while their own controls remain scrollable.
Closing, Escape, and browser navigation release the lock; nested inspectors keep
it until the last dialog closes. Incomplete edits retain the last valid settings. One Undo restores the settings
from before the edit session. New cards still use Add card / Cancel. Field selectors use literal top-level keys (`step`, `loss`), JSON pointers for
nested data (`/metrics/loss`), and reserved run fields (`@run`, `@name`, `@version`,
`@ordinal`, `@created`, `@config/seed`). There is no expression language. Card
filters accept text, numbers, or JSON; they apply before splitting and sorting.

The main navigation defaults to an icon rail and expands with its fold button.
Run rows use one compact line, with full names and status available on hover. Eye
buttons are the sole per-run selection control: hidden runs are excluded from
card queries. A global eye in the Runs header hides all visible runs or restores
automatic inclusion of all matching runs when any are hidden. Saving uses the existing v1 `selected` field, so
visibility survives reload without extending the saved format. Run colors are
deterministic from artifact identity, so selection, filtering, refreshes, and
reloads cannot reshuffle them. The same color marks scalar series and media tiles;
logged Plotly figures keep their own internal trace colors.

Run names open the normal run detail page on Overview. Its **Workspace** tab
(`/p/:project/a/:runId?tab=workspace`) embeds the same workspace component scoped
to that exact run, without the run-selector sidebar. Overview, Records, Files,
and the other detail tabs remain available. It uses the project's saved panel
layouts and ignores project run filters/visibility, including for older versions.
The initial `/p/:project/workspace/:runId` URLs redirect to this tab.

The run selector stays to the left of the card canvas (collapsible on small
screens). Cards are always draggable and resizable, with duplication and removal
available in their menus. Sections share one card size: widths are 1, ½, ⅓, or ¼
of the grid, and resizing any card changes all cards in its section. Cards moved
into a section adopt its size. Card settings contain no section sizing controls; use the resize handle.
Older saved layouts remain readable in the v1 format; the first card
in each section supplies its height and nearest supported width. This adjustment
is applied in memory until the layout is saved. Undo restores up to 30 layout changes. Save and Save
as store version-1 layouts in the project on the server, where agents read and write
them too; data is not copied. A save based on an older revision is refused instead of
overwriting. The browser remembers which layout was open, and layouts that earlier
versions kept in localStorage can be moved to the project. Card settings save automatically; other unsaved layout edits remain temporary. Saving the example removes
its preset URL parameter so reloading opens the saved view.

The workspace reads the records of all selected runs from up to four compressed streams
(`POST /ui-api/observations`, private to the UI). A run appears once all its records have
arrived, so no run is ever drawn partially, and cards fill in run by run. There is no
fixed record-count or response-byte cutoff; records stay in browser memory, also while
their run is hidden. A failed or incomplete first load shows an error with no charts.
Afterwards only the new records of live runs are fetched, every second (less often when
drawing them takes long), while the page is visible and auto-refresh is not paused. The
run picker refreshes every five seconds. Refresh loads everything again. Hidden runs are
remembered per layout in this browser; a layout's own `selected` applies until then.

The reproducible synthetic demo lives in `examples/training/workspace_demo.py`.
Open `/p/training-example/workspace?example=cards` for the eight-card tour. The
workspace Playwright suite mocks streams, so it also runs without demo records:
`npm test -- --project=desktop workspace.spec.ts` against a running Cairn UI.

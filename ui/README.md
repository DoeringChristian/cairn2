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

Build the media/report demo with:

```sh
uv run --extra all python examples/build_report_showcase.py
uv run --extra all cairn server --no-auth
```

The script uses normal repository discovery, records a run, and always publishes
the report in the `neural-rendering` project. Build files stay in
`build/report-showcase/<run-id>/`.

Against that running server, `npm test --prefix ui` checks native reports, plain IDs,
image zoom, real video playback, the TOC, and lineage. `CAIRN_UI_URL` overrides the
default test URL `http://localhost:18765`. Python integration tests are in
`tests/test_webui.py`.

## Shared components

Use `src/components` for buttons, fields, selects, cards, status, tables, metrics,
charts and report rendering. `/palette` previews the base components. Use CSS tokens
from Cairn Report (`--paper`, `--card`, `--ink`, `--muted`, `--line`, `--accent`) rather
than introducing colors per viewer. Project/report card structure comes from Prism2;
report layout and typography come from Cairn Report.

`ZoomImage` and `MediaPlayback` compose one `MediaLightbox`, using shared
`MediaIconButton` controls. The modal is portaled outside report content to prevent
prose styles from leaking into media. Wheel/pinch zoom, bounded pan, fit, fullscreen,
frame stepping, clip marking, region selection and reference copying share this viewer.
Frame stepping uses the selected FPS and browser time-based seeking.

Reports render `document.json` in normal page flow. `ReportToc` discovers rendered
headings and uses the existing left rail without shrinking the report. On mobile it
is collapsible above the report. `SelectionCopy` copies selected prose and its source
location. Project and record views refresh every five seconds, with a global pause.

`VideoTimeline` supports pointer-drag interval selection and keyboard-adjustable
endpoints. The lightbox can combine that interval with a pixel region in one copied
reference. `PlotlyFigure` renders Plotly MIME output with local, lazy-loaded Plotly,
Cairn colors and typography, and interactive zoom/legend/export controls.

Report comments are saved in the server repository. The UI comment routes group
discussion by project, namespace and report name, so new published versions retain
the discussion while each comment keeps its original artifact, quote and source
anchor. Browser sessions may add report comments; other repository writes still
require the bearer token. Select report text to reveal copy and comment icons, or
click an existing comment marker beside its passage to reopen and edit it.

The selection comment editor is a floating text box. Edits autosave after a short
pause; clicking outside flushes pending changes and closes it. Enter inserts a
newline. A small delete icon removes the comment across all report versions. Commented quotations are highlighted
without changing report layout. Highlight matching is scoped to the original
source file; unmatched quotations remain accessible from a marker at the start of the report.

The report overview groups builds by report name and opens the latest finished
version by default. The artifact detail selector retains access to older versions.

Custom HTML containing scripts, styles or canvas runs in an opaque-origin iframe
with scripts enabled and an independent CSP. It can use WebGPU in a browser that
supports it on localhost/HTTPS. It cannot access the parent report/session DOM;
external libraries/data can be loaded over HTTPS. Height messages are accepted only
from the corresponding frame. The demo falls back to Canvas when no GPU adapter
is available. Static HTML tables continue to render directly in the report.

Code fences use the shared `SyntaxCode` renderer with escaped, language-aware
highlighting. Plain fenced code is never executed. Image, video, audio, Plotly and
HTML outputs support comments. Clearing a comment and clicking outside deletes it.

Artifact and run pages include editable tag chips stored as `annotations.tags`.
Project lists offer matching-all tag filters, kept in the URL, and tag chips in
artifact/run rows. Annotation ETags protect against overwriting concurrent edits.
Displayed IDs copy their full value; code blocks copy the original code with a
brief tick confirmation. Text, media, selection, and lightbox comment controls share `CommentButton`. Media controls appear on the right and reopen an existing comment when present.

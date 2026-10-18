# Cairn2 media component provenance

Adapted from the user-authorized `../prism2` media viewer and playback components.
Cairn2 uses Cairn Report theme tokens and its shared buttons/select controls, plain
artifact IDs and report source locations. The upstream notice follows unchanged.

---

# Media component provenance

Authorized reference: `../cairn` (Python package `cairn-track`), inspected 2026-10-17.
The user requested Cairn-like image/video cards and lightboxes. No source from the
forbidden sibling `../prism` was read.

`geometry.ts` adapts `vendor/cairn-ui/src/lib/media/view-geometry.ts` directly,
replacing its dependency imports with local scale constants and a transform type.
It retains Cairn's normalized media-center coordinate system, fit/resize transforms,
and bounded panning. The source SHA-256 is
`f3a705d8f26ec8ba1780ea6e72d456aaad0ac57b17d93b7e0dd6d94ac724572c`.
The accompanying `LICENSE` preserves Apache-2.0 licensing.

`vendor/cairn-ui/src/components/viewers/VideoViewer.tsx` informed video inspection
interactions; source SHA-256:
`eb8f088388c9eb3ba59a887b9af301bb8b41a93c4cce20ccb4064235720eb7e5`.
Prism's `MediaLightbox.tsx`, presentation transport, point-cloud host and content cards
are new React implementations. They use the existing Folio-derived reader styling
and Prism's exact-revision blob API. No Cairn Python/runtime dependency is introduced.

The viewer uses explicit measured image/video fit dimensions to avoid intrinsic SVG
layout collapsing to zero. Browser validation is in `web/scripts/media-smoke.mjs`:
SVG/PNG sizing, image zoom/regions, MP4 zoom/pan/seek/frame-step/clip markers, opaque
HTML origin and a real Three.js WebGL canvas. Frame stepping is time-based at the
selected FPS; browser decoders do not guarantee exact frame-index seeking.

`vendor/cairn-ui/src/components/media/ZoomSplitPane.tsx` (SHA-256
`044896349e0e94785db9cc00f6db8418399cb8a24bf7ab05ff8991a4623f2ccf`) informed
cursor-anchored wheel/pinch, direct drag pan, double-click fit and crisp pixels at
large magnification. Video-only transforms keep fixed transport controls visible.
Prism coalesces gesture state with requestAnimationFrame; media elements remain
mounted during playback and gestures. Bounds are normalized before storing centers.

`VideoPreview.tsx` and `video-preview.css` are new shared inline controls, composed
with Prism/Folio tokens and Cairn-style inspect actions. They also attach to existing
sanitized report-output videos without replacing surrounding document DOM. Native
controls are disabled only on these application-owned video paths. Sandboxed HTML
and custom viewers retain their own controls. The old exported MediaViewer now
delegates to the same lightbox, preserving its compatibility entry point.

Validation includes actual Chromium two-touch pinch, anchored wheel geometry,
resize-center retention, unchanged video element identity while playing, initial
frame preservation, clip markers, current-time frame stepping and 280px-wide inline
controls. Browser frame stepping remains time-based, not exact decoder frame-index
control; single-video controls do not implement Cairn's synchronized compare clocks.

"""Disposable UI projections; published report bundles are never modified."""

import base64
import binascii
import json
import re
import threading
from collections import OrderedDict

MEDIA = (
    "video/mp4",
    "video/webm",
    "audio/wav",
    "audio/mpeg",
    "audio/ogg",
    "image/svg+xml",
    "image/png",
    "image/jpeg",
)


class ReportPreviews:
    def __init__(self, repository):
        self.repository = repository
        self.cache = OrderedDict()
        self.bytes = 0
        self.lock = threading.Lock()
        self.md = None

    def parse(self, document, blobs):
        """Parse what documents before v4 left as Markdown, as a v4 build stores it."""
        from .reports import (
            _div_attributes,
            _markdown_dependencies,
            _markdown_engine,
            _native_node,
            _resolved,
        )

        if self.md is None:
            self.md = _markdown_engine(_markdown_dependencies())
        labels = document.get("labels", {})

        def nodes(tokens, source):
            return [_native_node(token, source) for token in _resolved(tokens, labels)]

        def inline(text, source):
            tokens = _resolved(self.md.parseInline(str(text), {}), labels)
            return [_native_node(token, source) for token in tokens[0].children]

        def visit(node, source):
            if node["type"] == "inline" and not node.get("children") and node.get("content"):
                text = re.sub(r"\s*\{#(?:sec|fig|tbl)-[\w.-]+\}\s*$", "", node["content"])
                node["children"] = inline(text, node.get("source", source))
            if node["type"].startswith("container_") and node["nesting"] == 1:
                attributes = _div_attributes(node.get("info", ""))
                node["attributes"] = {**attributes, "class": " ".join(attributes["class"])}
            for child in node.get("children") or []:
                visit(child, source)

        for block in document.get("blocks", []):
            source = block.get("source")
            for node in block.get("nodes") or []:
                visit(node, source)
            options = block.get("options") or {}
            if caption := options.get("fig-cap") or options.get("tbl-cap"):
                block["caption_nodes"] = inline(caption, source)
            if options.get("fig-subcap"):
                block["subcaption_nodes"] = [inline(text, source) for text in options["fig-subcap"]]
            for output in block.get("outputs", []):
                data = output.get("data", {})
                value = data.get("text/markdown")
                if isinstance(value, dict) and value.get("asset") in blobs:
                    with self.repository._open_blob(blobs[value["asset"]], lease=False) as stream:
                        value["nodes"] = nodes(self.md.parse(stream.read().decode(), {}), source)
                elif value is not None:
                    text = "".join(value) if isinstance(value, list) else str(value)
                    data["text/markdown"] = {
                        "mime": "text/markdown",
                        "nodes": nodes(self.md.parse(text, {}), source),
                    }

    def get(self, digest, blobs=()):
        with self.lock:
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest][0]
            with self.repository._open_blob(digest, lease=False) as stream:
                document = json.load(stream)
            outputs = {}
            for block_index, block in enumerate(
                document.get("blocks", []) if document.get("version", 1) == 1 else []
            ):
                for output_index, output in enumerate(block.get("outputs", [])):
                    data = output.get("data", {})
                    key = f"{block_index}/{output_index}"
                    # Match the native reader's MIME preference exactly.
                    mime = next(
                        (
                            mime
                            for mime in ("application/vnd.plotly.v1+json", *MEDIA)
                            if data.get(mime)
                        ),
                        None,
                    )
                    if mime is None:
                        continue
                    raw = data[mime]
                    if mime == "application/vnd.plotly.v1+json":
                        payload = json.dumps(raw, separators=(",", ":")).encode()
                    else:
                        raw = "".join(raw) if isinstance(raw, list) else str(raw)
                        try:
                            payload = (
                                raw.encode() if mime == "image/svg+xml" else base64.b64decode(raw)
                            )
                        except (binascii.Error, ValueError):
                            # Keep malformed rich output as-is rather than hiding the whole report.
                            continue
                    outputs[key] = (payload, mime)
                    output["ui_payload"] = {"path": key, "mime": mime}
                    output["data"] = {}
            self.parse(document, {blob["name"]: blob["digest"] for blob in blobs})
            preview = json.dumps(document, separators=(",", ":")).encode()
            result = (preview, outputs)
            size = len(preview) + sum(len(payload) for payload, _ in outputs.values())
            budget = 64 * 1024 * 1024
            if size <= budget:
                while self.cache and (self.bytes + size > budget or len(self.cache) >= 8):
                    _, (_, previous_size) = self.cache.popitem(last=False)
                    self.bytes -= previous_size
                self.cache[digest] = (result, size)
                self.bytes += size
            return result

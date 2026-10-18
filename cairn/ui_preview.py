"""Disposable UI projections; published report bundles are never modified."""

import base64
import binascii
import json
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

    def get(self, digest):
        with self.lock:
            if digest in self.cache:
                self.cache.move_to_end(digest)
                return self.cache[digest][0]
            with self.repository.open_blob(digest) as stream:
                document = json.load(stream)
            outputs = {}
            for block_index, block in enumerate(document.get("blocks", [])):
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

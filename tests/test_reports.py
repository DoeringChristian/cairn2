import json
import socket
import threading
import time

import pytest

from cairn import reports
from cairn.model import ValidationError
from cairn.sdk import Client
from cairn.storage import Repository


def seed(backend, value=7, name="baseline"):
    opened = backend.start("default", "runs", name)
    revision, writer = opened["artifact"]["id"], opened["writer"]["id"]
    backend.append_batch(
        revision, writer, [{"sequence": 1, "kind": "record", "data": {"score": value}}]
    )
    backend.close_writer(revision, writer, 1)
    return backend.finish(revision, value={"score": value})


@pytest.fixture(params=["local", "http"])
def client(request, tmp_path):
    if request.param == "local":
        value = Client(tmp_path / "repo")
        yield value
        value.backend.close()
        return
    import uvicorn

    from cairn.server import create_app

    repository = Repository(tmp_path / "repo")
    listener = socket.socket()
    listener.bind(("127.0.0.1", 0))
    port = listener.getsockname()[1]
    token = "report-test-secret-must-not-be-captured"
    server = uvicorn.Server(uvicorn.Config(create_app(repository, token=token), log_level="error"))
    thread = threading.Thread(target=lambda: server.run(sockets=[listener]), daemon=True)
    thread.start()
    deadline = time.monotonic() + 10
    while not server.started and time.monotonic() < deadline:
        time.sleep(0.01)
    assert server.started
    value = Client(f"http://127.0.0.1:{port}", token=token)
    try:
        yield value
    finally:
        value.backend.close()
        server.should_exit = True
        thread.join(timeout=10)
        listener.close()
        repository.close()


def test_inputs_pinned_before_execution_publish_export_and_copy(client, tmp_path, monkeypatch):
    original = seed(client.backend)
    source = tmp_path / "analysis.qmd"
    source.write_text("""---
title: A pinned analysis
cairn:
  inputs:
    baseline: runs/baseline@latest
---
## Results {#sec-results}

A footnote can cross a Python cell.[^note]

```{python}
#| label: tbl-scores
#| tbl-cap: Frozen scores
from IPython.display import Markdown, display
assert inputs["baseline"].read()["score"] == 7
assert client.resolve("runs/baseline@latest").read()["score"] == 7
assert inputs["baseline"].records()[0]["data"]["score"] == 7
# Explicit independent clients keep their ordinary semantics in the kernel.
from cairn.sdk import Client
with Client(_endpoint, project=_project, token=_source_client.token) as independent:
    assert independent.resolve("runs/baseline@latest").read()["score"] == 99
print("frozen score: 7")
display(Markdown("| item | score |\\n| - | - |\\n| baseline | 7 |"))
```

See @tbl-scores and @sec-results. The equation is $x^2$.

$$y = x^2$$ {#eq-square}

See @eq-square.

[^note]: Its definition comes after execution.
""")
    real_pin = reports._pin_inputs

    def move_head_after_pinning(client, metadata):
        pins = real_pin(client, metadata)
        seed(client.backend, 99)
        return pins

    monkeypatch.setattr(reports, "_pin_inputs", move_head_after_pinning)
    result = reports.build(source, tmp_path / "build", client=client, timeout=30)
    assert result.manifest["inputs"][0]["artifact_id"] == original["id"]
    assert client.backend.resolve("runs/baseline@latest")["id"] != original["id"]
    page = (result.path / "index.html").read_text()
    assert "frozen score: 7" in page
    assert 'href="#tbl-scores">Table 1</a>' in page
    assert 'href="#eq-square">Equation 1</a>' in page
    assert "<math" in page and "footnote-ref" in page
    assert "Its definition comes after execution" in page
    publication = reports.publish(result.path, client=client, name="analysis/summary")
    version = publication
    assert version["namespace"] == "reports"
    assert client.resolve("reports/analysis/summary@latest").descriptor["id"] == version["id"]
    assert {d["artifact_id"] for d in version["manifest"]["dependencies"]} == {original["id"]}
    assert (
        reports.publish(result.path, client=client, name="analysis/summary")["id"] == version["id"]
    )
    exported = reports.export(version["id"], tmp_path / "exported", client=client)
    assert (exported / "index.html").read_bytes() == (result.path / "index.html").read_bytes()
    exported_replay = reports.build(
        exported / "sources/analysis.qmd", tmp_path / "export-replay", client=client, timeout=30
    )
    assert exported_replay.manifest["inputs"][0]["artifact_id"] == original["id"]
    copied = reports.copy(version["id"], tmp_path / "copied", client=client)
    copied_source = copied.read_text()
    assert original["id"] in copied_source
    assert "baseline: runs/baseline@latest" not in copied_source
    assert (copied.parent / "cairn-lock.json").is_file()
    replay = reports.build(copied, tmp_path / "replay", client=client, timeout=30)
    assert replay.manifest["inputs"][0]["artifact_id"] == original["id"]
    assert "frozen score: 7" in (replay.path / "index.html").read_text()
    for path in result.path.rglob("*"):
        if path.is_file():
            assert b"report-test-secret-must-not-be-captured" not in path.read_bytes()
    (result.path / "index.html").write_text("tampered")
    with pytest.raises(ValidationError, match="changed since build"):
        reports.publish(result.path, client=client)


def test_includes_resources_images_static_html_and_source_locations(tmp_path):
    (tmp_path / "parts").mkdir()
    (tmp_path / "data.txt").write_text("captured data")
    (tmp_path / "parts" / "pixel.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"><rect width="2" height="2"/></svg>'
    )
    (tmp_path / "parts" / "results.qmd").write_text("""## Included results {#sec-included}

![A captured figure](pixel.svg){#fig-pixel}

```{python}
#| echo: false
from pathlib import Path
from IPython.display import HTML, SVG, display
print(Path("data.txt").read_text())
display(HTML("<table><tr><td>table output</td></tr></table>"))
display(SVG('<svg xmlns="http://www.w3.org/2000/svg" width="4" height="4"><circle cx="2" cy="2" r="2"/></svg>'))
```
""")
    source = tmp_path / "main.qmd"
    source.write_text("""---
title: Included report
cairn:
  resources: [data.txt]
---
{{< include parts/results.qmd >}}

See @fig-pixel.

::: {.callout-warning title="Recorded inputs"}
Sources and local resources are captured.
:::
""")
    result = reports.build(source, tmp_path / "build", timeout=30)
    page = (result.path / "index.html").read_text()
    assert 'sandbox=""' in page and "captured data" in page
    assert "Recorded inputs" in page
    assert 'data-source-path="parts/results.qmd"' in page
    assert (result.path / "sources/parts/results.qmd").is_file()
    assert (result.path / "sources/data.txt").read_text() == "captured data"
    assert len(list((result.path / "assets").glob("*.svg"))) == 2
    document = json.loads((result.path / "document.json").read_text())
    code = next(block for block in document["blocks"] if block["kind"] == "code")
    assert code["source"] == {"path": "parts/results.qmd", "line_start": 5, "line_end": 12}
    assert len(code["outputs"]) == 3
    client = Client(tmp_path / "repo")
    published = reports.publish(result.path, client=client)
    exported = reports.export(published["id"], tmp_path / "export", client=client)
    assert (exported / "sources/parts/pixel.svg").is_file()
    assert len(list((exported / "assets").glob("*.svg"))) == 2
    client.backend.close()


def test_failure_diagnostics_and_undeclared_input_policy(tmp_path):
    client = Client(tmp_path / "repo")
    seed(client.backend)
    source = tmp_path / "bad.qmd"
    source.write_text(
        '# Failure\n\n```{python}\nprint("before failure")\nclient.resolve("runs/baseline@latest")\n```\n'
    )
    with pytest.raises(reports.ReportBuildError) as failure:
        reports.build(source, tmp_path / "failed", client=client, timeout=30)
    directory = failure.value.directory
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"]["source"]["line_start"] == 3
    assert "undeclared reference" in (directory / "diagnostics.txt").read_text()
    assert "before failure" in (directory / "executed.ipynb").read_text()
    with pytest.raises(ValidationError, match="Only complete"):
        reports.publish(directory, client=client)
    assert client.backend.list_artifacts(namespace="reports")["items"] == []
    client.backend.close()


@pytest.mark.parametrize(
    "content",
    [
        "{{< include ../outside.qmd >}}\n",
        "![outside](../outside.svg)\n",
        "{{< include cycle.qmd >}}\n",
        "![remote](https://example.com/image.png)\n",
    ],
)
def test_unsafe_sources_fail_before_execution(tmp_path, content):
    source = tmp_path / "main.qmd"
    source.write_text(content)
    (tmp_path / "cycle.qmd").write_text("{{< include main.qmd >}}\n")
    with pytest.raises(reports.ReportBuildError):
        reports.build(source, tmp_path / "bad")
    assert not (tmp_path / "bad/executed.ipynb").exists()


def test_live_input_requires_finished_artifact(tmp_path):
    client = Client(tmp_path / "repo")
    opened = client.backend.start("default", "runs", "open")
    source = tmp_path / "live.qmd"
    source.write_text("---\ncairn:\n  inputs:\n    source: runs/open@live\n---\nNo execution.\n")
    with pytest.raises(reports.ReportBuildError, match="finish the artifact"):
        reports.build(source, tmp_path / "build", client=client)
    assert client.backend.versions(opened["artifact"]["id"]) == []
    client.backend.close()


def test_pinned_reader_scope_and_read_operations(client, tmp_path):
    original = seed(client.backend)
    pins = [
        {
            "requested": "runs/baseline@latest",
            "artifact_id": original["id"],
            "aliases": ["old-name"],
        }
    ]
    reader = reports.PinnedInputs(client, pins)
    latest = seed(client.backend, 99)
    assert reader.resolve("old-name").read() == {"score": 7}
    assert reader.resolve("runs/baseline@latest").records()[0]["data"] == {"score": 7}
    assert reader.backend.resolve_many(["old-name", original["id"]])[0]["id"] == original["id"]
    assert reader.backend.query("runs/baseline@latest")["items"][0]["data"] == {"score": 7}
    for operation in (
        lambda: reader.resolve("runs/undeclared@latest"),
        lambda: reader.backend.resolve_many(["runs/undeclared@latest"]),
        lambda: reader.backend.query(latest["id"]),
    ):
        with pytest.raises(ValidationError, match="undeclared reference"):
            operation()
    with pytest.raises(ValidationError, match="declared project"):
        reader.backend.resolve(original["id"], project="other")
    assert not hasattr(reader, "start")
    assert not hasattr(reader.backend, "append_batch")
    assert client.resolve("runs/baseline@latest").id == latest["id"]
    with Client(tmp_path / "independent") as independent:
        separate = seed(independent.backend, 123)
        assert independent.resolve("runs/baseline@latest").id == separate["id"]
    with pytest.raises(ValidationError, match="different versions"):
        reports.PinnedInputs(
            client, [*pins, {"requested": "old-name", "artifact_id": latest["id"]}]
        )

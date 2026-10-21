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
    other = seed(client.backend, 3, name="other")
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
import cairn
from IPython.display import Markdown, display
assert client.resolve("runs/baseline@latest").id == inputs["baseline"].id
assert inputs["baseline"].records()[0]["data"] == inputs["baseline"].read()
assert cairn.use("runs/other@latest").read()["score"] == 3
assert client.use("runs/other@latest", role="other").id == cairn.use("runs/other@latest").id
# Explicit independent clients keep their ordinary semantics in the kernel.
from cairn.sdk import Client
with Client(_endpoint, project=_project, token=_source_client.token) as independent:
    assert independent.resolve("runs/baseline@latest").read()["score"] == 99
print("frozen score:", inputs["baseline"].read()["score"])
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
    assert [item["artifact_id"] for item in result.manifest["inputs"]] == [
        original["id"],
        other["id"],
    ]
    assert result.manifest["inputs"][1] == {
        "requested": "runs/other@latest",
        "artifact_id": other["id"],
        "role": "input",
    }
    assert not (result.path / "inputs.jsonl").exists()
    monkeypatch.undo()
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
    assert {d["artifact_id"] for d in version["manifest"]["dependencies"]} == {
        original["id"],
        other["id"],
    }
    assert (
        reports.publish(result.path, client=client, name="analysis/summary")["id"] == version["id"]
    )
    exported = reports.export(version["id"], tmp_path / "exported", client=client)
    assert (exported / "index.html").read_bytes() == (result.path / "index.html").read_bytes()
    exported_replay = reports.build(
        exported / "sources/analysis.qmd", tmp_path / "export-replay", client=client, timeout=30
    )
    latest = client.backend.resolve("runs/baseline@latest")["id"]
    assert exported_replay.manifest["inputs"][0]["artifact_id"] == latest
    copied = reports.copy(version["id"], tmp_path / "copied", client=client)
    assert copied.read_text() == source.read_text()
    replay = reports.build(copied, tmp_path / "replay", client=client, timeout=30)
    assert replay.manifest["inputs"][0]["artifact_id"] == latest
    assert "frozen score: 99" in (replay.path / "index.html").read_text()
    for path in result.path.rglob("*"):
        if path.is_file():
            assert b"report-test-secret-must-not-be-captured" not in path.read_bytes()
    (result.path / "index.html").write_text("tampered")
    with pytest.raises(ValidationError, match="changed since build"):
        reports.publish(result.path, client=client)


def test_attributes_sub_superscript_definition_and_task_lists(tmp_path):
    (tmp_path / "box.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'
    )
    source = tmp_path / "markdown.qmd"
    source.write_text("""## Results {#sec-results .unnumbered}

![A box](box.svg){#fig-box.small width=50% .wide onerror=alert(1)}

See @fig-box.small and @sec-results: H~2~O, x^2^, [red]{.red onclick=x}, `x`{.py}.

Term
: Definition

- [x] done
- [ ] open
""")
    result = reports.build(source, tmp_path / "build")
    page = (result.path / "index.html").read_text()
    assert '<h2 id="sec-results">Results</h2>' in page
    assert '<figure id="fig-box.small"><img src="assets/' in page
    assert 'alt="A box" width="50%" class="wide" loading="lazy" />' in page
    assert "onerror" not in page and "onclick" not in page
    assert 'href="#fig-box.small">Figure 1</a>' in page
    assert "H<sub>2</sub>O, x<sup>2</sup>" in page and '<span class="red">red</span>' in page
    assert '<code class="py">x</code>' in page
    assert "<dt>Term</dt>\n<dd>Definition</dd>" in page
    assert 'checked="checked" disabled="disabled" type="checkbox"> done' in page
    document = json.loads((result.path / "document.json").read_text())
    image = next(
        child
        for node in document["blocks"][0]["nodes"]
        for child in node["children"]
        if child["type"] == "image"
    )
    assert image["attributes"]["width"] == "50%" and "id" not in image["attributes"]


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
    assert document["version"] == 2
    assert result.manifest["document_version"] == 2
    code = next(block for block in document["blocks"] if block["kind"] == "code")
    assert code["source"] == {"path": "parts/results.qmd", "line_start": 5, "line_end": 12}
    assert len(code["outputs"]) == 3
    client = Client(tmp_path / "repo")
    published = reports.publish(result.path, client=client)
    exported = reports.export(published["id"], tmp_path / "export", client=client)
    assert (exported / "sources/parts/pixel.svg").is_file()
    assert len(list((exported / "assets").glob("*.svg"))) == 2
    client.backend.close()


def test_failure_diagnostics(tmp_path):
    client = Client(tmp_path / "repo")
    seed(client.backend)
    source = tmp_path / "bad.qmd"
    source.write_text(
        '# Failure\n\n```{python}\nprint("before failure")\nclient.resolve("runs/missing@latest")\n```\n'
    )
    with pytest.raises(reports.ReportBuildError) as failure:
        reports.build(source, tmp_path / "failed", client=client, timeout=30)
    directory = failure.value.directory
    manifest = json.loads((directory / "manifest.json").read_text())
    assert manifest["status"] == "failed"
    assert manifest["error"]["source"]["line_start"] == 3
    assert "NotFound" in (directory / "diagnostics.txt").read_text()
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


def test_hydra_config_overrides_groups_and_multirun(tmp_path, capsys):
    from hydra import initialize
    from hydra.core.global_hydra import GlobalHydra

    from cairn.cli import main

    (tmp_path / "conf/model").mkdir(parents=True)
    (tmp_path / "conf/model/resnet.yaml").write_text("name: resnet\nlr: 0.1\n")
    (tmp_path / "conf/model/vit.yaml").write_text("name: vit\nlr: 0.01\n")
    source = tmp_path / "ablation.qmd"
    source.write_text("""---
title: Ablation
config:
  defaults:
    - model: resnet
    - _self_
  alpha: 0.1
  label: ${model.name}-${alpha}
cairn:
  config_path: conf
---

```{python}
print(cfg.label, cfg.model.lr)
```
""")
    with initialize(config_path=None, version_base=None):
        result = reports.build(source, tmp_path / "vit", overrides=["model=vit", "alpha=0.5"])
        assert GlobalHydra.instance().is_initialized()
    assert "vit-0.5 0.01" in (result.path / "index.html").read_text()
    assert result.manifest["config"] == {
        "model": {"name": "vit", "lr": 0.01},
        "alpha": 0.5,
        "label": "vit-0.5",
    }
    assert result.manifest["overrides"] == ["model=vit", "alpha=0.5"]
    assert (result.path / "sources/conf/model/vit.yaml").is_file()
    for overrides in (["beta=1"], ["model=missing"]):
        with pytest.raises(reports.ReportBuildError, match="Invalid report config"):
            reports.build(source, overrides=overrides)
    plain = tmp_path / "plain.qmd"
    plain.write_text("No config.\n")
    with pytest.raises(reports.ReportBuildError, match="require a config"):
        reports.build(plain, overrides=["alpha=1"])
    plain.write_text("---\nconfig:\n  hydra: {}\n---\n")
    with pytest.raises(reports.ReportBuildError, match="without a hydra key"):
        reports.build(plain)
    with pytest.raises(ValidationError, match="list of strings"):
        reports.build(source, overrides="alpha=1")
    repo = str(tmp_path / "repo")
    arguments = ["--repo", repo, "report", "build", str(source), "-m", "alpha=1,2"]
    assert main([*arguments, "--output", str(tmp_path / "sweep"), "--publish"]) == 0
    built = json.loads(capsys.readouterr().out)
    assert [item["directory"] for item in built] == [str(tmp_path / f"sweep/{i}") for i in (0, 1)]
    client = Client(repo)
    versions = client.backend.versions("reports/ablation@latest")
    assert [item["metadata"]["config"]["label"] for item in versions] == ["resnet-1", "resnet-2"]
    client.backend.close()


def test_report_reader_resolves_once_records_reads_and_keeps_scope(client, tmp_path):
    original = seed(client.backend)
    record = tmp_path / "inputs.jsonl"
    pins = [{"requested": "runs/baseline@latest", "artifact_id": original["id"]}]
    reader = reports.ReportInputs(client, pins, str(record))
    latest = seed(client.backend, 99)
    assert reader.resolve("runs/baseline@latest").read() == {"score": 7}
    assert reader.backend.resolve_many([original["id"]])[0]["id"] == original["id"]
    assert reader.backend.query("runs/baseline@latest")["items"][0]["data"] == {"score": 7}
    assert not record.exists()
    assert reader.use(latest["id"], role="ablation").read() == {"score": 99}
    other = seed(client.backend, 5, name="other")
    assert reader.backend.query("runs/other@latest")["items"][0]["data"] == {"score": 5}
    seed(client.backend, 6, name="other")
    assert reader.resolve("runs/other@latest").id == other["id"]
    assert [json.loads(line) for line in record.read_text().splitlines()] == [
        {"requested": latest["id"], "artifact_id": latest["id"], "role": "ablation"},
        {"requested": "runs/other@latest", "artifact_id": other["id"], "role": "input"},
    ]
    opened = client.backend.start("default", "runs", "open")
    with pytest.raises(ValidationError, match="finish the artifact"):
        reader.resolve(opened["artifact"]["id"])
    with pytest.raises(ValidationError, match="scoped to its project"):
        reader.backend.resolve(original["id"], project="other")
    assert not hasattr(reader, "start")
    assert not hasattr(reader.backend, "append_batch")
    with Client(tmp_path / "independent") as independent:
        separate = seed(independent.backend, 123)
        assert independent.resolve("runs/baseline@latest").id == separate["id"]


def test_native_v2_externalizes_all_mime_payloads_without_mutating_notebook(tmp_path):
    import base64
    import copy

    from cairn.reports import _native_output

    original = {
        "output_type": "display_data",
        "metadata": {},
        "data": {
            "image/png": base64.b64encode(b"png bytes").decode(),
            "image/svg+xml": "<svg/>",
            "video/mp4": base64.b64encode(b"video bytes").decode(),
            "audio/wav": base64.b64encode(b"audio bytes").decode(),
            "application/vnd.plotly.v1+json": {"data": [{"x": [1, 2]}]},
            "application/json": {"asset": "literal user data, not a reference"},
            "text/html": "<table><tr><td>value</td></tr></table>",
            "text/plain": "plain text",
            "text/markdown": "**markdown**",
        },
    }
    before = copy.deepcopy(original)
    projected = _native_output(original, tmp_path)
    assert original == before
    for mime, ref in projected["data"].items():
        assert ref["mime"] == mime
        payload = (tmp_path / ref["asset"]).read_bytes()
        if ref["encoding"] == "json":
            assert json.loads(payload) == original["data"][mime]
        elif ref["encoding"] == "binary":
            assert payload == base64.b64decode(original["data"][mime])
        else:
            assert payload.decode() == original["data"][mime]
    assert _native_output(original, tmp_path) == projected
    assert len(list((tmp_path / "assets").iterdir())) == len(original["data"])
    stream = _native_output(
        {"output_type": "stream", "name": "stdout", "text": "printed\n"}, tmp_path
    )
    assert (tmp_path / stream["text"]["asset"]).read_text() == "printed\n"

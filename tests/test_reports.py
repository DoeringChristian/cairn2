import json
import re
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


def test_fenced_divs_hold_cells_columns_tabs_figures_and_margins(tmp_path):
    (tmp_path / "box.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'
    )
    source = tmp_path / "layout.qmd"
    source.write_text("""::: {.columns}
::: {.column width="40%"}
Left text.
:::
::: {.column}
```{python}
print("right cell")
```
:::
:::

::: {.panel-tabset}
## Alpha
```{python}
print("alpha output")
```

## Beta
Second tab.
:::

::: {#fig-pair layout="[[2,1]]"}
![Left box](box.svg){#fig-left}

![Right box](box.svg)

Two boxes.
:::

See @fig-pair and @fig-left.

::: {.callout-tip title="Tip"}
```{python}
print("in callout")
```
:::

```{python}
#| layout-ncol: 2
#| fig-subcap: [first, second]
#| column: margin
from IPython.display import SVG, display
print("text first")
for _ in range(2): display(SVG(open("box.svg").read()))
```
""")
    result = reports.build(source, tmp_path / "build", timeout=30)
    page = re.sub(
        r' data-source-path="[^"]*" data-line-start="\d+" data-line-end="\d+"',
        "",
        (result.path / "index.html").read_text(),
    )
    assert re.search(r'<div class="column" style="flex:0 0 40%"><section [^>]*><p>Left text', page)
    assert re.search(
        r'<div class="column"><section [^>]*><pre class="code">.*right cell', page, re.DOTALL
    )
    assert re.search(
        r'<div class="tab"><p class="tab-title">Alpha</p><section .*alpha output', page, re.DOTALL
    )
    assert '<p class="tab-title">Beta</p>' in page and "## Beta" not in page
    assert 'style="width:66.6667%"' in page and 'style="width:33.3333%"' in page
    assert "<figcaption>(a) Left box</figcaption>" in page
    assert "<figcaption>(b) Right box</figcaption>" in page
    assert re.search(r'<figcaption class="source-block" id="block-\d+">Figure 1: Two boxes', page)
    assert 'href="#fig-left">Figure 1(a)</a>' in page
    assert re.search(
        r'<aside class="callout tip"><p class="callout-title">Tip</p><section .*in callout',
        page,
        re.DOTALL,
    )
    assert '<section class="source-block column-margin"' in page
    assert "<figcaption>(a) first</figcaption>" in page and "(b) second" in page
    document = json.loads((result.path / "document.json").read_text())
    kinds = [block["kind"] for block in document["blocks"]]
    assert kinds.count("open") == kinds.count("close") == 8
    figure = next(b for b in document["blocks"] if b.get("attributes", {}).get("id") == "fig-pair")
    assert figure["grid"] == [[66.6667, 33.3333]]
    assert any(block.get("caption") for block in document["blocks"])
    with pytest.raises(reports.ReportBuildError, match="column must be one of"):
        source.write_text("```{python}\n#| column: side\n```\n")
        reports.build(source)


def test_inline_python_sees_earlier_cells_and_reports_failures(tmp_path):
    source = tmp_path / "inline.qmd"
    source.write_text("""Before: `{python} 1 + 1`.

```{python}
loss = 0.123456
```

The loss is `{python} f"{loss:.3f}"` <b>, literal `x`.

## Twice `{python} loss * 2`
""")
    result = reports.build(source, tmp_path / "build")
    page = (result.path / "index.html").read_text()
    assert "Before: 2." in page and "The loss is 0.123 &lt;b&gt;, literal <code>x</code>." in page
    assert "<h2>Twice 0.246912</h2>" in page
    document = json.loads((result.path / "document.json").read_text())
    texts = [
        c["content"] for b in document["blocks"] for n in b.get("nodes", []) for c in n["children"]
    ]
    assert "0.123" in texts and not any("{python}" in text for text in texts)
    source.write_text("Fails: `{python} missing_name`\n")
    with pytest.raises(reports.ReportBuildError, match="NameError") as failure:
        reports.build(source, tmp_path / "failed")
    assert failure.value.details["source"]["line_start"] == 1


def test_cross_document_references_and_embeds_record_the_other_report(tmp_path):
    client = Client(tmp_path / "repo")
    (tmp_path / "box.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" width="2" height="2"/>'
    )
    (tmp_path / "baseline.qmd").write_text("""---
title: Baseline study
---
![A box](box.svg){#fig-box}

```{python}
#| label: fig-loss
#| fig-cap: Loss curve
print("loss output")
```
""")
    baseline = reports.publish(
        reports.build(tmp_path / "baseline.qmd", client=client).path, client=client, name="baseline"
    )
    source = tmp_path / "follow.qmd"
    source.write_text("""See @reports/baseline#fig-loss and @reports/baseline@v1#fig-box.

{{< embed reports/baseline#fig-loss >}}
""")
    result = reports.build(source, tmp_path / "build", client=client)
    page = (result.path / "index.html").read_text()
    link = f'href="/p/default/a/{baseline["id"]}#fig-loss">Figure 2 in <em>Baseline study</em></a>'
    assert link in page and "Figure 1 in <em>Baseline study</em>" in page
    assert "loss output" in page and "Figure 2 in Baseline study: Loss curve" in page
    assert "print(" not in page
    assert [(item["requested"], item["role"]) for item in result.manifest["inputs"]] == [
        ("reports/baseline", "reference"),
        ("reports/baseline@v1", "reference"),
    ]
    assert result.manifest["dependencies"] == [{"artifact_id": baseline["id"], "role": "reference"}]
    document = json.loads((result.path / "document.json").read_text())
    embedded = next(block for block in document["blocks"] if "embed" in block)
    assert embedded["kind"] == "code" and embedded["caption_prefix"].startswith("Figure 2 in")
    for text, error in (
        ("See @reports/baseline#fig-missing.", "Unknown cross reference"),
        ("{{< embed reports/baseline#fig-box >}}", "not a Python cell"),
        ("See @reports/missing#fig-box.", "Unknown artifact name"),
    ):
        source.write_text(text + "\n")
        with pytest.raises((reports.ReportBuildError, ValidationError), match=error):
            reports.build(source, client=client)
    client.backend.close()


def test_citations_from_bibtex_in_author_year_and_numeric_styles(tmp_path):
    (
        tmp_path / "refs.bib"
    ).write_text(r"""@article{smith2020, author={Smith, John and Doe, Alice and M{\"u}ller, Kai},
  title={Fast {GPU} rendering}, journal={Graphics J.}, year={2020}, volume={12}, number={3},
  pages={45--67}, doi={10.1/x}}
@book{knuth, author={Donald E. Knuth}, title={The Art}, publisher={Addison-Wesley}, year=1968}
@misc{unused, title={Never cited}, year={2001}}
""")
    source = tmp_path / "cited.qmd"
    body = """## Intro {#sec-intro}

Fast [@smith2020, p. 3]. @knuth agrees [see @knuth; -@smith2020]. Kept: @nobody, a@b.com, @sec-intro.

::: {#refs}
:::

After the references.
"""
    source.write_text("---\nbibliography: refs.bib\n---\n" + body)
    page = (reports.build(source, tmp_path / "author-year").path / "index.html").read_text()
    cite = '<a class="citation" href="#ref-{}">{}</a>'
    assert f"Fast ({cite.format('smith2020', 'Smith et al. 2020')}, p. 3)." in page
    assert f"Knuth ({cite.format('knuth', '1968')}) agrees" in page
    assert f"(see {cite.format('knuth', 'Knuth 1968')}; {cite.format('smith2020', '2020')})" in page
    assert 'Kept: @nobody, a@b.com, <a class="crossref" href="#sec-intro">Section 1</a>.' in page
    knuth = page.index('id="ref-knuth"')
    assert knuth < page.index('id="ref-smith2020"') < page.index("After the references")
    assert (
        "Smith, J., Doe, A., &amp; Müller, K. (2020). Fast GPU rendering. <em>Graphics J.</em>, 12(3), 45–67."
        in page
    )
    assert '<a href="https://doi.org/10.1/x">' in page and "Never cited" not in page
    assert "<h2>References</h2>" not in page
    source.write_text(
        "---\nbibliography: refs.bib\ncitation-style: numeric\n---\n"
        + body.replace("::: {#refs}\n:::\n", "")
    )
    page = (reports.build(source, tmp_path / "numeric").path / "index.html").read_text()
    assert (
        f"Fast [{cite.format('smith2020', '1')}, p. 3]. Knuth [{cite.format('knuth', '2')}]" in page
    )
    assert '<h2>References</h2><p id="ref-smith2020" class="reference">[1] Smith' in page
    source.write_text("---\nbibliography: refs.bib\ncitation-style: chicago\n---\n")
    with pytest.raises(reports.ReportBuildError, match="citation-style"):
        reports.build(source)


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
    assert document["version"] == 3
    assert result.manifest["document_version"] == 3
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

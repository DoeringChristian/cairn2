# Large datasets

Use the ordinary artifact API for datasets, including directories containing
hundreds of gigabytes of images. Cairn automatically selects a paged format for
directories exceeding 32 MiB or 128 files and chunks path-backed `File` values
larger than 32 MiB. Smaller captures keep their existing format.

```python
from cairn import Client, Directory

with Client("http://hub:8765", project="fabric") as client:
    dataset = client.start("datasets", "fabric-hex-region")
    dataset.finish(value=Directory("/data/fabric-hex-region"))
    print(dataset.id)  # Pin this ID in downstream runs.
```

`artifact.add(path, name="images")` uses the same uploader. There is no need to
call the backend directly, split the directory by hand, or increase the outbox
limit. Uploads require a reachable repository and block while transferring data;
the source must remain unchanged until the call returns.

## Uploads and interrupted jobs

Files in paged directories use blobs of at most 8 MiB. Small images stay as one
blob; larger files have an index of content-addressed chunks. Index pages hold
at most 128 entries, with an additional metadata size bound. Large directory
trees have several index levels instead of one enormous artifact manifest.

The uploader transfers one chunk at a time and acknowledges its retaining
record before proceeding. Existing content is deduplicated by SHA-256. It does
not create a second local copy of the dataset or consume dataset-sized space in
the run's outbox. Directory traversal sorts each individual directory, so very
large flat directories still require memory proportional to that directory's
number of names, but not its file contents.

Progress is checkpointed under `<state>/uploads/`, where `<state>` is
`CAIRN_STATE_DIR`, otherwise `$XDG_STATE_HOME/cairn` or `~/.local/state/cairn`.
This contains metadata, not dataset bytes. Keep this state directory and the
unchanged source to resume an interrupted upload by repeating the same add or
finish operation against the same repository and project. A restarted process
may use a new owning artifact: the dataset checkpoint is independent of that
artifact. Completed chunks are not uploaded again; an interrupted large file
may still be reread locally to compute its complete hash.

`Client.recover()` replays already queued owner records and finish requests. It
does not independently resume a directory traversal that failed before its
reference was queued: rerun the source operation for that case. Network errors
propagate to the caller; a job runner can retry the operation.

Index parts use ordinary artifacts in the internal `cairn_parts` namespace.
They are hidden from the browser's artifact lists, but remain visible to
low-level repository listings. Their retained dependency graph protects bytes
during uploads and after publication. Successfully attached parts are soft
deleted and can be collected once their owning datasets are deleted. An
abandoned partial upload retains its parts until explicitly deleted; there is
currently no automatic expiry of resumable uploads. Removing a local checkpoint
alone does not release those server-side parts.

## Reading on another machine

Only repository access is needed; the original machine and its upload checkpoint
are not involved.

```python
from cairn import Client

with Client("http://hub:8765", project="fabric") as client:
    dataset = client.get("datasets/fabric-hex-region@v1")

    # Only the index pages and blobs needed for this file are fetched.
    image_bytes = dataset.file("frames/000042.png").load()

    # A seekable binary stream fetches chunks as needed.
    with dataset.file("volume.bin").open() as stream:
        stream.seek(1024 * 1024 * 1024)
        block = stream.read(4096)

    # Enumerate file names without loading a single giant manifest.
    for name in dataset.value.iter_files():
        print(name)

    # Materialize everything, or pass one relative file name.
    dataset.download(to="/scratch/fabric")
    dataset.download("frames/000042.png", to="/scratch/selected")
```

For an `images` capture instead of a final directory value, use
`dataset.file("images/frames/000042.png")`,
`dataset.file("images").iter_files()`, and `dataset.download("images", to=...)`.
`.load()` deliberately returns the whole selected file in memory; use `.open()`
for bounded reads of large files.

Fetched chunks are hash-verified and cached in `<cache>/dataset-chunks`, with a
default budget of 256 MiB. Set `CAIRN_DATASET_CACHE_BYTES` to change it. The cache
keeps at least the current chunk; simultaneous readers can temporarily exceed
the budget while fetching. Cache eviction never deletes upload checkpoints or
materialized dataset files. `<cache>` follows `CAIRN_CACHE_DIR`, then
`$XDG_CACHE_HOME/cairn`, then `~/.cache/cairn`.

Downloads assemble a hidden `.partial` file alongside the destination. Repeating
the download verifies existing chunks and fetches missing or damaged content.
Only after verifying the complete size and SHA-256 is the file atomically moved
into place and made read-only. Completed files are reused after verification.
The output disk still needs space for the requested data; the bounded cache
does not replace that space. A full dataset download publishes files
individually, not as one atomic directory transaction.

## Compatibility and scope

Both ends need the `paged_datasets` capability. Older clients cannot read the
new file/directory codec version 2; older servers are rejected before a paged
upload begins. No repository schema migration is needed, and existing version 1
artifacts remain readable. Repository export/import preserves the dependency
graph and its bytes.

Automatic chunking applies to path-backed `File` and `Directory` values. It does
not yet chunk arbitrary in-memory Python values, generated array/table payloads,
or file-like input streams. `link=True` keeps the existing shared-path, no-copy
format and its manifest limits. For a very large collection of linked files,
use a normal paged directory upload instead. Symlinks in uploaded directory
sources are rejected, as before.

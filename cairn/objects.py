"""Immutable byte adapters. Repository transactions own visibility and retention."""

import os
import re
import tempfile
from contextlib import closing
from pathlib import Path

from .model import IntegrityError, NotFound, safe_path


def sync_directory(path):
    fd = os.open(path, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)


def ensure_directory(path):
    """Persist new directory entries as well as the files subsequently placed there."""
    path = Path(path)
    missing, current = [], path
    while not current.exists():
        missing.append(current)
        current = current.parent
    path.mkdir(parents=True, exist_ok=True)
    for created in reversed(missing):
        sync_directory(created.parent)


class Files:
    def __init__(self, path):
        self.path = Path(path).expanduser().resolve()
        ensure_directory(self.path)

    def _path(self, key):
        path = self.path / safe_path(key)
        if not path.resolve().is_relative_to(self.path):
            raise IntegrityError("Object path escapes storage")
        return path

    def put(self, key, source, *, digest, size):
        path = self._path(key)
        ensure_directory(path.parent)
        fd, temporary = tempfile.mkstemp(prefix=f".upload-{path.name}-", dir=path.parent)
        try:
            with os.fdopen(fd, "wb") as target:
                while chunk := source.read(1024 * 1024):
                    target.write(chunk)
                target.flush()
                os.fsync(target.fileno())
            if os.path.getsize(temporary) != size:
                raise IntegrityError("Object upload length changed")
            os.replace(temporary, path)
            sync_directory(path.parent)
        finally:
            Path(temporary).unlink(missing_ok=True)

    def open(self, key):
        try:
            return self._path(key).open("rb")
        except FileNotFoundError as exc:
            raise NotFound("Object bytes are missing") from exc

    def delete(self, key):
        path = self._path(key)
        path.unlink(missing_ok=True)
        if path.parent.exists():
            sync_directory(path.parent)

    def inventory(self):
        for path in self.path.rglob("*"):
            if path.is_file():
                stat = path.stat()
                entry = {
                    "key": path.relative_to(self.path).as_posix(),
                    "size": stat.st_size,
                    "modified": stat.st_mtime,
                }
                staging = re.match(r"^\.upload-(o_[0-9a-f]{32})-", path.name)
                if staging:
                    entry["upload_key"] = (
                        (path.parent / staging[1]).relative_to(self.path).as_posix()
                    )
                yield entry


class S3:
    def __init__(self, bucket, *, prefix="cairn/", endpoint_url=None, region=None):
        try:
            import boto3
        except ImportError as exc:
            raise RuntimeError("Install cairn2[s3] to use S3 storage") from exc
        self.bucket = bucket
        self.prefix = (safe_path(prefix.strip("/")) + "/") if prefix.strip("/") else ""
        self.client = boto3.client("s3", endpoint_url=endpoint_url, region_name=region)

    def _key(self, key):
        return self.prefix + safe_path(key)

    def put(self, key, source, *, digest, size):
        self.client.upload_fileobj(
            source, self.bucket, self._key(key), ExtraArgs={"Metadata": {"sha256": digest}}
        )
        result = self.client.head_object(Bucket=self.bucket, Key=self._key(key))
        if result["ContentLength"] != size or result["Metadata"].get("sha256") != digest:
            raise IntegrityError("S3 object verification failed")

    def open(self, key):
        try:
            response = self.client.get_object(Bucket=self.bucket, Key=self._key(key))
        except self.client.exceptions.NoSuchKey as exc:
            raise NotFound("Object bytes are missing") from exc
        return closing(response["Body"])

    def delete(self, key):
        self.client.delete_object(Bucket=self.bucket, Key=self._key(key))

    def inventory(self):
        for page in self.client.get_paginator("list_objects_v2").paginate(
            Bucket=self.bucket, Prefix=self.prefix
        ):
            for entry in page.get("Contents", []):
                yield {
                    "key": entry["Key"][len(self.prefix) :],
                    "size": entry["Size"],
                    "modified": entry["LastModified"].timestamp(),
                }

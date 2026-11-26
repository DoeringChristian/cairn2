"""The operations of a repository on a server."""

import logging

import httpx

from . import cairn_pb2 as pb
from .errors import CODES, CairnError, Conflict, Pending
from .operations import ANSWERS

PIECE = 64 * 1024 * 1024
SERVERS = set()


def foreign(record):
    """Where a program shows what is logged, its own requests are, and not those to Cairn."""
    message = record.getMessage()
    return not any(f" {server}" in message for server in tuple(SERVERS))


logging.getLogger("httpx").addFilter(foreign)


class Remote:
    def __init__(self, url, token=None, timeout=60):
        self.url = url.rstrip("/")
        self._options = {"base_url": self.url, "headers": {"Authorization": f"Bearer {token}"} if token else {}, "timeout": timeout}
        self._connection = httpx.Client(**self._options)
        SERVERS.add(str(self._connection.base_url))

    @property
    def _http(self):
        """A run is still read after its `with` has closed the connection."""
        if self._connection.is_closed:
            self._connection = httpx.Client(**self._options)
        return self._connection

    def __getattr__(self, name):
        if name not in ANSWERS:
            raise AttributeError(name)

        def call(message):
            response = self._send("POST", f"/v1/{name}", content=message.SerializeToString())
            return ANSWERS[name].FromString(response.content)

        setattr(self, name, call)
        return call

    def _send(self, method, path, **options):
        response = self._http.request(method, path, **options)
        if response.is_error:
            raise self._error(response.status_code, response.content)
        return response

    @staticmethod
    def _error(status, content):
        try:
            error = pb.Error.FromString(content)
            if error.code in CODES:
                return CODES[error.code](error.message, **error.details)
        except Exception:  # noqa: BLE001, S110 - not from Cairn, e.g. a proxy
            pass
        return (Pending if status >= 500 else CairnError)(f"The server answered {status}")

    def put(self, digest, stream, size=0, offset=0):
        """Store content; large content goes in pieces, from an offset the server has reached."""

        def chunks(end):
            while chunk := stream.read(min(1 << 20, end - stream.tell())):
                yield chunk

        path = f"/v1/content/{digest.hex()}"
        if size <= PIECE:
            self._send("PUT", path, content=chunks(1 << 62))
            return
        while True:
            stream.seek(offset)
            end = min(offset + PIECE, size)
            try:
                # The server may have to read again what it has already.
                self._send("PUT", path, content=chunks(end), params={"offset": offset, "size": size}, timeout=httpx.Timeout(60, read=3600))
                offset = end
            except Conflict as error:
                # Another program sends the same content.
                raise Pending(str(error)) from error
            if offset >= size:
                return

    def stream(self, digest, start=0, end=None):
        headers = {"Range": f"bytes={start}-{'' if end is None else end - 1}"} if start or end is not None else {}
        with self._http.stream("GET", f"/v1/content/{digest.hex()}", headers=headers) as response:
            if response.is_error:
                raise self._error(response.status_code, response.read())
            yield from response.iter_bytes(1 << 20)

    def close(self):
        self._connection.close()

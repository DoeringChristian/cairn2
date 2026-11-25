"""Width and height of images and videos, from the bytes at their start."""

import re
import struct


def _png(data):
    return struct.unpack(">II", data[16:24]) if data.startswith(b"\x89PNG\r\n\x1a\n") else None


def _gif(data):
    return struct.unpack("<HH", data[6:10]) if data[:4] == b"GIF8" else None


def _jpeg(data):
    at = 2
    while data[:2] == b"\xff\xd8" and at + 9 < len(data) and data[at] == 0xFF:
        kind, length = data[at + 1], int.from_bytes(data[at + 2 : at + 4], "big")
        if 0xC0 <= kind <= 0xCF and kind not in (0xC4, 0xC8, 0xCC):
            height, width = struct.unpack(">HH", data[at + 5 : at + 9])
            return width, height
        at += 2 + length
    return None


def _webp(data):
    if data[:4] != b"RIFF" or data[8:12] != b"WEBP":
        return None
    if data[12:16] == b"VP8X":
        return 1 + int.from_bytes(data[24:27], "little"), 1 + int.from_bytes(data[27:30], "little")
    if data[12:16] == b"VP8L":
        bits = int.from_bytes(data[21:25], "little")
        return 1 + (bits & 0x3FFF), 1 + (bits >> 14 & 0x3FFF)
    width, height = struct.unpack("<HH", data[26:30])
    return width & 0x3FFF, height & 0x3FFF


def _svg(data):
    match = re.search(rb"<svg\b[^>]*>", data)
    if not match:
        return None
    sides = [re.search(rb"\b%s\s*=\s*[\"']([\d.]+)(px|pt)?[\"']" % side, match[0]) for side in (b"width", b"height")]
    if all(sides):
        # A point is 4/3 of a pixel.
        return tuple(round(float(side[1]) * (4 / 3 if side[2] == b"pt" else 1)) for side in sides)
    box = re.search(rb"viewBox\s*=\s*[\"']\s*[\d.eE+-]+[\s,]+[\d.eE+-]+[\s,]+([\d.]+)[\s,]+([\d.]+)", match[0])
    return (round(float(box[1])), round(float(box[2]))) if box else None


def _boxes(read, at, end):
    """The boxes of an MP4 between two positions, as (name, from, to)."""
    while at + 8 <= end:
        head = read(at, 16)
        if len(head) < 8:
            return
        size, name, start = int.from_bytes(head[:4], "big"), head[4:8], at + 8
        if size == 1:
            size, start = int.from_bytes(head[8:16], "big"), at + 16
        size = size or end - at
        if size < 8:
            return
        yield name, start, at + size
        at += size


def _mp4(read, size):
    for name, start, end in _boxes(read, 0, size):
        if name != b"moov":
            continue
        for inner, first, last in _boxes(read, start, end):
            if inner != b"trak":
                continue
            for box, begin, stop in _boxes(read, first, last):
                if box == b"tkhd" and stop - begin >= 84:
                    tail = read(stop - 8, 8)
                    width, height = (value >> 16 for value in struct.unpack(">II", tail))
                    matrix = struct.unpack(">ii", read(stop - 44, 8))
                    if width and height:
                        return (width, height) if matrix[0] else (height, width)  # turned by 90 degrees
    return None


def size_of(read, size, media):
    """`read(position, count)` gives bytes; None when the size is not known."""
    try:
        if media.startswith("video/"):
            return _mp4(read, size) if read(4, 4) == b"ftyp" else None
        if media.startswith("image/"):
            data = read(0, 256 * 1024)
            return next((found for known in (_png, _jpeg, _gif, _webp, _svg) if (found := known(data))), None)
    except (struct.error, IndexError, ValueError):
        pass
    return None

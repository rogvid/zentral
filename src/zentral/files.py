"""What a file really is, and making it safe to keep.

Nothing here trusts the file name or a declared content type: the type comes
from the first bytes, against a short allow-list of formats that browsers do
not execute. Images are stripped of metadata before they are stored, because
photos carry location (EXIF GPS), device serial numbers and editing history
that nobody meant to send to a service desk.

Pure functions, no I/O; exhaustively tested.
"""

from __future__ import annotations

import re
import struct
import unicodedata
from dataclasses import dataclass


class FileRejected(ValueError):
    """The file is not of an allowed type, or is malformed."""


@dataclass(frozen=True, slots=True)
class Kind:
    content_type: str
    extension: str


PNG = Kind("image/png", "png")
JPEG = Kind("image/jpeg", "jpg")
GIF = Kind("image/gif", "gif")
PDF = Kind("application/pdf", "pdf")
TEXT = Kind("text/plain", "txt")

IMAGES = frozenset({PNG.content_type, JPEG.content_type, GIF.content_type})
_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n"
# Text kept by name for the people reading it; everything that can hide data goes.
_PNG_DROP = {b"tEXt", b"zTXt", b"iTXt", b"eXIf", b"tIME"}
# JPEG: APP1 (EXIF, XMP), APP13 (Photoshop/IPTC) and comments. APP0 (JFIF),
# APP2 (colour profile) and APP14 (Adobe colour transform) affect rendering.
_JPEG_DROP = {0xE1, 0xED, 0xFE}


def sniff(data: bytes) -> Kind:
    """The file's real type, from its content. Raises for anything not allowed."""
    if data.startswith(_PNG_SIGNATURE):
        return PNG
    if data.startswith(b"\xff\xd8\xff"):
        return JPEG
    if data.startswith((b"GIF87a", b"GIF89a")):
        return GIF
    if data.startswith(b"%PDF-"):
        return PDF
    if _is_text(data):
        return TEXT
    raise FileRejected("this type of file is not accepted (images, PDF and plain text are)")


def _is_text(data: bytes) -> bool:
    if b"\x00" in data:
        return False
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        return False
    head = text.lstrip()[:512].lower()
    # Markup that a browser would render (and run) is not "plain text".
    return not head.startswith(("<!doctype", "<html", "<svg", "<?xml", "<script"))


def sanitize(kind: Kind, data: bytes) -> bytes:
    """Remove metadata from images; other kinds are returned unchanged."""
    if kind == PNG:
        return _strip_png(data)
    if kind == JPEG:
        return _strip_jpeg(data)
    return data


def _strip_png(data: bytes) -> bytes:
    out = bytearray(_PNG_SIGNATURE)
    pos = len(_PNG_SIGNATURE)
    while pos < len(data):
        if pos + 12 > len(data):
            raise FileRejected("the image is damaged")
        (length,) = struct.unpack(">I", data[pos : pos + 4])
        chunk_type = data[pos + 4 : pos + 8]
        end = pos + 12 + length
        if end > len(data):
            raise FileRejected("the image is damaged")
        if chunk_type not in _PNG_DROP:
            out += data[pos:end]
        pos = end
        if chunk_type == b"IEND":
            break  # anything after the image end is not part of the image
    return bytes(out)


def _strip_jpeg(data: bytes) -> bytes:
    out = bytearray(b"\xff\xd8")
    pos = 2
    while pos < len(data):
        if data[pos] != 0xFF:
            raise FileRejected("the image is damaged")
        marker = data[pos + 1] if pos + 1 < len(data) else -1
        if marker == 0xFF:  # fill byte
            pos += 1
            continue
        if marker == 0xDA:  # start of scan: the image data runs to the end
            out += data[pos:]
            return bytes(out)
        if marker in (0xD8, 0x01) or 0xD0 <= marker <= 0xD7:  # markers without a length
            out += data[pos : pos + 2]
            pos += 2
            continue
        if marker == 0xD9:  # end of image
            out += data[pos : pos + 2]
            return bytes(out)
        if pos + 4 > len(data):
            raise FileRejected("the image is damaged")
        (length,) = struct.unpack(">H", data[pos + 2 : pos + 4])
        end = pos + 2 + length
        if length < 2 or end > len(data):
            raise FileRejected("the image is damaged")
        if marker not in _JPEG_DROP:
            out += data[pos:end]
        pos = end
    raise FileRejected("the image is damaged")


_UNSAFE = re.compile(r"[^\w .()+-]")


def safe_filename(name: str, kind: Kind) -> str:
    """A display name that is safe in headers and file systems, with the right extension."""
    name = unicodedata.normalize("NFKC", name or "")
    name = name.replace("\\", "/").rsplit("/", 1)[-1]  # never a path
    name = "".join(ch for ch in name if unicodedata.category(ch)[0] != "C")
    name = _UNSAFE.sub("_", name).strip(" ._") or "attachment"
    stem = name.rsplit(".", 1)[0] if "." in name else name
    return f"{stem[:100].strip(' ._') or 'attachment'}.{kind.extension}"

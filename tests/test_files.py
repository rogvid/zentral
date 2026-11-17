"""File type detection, metadata stripping and safe names."""

from __future__ import annotations

import struct
import zlib

import pytest

from zentral.files import (
    GIF,
    JPEG,
    PDF,
    PNG,
    TEXT,
    FileRejected,
    safe_filename,
    sanitize,
    sniff,
)


def png_chunk(kind: bytes, data: bytes) -> bytes:
    return struct.pack(">I", len(data)) + kind + data + struct.pack(">I", zlib.crc32(kind + data))


def make_png(*extra: bytes, trailer: bytes = b"") -> bytes:
    header = png_chunk(b"IHDR", struct.pack(">IIBBBBB", 1, 1, 8, 2, 0, 0, 0))
    pixels = png_chunk(b"IDAT", zlib.compress(b"\x00\xff\x00\x00"))
    return (
        b"\x89PNG\r\n\x1a\n" + header + b"".join(extra) + pixels + png_chunk(b"IEND", b"") + trailer
    )


def jpeg_segment(marker: int, data: bytes) -> bytes:
    return bytes([0xFF, marker]) + struct.pack(">H", len(data) + 2) + data


def make_jpeg(*extra: bytes) -> bytes:
    return (
        b"\xff\xd8"
        + jpeg_segment(0xE0, b"JFIF\x00\x01\x01\x00\x00\x01\x00\x01\x00\x00")
        + b"".join(extra)
        + jpeg_segment(0xDB, b"\x00" + bytes(64))
        + b"\xff\xda"
        + struct.pack(">H", 8)
        + b"\x01\x01\x00\x00\x3f\x00"
        + b"\x12\x34\x56"
        + b"\xff\xd9"
    )


@pytest.mark.parametrize(
    ("data", "kind"),
    [
        (make_png(), PNG),
        (make_jpeg(), JPEG),
        (b"GIF89a" + bytes(20), GIF),
        (b"%PDF-1.7\n...", PDF),
        ("Traceback (most recent call last):\n  ærøå\n".encode(), TEXT),
    ],
)
def test_allowed_types_are_recognised_by_content(data, kind):
    assert sniff(data) == kind


@pytest.mark.parametrize(
    "data",
    [
        b'<svg xmlns="http://www.w3.org/2000/svg"><script>alert(1)</script></svg>',
        b"  <!DOCTYPE html><html><script>x</script>",
        b"<?xml version='1.0'?><svg/>",
        b"MZ\x90\x00" + bytes(100),  # Windows executable
        b"PK\x03\x04" + bytes(100),  # zip, docx, xlsx
        b"\x7fELF" + bytes(100),
        b"text with a \x00 byte",
        b"\xff\xfe\xfa invalid utf-8",
    ],
)
def test_everything_else_is_refused(data):
    with pytest.raises(FileRejected):
        sniff(data)


def test_png_metadata_is_stripped_and_the_image_kept():
    secret = png_chunk(b"tEXt", b"Author\x00Jane Doe, 55 Main St")
    exif = png_chunk(b"eXIf", b"MM\x00*GPS 55.67N 12.56E")
    stamp = png_chunk(b"tIME", bytes(7))
    original = make_png(secret, exif, stamp, trailer=b"hidden data after the end")
    cleaned = sanitize(PNG, original)
    assert cleaned == make_png()
    assert b"Jane" not in cleaned and b"GPS" not in cleaned and b"hidden" not in cleaned


def test_jpeg_metadata_is_stripped_and_the_image_kept():
    exif = jpeg_segment(0xE1, b"Exif\x00\x00GPSLatitude 55.67")
    xmp = jpeg_segment(0xE1, b"http://ns.adobe.com/xap/1.0/\x00<creator>Jane</creator>")
    iptc = jpeg_segment(0xED, b"Photoshop 3.0\x00Jane")
    comment = jpeg_segment(0xFE, b"taken at home")
    profile = jpeg_segment(0xE2, b"ICC_PROFILE\x00colours")
    cleaned = sanitize(JPEG, make_jpeg(exif, xmp, iptc, comment, profile))
    assert cleaned == make_jpeg(profile)  # colour profile kept: it changes how the image looks
    for leak in (b"GPS", b"Jane", b"home"):
        assert leak not in cleaned


@pytest.mark.parametrize(
    ("kind", "data"),
    [
        (PNG, b"\x89PNG\r\n\x1a\n\x00\x00\x00\xffIHDR"),  # chunk longer than the file
        (JPEG, b"\xff\xd8\xff\xe1\x00\xffshort"),
        (JPEG, b"\xff\xd8garbage"),
    ],
)
def test_damaged_images_are_refused(kind, data):
    with pytest.raises(FileRejected):
        sanitize(kind, data)


def test_other_kinds_pass_unchanged():
    assert sanitize(PDF, b"%PDF-1.7 x") == b"%PDF-1.7 x"
    assert sanitize(TEXT, b"log line") == b"log line"


@pytest.mark.parametrize(
    ("name", "kind", "expected"),
    [
        ("screenshot.png", PNG, "screenshot.png"),
        ("../../etc/passwd", TEXT, "passwd.txt"),
        ("C:\\Users\\jane\\report.pdf", PDF, "report.pdf"),
        ("invoice.pdf.exe", PDF, "invoice.pdf.pdf"),
        ("photo.JPG", JPEG, "photo.jpg"),
        ("evil\u202egnp.exe", PNG, "evilgnp.png"),  # right-to-left override removed
        ('quote"; filename=x.html', TEXT, "quote__ filename_x.txt"),
        ("", PNG, "attachment.png"),
        ("...", GIF, "attachment.gif"),
        ("Skærmbillede 2026.png", PNG, "Skærmbillede 2026.png"),
    ],
)
def test_safe_filenames(name, kind, expected):
    assert safe_filename(name, kind) == expected


def test_long_names_are_shortened():
    assert len(safe_filename("a" * 500 + ".png", PNG)) == 104

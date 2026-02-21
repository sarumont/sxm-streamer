"""Pure functions for building ID3v2.3 binary tags."""

import struct
from typing import Optional


def encode_syncsafe(n: int) -> bytes:
    """Encode an integer as a 4-byte syncsafe integer (7 bits per byte)."""
    if n < 0:
        raise ValueError("Syncsafe integers must be non-negative")
    return bytes([
        (n >> 21) & 0x7F,
        (n >> 14) & 0x7F,
        (n >> 7) & 0x7F,
        n & 0x7F,
    ])


def _build_text_frame(frame_id: str, text: str) -> bytes:
    """Build a TIT2 or TPE1 text frame (ISO-8859-1 encoding)."""
    text_bytes = text.encode("latin-1", errors="replace")
    # encoding byte (0x00 = ISO-8859-1) + text
    payload = b"\x00" + text_bytes
    return (
        frame_id.encode("ascii")
        + struct.pack(">I", len(payload))
        + b"\x00\x00"  # flags
        + payload
    )


def _build_apic_frame(image_data: bytes, mime_type: str) -> bytes:
    """Build an APIC (attached picture) frame for front cover."""
    # encoding (0x00) + mime + null + picture type (0x03=front cover) + description null + data
    payload = (
        b"\x00"
        + mime_type.encode("ascii") + b"\x00"
        + b"\x03"  # front cover
        + b"\x00"  # empty description
        + image_data
    )
    return (
        b"APIC"
        + struct.pack(">I", len(payload))
        + b"\x00\x00"  # flags
        + payload
    )


def detect_image_mime(data: bytes) -> str:
    """Detect image MIME type from magic bytes."""
    if data[:8] == b"\x89PNG\r\n\x1a\n":
        return "image/png"
    if data[:2] == b"\xff\xd8":
        return "image/jpeg"
    return "image/jpeg"  # default assumption


def build_id3v2_tag(
    title: str = "",
    artist: str = "",
    image_data: Optional[bytes] = None,
    image_mime: str = "",
) -> bytes:
    """Build a complete ID3v2.3 tag with optional text and picture frames."""
    frames = b""
    if title:
        frames += _build_text_frame("TIT2", title)
    if artist:
        frames += _build_text_frame("TPE1", artist)
    if image_data:
        mime = image_mime or detect_image_mime(image_data)
        frames += _build_apic_frame(image_data, mime)

    if not frames:
        return b""

    header = b"ID3" + b"\x03\x00" + b"\x00" + encode_syncsafe(len(frames))
    return header + frames

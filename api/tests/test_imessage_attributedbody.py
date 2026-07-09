"""Tests for _decode_attributed_body (→1648).

macOS chat.db stores recent message bodies in the attributedBody BLOB column
using StreamTypedCoder encoding. The text column is NULL for these messages.
"""
import pytest
from services.imessage import _decode_attributed_body


def _make_blob(text: str, use_0x81: bool = False) -> bytes:
    """Build a minimal StreamTypedCoder blob wrapping a plain string.

    Constructs the NSString section only — enough for the decoder to work.
    Uses the NSSerializer variable-length integer encoding for the length field:
      0x00–0x7F : direct (lengths 0–127)
      0x81 HH LL: 16-bit big-endian (lengths 128–65535)
      0x82 ...  : 32-bit big-endian (lengths > 65535)
    """
    encoded = text.encode("utf-8")
    n = len(encoded)
    if n > 65535:
        length_field = bytes([0x82, (n >> 24) & 0xFF, (n >> 16) & 0xFF, (n >> 8) & 0xFF, n & 0xFF])
    elif use_0x81 or n >= 128:
        length_field = bytes([0x81, (n >> 8) & 0xFF, n & 0xFF])
    else:
        length_field = bytes([n])
    # 5 metadata bytes (class type tag + version + value header), then length, then text
    return b"NSString\x01\x95\x84\x01\x2b" + length_field + encoded


# -------------------------------------------------------------------
# Unit tests for the decoder
# -------------------------------------------------------------------

def test_short_message_simple_length():
    blob = _make_blob("hello world")
    result = _decode_attributed_body(blob)
    assert result == "hello world"


def test_empty_bytes_returns_none():
    assert _decode_attributed_body(b"") is None


def test_none_input_returns_none():
    assert _decode_attributed_body(None) is None


def test_long_message_0x81_length_path():
    """A 200-char string forces the 0x81 length-byte encoding path."""
    long_text = "A" * 200
    blob = _make_blob(long_text, use_0x81=True)
    result = _decode_attributed_body(blob)
    assert result == long_text


def test_malformed_blob_returns_none():
    """Random bytes with no NSString marker must return None, not raise."""
    assert _decode_attributed_body(b"\xde\xad\xbe\xef\x00\x01\x02") is None


def test_leading_control_chars_stripped():
    """Apple's StreamTypedCoder sometimes prepends a marker byte (\x02 etc)."""
    encoded = b"\x02hello world"
    n = len(encoded)
    blob = b"NSString\x01\x95\x84\x01\x2b" + bytes([n]) + encoded
    result = _decode_attributed_body(blob)
    assert result == "hello world"


def test_truncated_blob_after_nsstring_returns_none():
    """Blob ends immediately after NSString — not enough metadata bytes."""
    assert _decode_attributed_body(b"NSString") is None


def test_0x81_length_boundary_128():
    """A 128-char string uses the 0x81 path with length 128 = 0x0080."""
    text = "A" * 128
    blob = _make_blob(text)
    result = _decode_attributed_body(blob)
    assert result == text


def test_0x81_length_boundary_255():
    """A 255-char string uses the 0x81 path with length 255 = 0x00FF."""
    text = "B" * 255
    blob = _make_blob(text)
    result = _decode_attributed_body(blob)
    assert result == text


def test_0x81_length_boundary_256():
    """A 256-char string uses the 0x81 path with length 256 = 0x0100."""
    text = "C" * 256
    blob = _make_blob(text)
    result = _decode_attributed_body(blob)
    assert result == text


# -------------------------------------------------------------------
# Regression: →2536 — real-format 0x81 blob decoded as garbage "He"
#
# Root cause: the 0x81 tag in NSSerializer encodes a 16-bit big-endian
# length (2 bytes: HH LL). The old code read only 1 byte (HH) as the
# length, so for a 584-char message it read n=HH=0x02=2 and extracted
# after[7:9] = [0x48='H', first_text_byte='e'] = "He".
#
# Real blob structure for a 584-char message (length=0x0248):
#   NSString + 5 metadata bytes + 0x81 0x02 0x48 + text...
#              ^after[0..4]        ^[5]  ^[6]  ^[7]  ^[8..591]
#
# Buggy decoder: n = after[6] = 0x02 = 2
#                text_bytes = after[7:9] = [0x48, text[0]='e'] = "He"
# Fixed decoder: n = (after[6]<<8)|after[7] = 0x0248 = 584
#                text_bytes = after[8:8+584] = full message text
# -------------------------------------------------------------------

# Real-format blob: uses the observed real metadata bytes \x01\x94\x84\x01\x2b
# (from REAL_BLOB_ROWID5) rather than the synthetic helper's \x01\x95.
# length = 584 = 0x0248 → tag=0x81, HH=0x02, LL=0x48
# text starts at after[8]; first char 'e' was misread as part of "He" by old code.
_REAL_FORMAT_0x81_TEXT = "e" + ("x" * 583)  # 584 chars
REAL_FORMAT_0x81_BLOB = (
    b"NSString\x01\x94\x84\x01\x2b"  # 5 observed real metadata bytes
    b"\x81\x02\x48"                   # 0x81 tag + 16-bit big-endian length 0x0248=584
    + _REAL_FORMAT_0x81_TEXT.encode("utf-8")
)


def test_real_format_0x81_full_decode():
    """Real-format 0x81 blob (584 chars) decodes to the full text, not 'He'."""
    result = _decode_attributed_body(REAL_FORMAT_0x81_BLOB)
    assert result == _REAL_FORMAT_0x81_TEXT, (
        f"Expected 584-char text starting with 'e', got {repr(result[:20]) if result else None}"
    )


def test_real_format_0x81_not_garbage():
    """Decoded result is not the 2-char garbage 'He' from the old buggy path."""
    result = _decode_attributed_body(REAL_FORMAT_0x81_BLOB)
    assert result != "He", (
        "Decoder returned 2-char garbage 'He' — 0x81 tag must read 2 bytes "
        "big-endian, not 1 byte. The 0x48 at after[7] is the low byte of the "
        "length 0x0248=584, not the character 'H'."
    )
    assert result is not None
    assert len(result) == 584


def test_real_format_0x81_length_512():
    """A real-format 0x81 blob for a 512-char message decodes correctly.

    length=512=0x0200: tag=0x81, HH=0x02, LL=0x00.
    Old buggy decoder: n = HH = 2 → reads 2 chars.
    Fixed decoder:     n = 0x0200 = 512 → reads all 512 chars.
    """
    text = "Hello " * 85 + "Hi"  # 510+2 = 512 chars
    assert len(text) == 512
    encoded = text.encode("utf-8")
    blob = (
        b"NSString\x01\x94\x84\x01\x2b"
        b"\x81\x02\x00"  # length = 512 = 0x0200
        + encoded
    )
    result = _decode_attributed_body(blob)
    assert result == text, f"Expected 512-char text, got {repr(result[:30]) if result else None}"


# -------------------------------------------------------------------
# Real blob captured from ~/Library/Messages/chat.db (ROWID=5)
# text IS NULL, attributedBody contains a URL (44 bytes, simple length path)
# -------------------------------------------------------------------

# Real blob captured from chat.db ROWID=5 (URL message, simple length path).
# Captured 2026-05-22; text IS NULL, attributedBody contains a gemini.google.com URL.
REAL_BLOB_ROWID5 = bytes.fromhex(
    "040b73747265616d747970656481e803840140848484124e5341747472696275746564"
    "537472696e67008484084e534f626a656374008592848484084e53537472696e670194"
    "84012b2c68747470733a2f2f67656d696e692e676f6f676c652e636f6d2f7368617265"
    "2f6463326238646236653863618684026949012c928484840c4e5344696374696f6e61"
    "727900948401690492849696205f5f6b494d4c696e6b4973526963684c696e6b417474"
    "7269627574654e616d658692848484084e534e756d626572008484074e5356616c7565"
    "009484012a848401639d0186928496961d5f5f6b494d4d657373616765506172744174"
    "747269627574654e616d658692849b9c8499990086928496961e5f5f6b494d44617461"
    "44657465637465644174747269627574654e616d6586928484840d4e534d757461626c"
    "6544617461008484064e5344617461009499813e0284065b353734635d62706c697374"
    "3030d4010203040506070c582476657273696f6e592461726368697665725424746f70"
    "58246f626a6563747312000186a05f100f4e534b657965644172636869766572d20809"
    "0a0b5776657273696f6e5964642d726573756c74800b8001ac0d0e1c2425262c2d2e32"
    "353955246e756c6cd70f101112131415161718191a1b1a524d535624636c6173735241"
    "525154515052535252564e8006800a8002800710018008d41d1e1f10202122235f1012"
    "4e532e72616e676576616c2e6c656e6774685f10144e532e72616e676576616c2e6c6f"
    "636174696f6e5a4e532e7370656369616c8003800410048005102c1000d22728292a5a"
    "24636c6173736e616d655824636c6173736573574e5356616c7565a2292b584e534f62"
    "6a6563745f102c68747470733a2f2f67656d696e692e676f6f676c652e636f6d2f7368"
    "6172652f646332623864623665386361574874747055524cd22f1030315a4e532e6f62"
    "6a65637473808009d227283334574e534172726179a2332bd2272836375f100f444453"
    "63616e6e6572526573756c74a2382b5f100f44445363616e6e6572526573756c741001"
    "00080011001a00240029003200370049004e005600600062006400710077008600890090"
    "009300950097009a009d009f00a100a300a500a700a900b200c700de00e900eb00ed00"
    "ef00f100f300f500fa0105010e01160119012201510159015e0169016a016c01710179"
    "017c01810193019601a80000000000000201000000000000003a0000000000000000"
    "00000000000001aa8692849696165f5f6b494d4c696e6b4174747269627574654e616d"
    "658692848484054e5355524c00949d00928496962c68747470733a2f2f67656d696e69"
    "2e676f6f676c652e636f6d2f73686172652f64633262386462366538636186868686"
)


def test_real_blob_decodes_url():
    result = _decode_attributed_body(REAL_BLOB_ROWID5)
    assert result is not None
    assert "gemini.google.com" in result

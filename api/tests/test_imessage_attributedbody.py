"""Tests for _decode_attributed_body (→1648).

macOS chat.db stores recent message bodies in the attributedBody BLOB column
using StreamTypedCoder encoding. The text column is NULL for these messages.
"""
import pytest
from services.imessage import _decode_attributed_body


def _make_blob(text: str, use_0x81: bool = False) -> bytes:
    """Build a minimal StreamTypedCoder blob wrapping a plain string.

    Constructs the NSString section only — enough for the decoder to work.
    """
    encoded = text.encode("utf-8")
    n = len(encoded)
    if use_0x81 or n >= 128:
        length_field = bytes([0x81, n & 0xFF])
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

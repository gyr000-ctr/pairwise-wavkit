import threading

import pytest

import wavkit

def _raises(fn, exc=ValueError):
    try:
        fn()
    except exc:
        return True
    return False

import struct


def _chunk(cid, payload):
    pad = b"\x00" if len(payload) % 2 else b""
    return cid + struct.pack("<I", len(payload)) + payload + pad


def _fmt(channels=1, rate=8000, bits=8):
    align = channels * bits // 8
    return _chunk(b"fmt ", struct.pack("<HHIIHH", 1, channels, rate, rate * align, align, bits))


def _wav(*chs):
    body = b"".join(chs)
    return b"RIFF" + struct.pack("<I", 4 + len(body)) + b"WAVE" + body


def _basic():
    return _wav(_fmt(), _chunk(b"data", b"\x01\x02\x03"))


def _whole(data):
    s = wavkit.WavStream()
    s.feed(data)
    return s.finalize()


def _split(data, n):
    s = wavkit.WavStream()
    for i in range(0, len(data), n):
        s.feed(data[i:i + n])
    return s.finalize()


def _same_split(n):
    data = _basic()
    return _split(data, n) == _whole(data)


def _odd_padding_split():
    data = _wav(_fmt(), _chunk(b"JUNK", b"abc"), _chunk(b"data", b"\x05"))
    return _split(data, 3) == _whole(data)


def _sample24_split():
    data = _wav(_fmt(bits=24), _chunk(b"data", b"\xff\xff\xff\x01\x00\x00"))
    return _split(data, 5)["samples"] == [-1, 1]


def _samples16():
    data = _wav(_fmt(bits=16), _chunk(b"data", b"\x01\x00\xff\xff"))
    return _whole(data)["samples"] == [1, -1]


def _truncated():
    data = _basic()
    s = wavkit.WavStream()
    s.feed(data[:-2])
    return _raises(s.finalize)


def _bad_magic():
    s = wavkit.WavStream()
    return _raises(lambda: s.feed(b"RIFX" + b"\x00" * 20))


def _data_before_fmt():
    data = _wav(_chunk(b"data", b"\x01"), _fmt())
    return _raises(lambda: _whole(data))


def _feed_after_finalize():
    s = wavkit.WavStream()
    s.feed(_basic())
    s.finalize()
    return _raises(lambda: s.feed(b"\x00"))


def _double_finalize():
    s = wavkit.WavStream()
    s.feed(_basic())
    s.finalize()
    return _raises(s.finalize)


def _unknown_chunk():
    data = _wav(_chunk(b"LIST", b"\x00\x00"), _fmt(), _chunk(b"data", b"\x07"))
    return _whole(data)["data_size"] == 1


def _riff_mismatch():
    data = bytearray(_basic())
    struct.pack_into("<I", data, 4, 4)
    return _raises(lambda: _whole(bytes(data)))


def _samples_len():
    data = _wav(_fmt(bits=16), _chunk(b"data", b"\x01\x00\x02\x00"))
    return len(_whole(data)["samples"]) == 2


def _empty_feed():
    s = wavkit.WavStream()
    s.feed(b"")
    s.feed(_basic())
    return s.finalize()["data_size"] == 3

CASES = [
        ("split1", lambda: _same_split(1)),
        ("split3", lambda: _same_split(3)),
        ("split7", lambda: _same_split(7)),
        ("odd_padding", lambda: _odd_padding_split()),
        ("sample24", lambda: _sample24_split()),
        ("samples16", lambda: _samples16()),
        ("truncated", lambda: _truncated()),
        ("bad_magic", lambda: _bad_magic()),
        ("data_before_fmt", lambda: _data_before_fmt()),
        ("feed_after", lambda: _feed_after_finalize()),
        ("double_finalize", lambda: _double_finalize()),
        ("unknown_chunk", lambda: _unknown_chunk()),
        ("riff_mismatch", lambda: _riff_mismatch()),
        ("samples_len", lambda: _samples_len()),
        ("empty_feed", lambda: _empty_feed()),
    ]


@pytest.mark.parametrize("name,check", CASES, ids=[c[0] for c in CASES])
def test_spec(name, check):
    assert check() is True

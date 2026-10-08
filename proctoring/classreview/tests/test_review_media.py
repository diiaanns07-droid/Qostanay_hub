"""Clip validation and Range parsing (no server)."""

from __future__ import annotations

import pytest
from conftest import tiny_avi, tiny_mp4

from classreview.media import InvalidMedia, RangeNotSatisfiable, inspect, parse_range


def test_real_test_clip_is_valid_vp9_mp4(clip):
    info = inspect("video/mp4", clip)
    assert info.container == "mp4" and info.codec == "vp09" and info.browser_playable
    assert info.duration_s == pytest.approx(4.0, abs=0.2)


def test_hand_built_layouts():
    info = inspect("video/mp4", tiny_mp4(b"avc1", faststart=True))
    assert (info.codec, info.faststart, info.duration_s, info.browser_playable) == ("avc1", True, 6.0, True)
    assert inspect("video/mp4", tiny_mp4(b"avc1", faststart=False)).faststart is False
    mp4v = inspect("video/mp4", tiny_mp4(b"mp4v"))
    assert mp4v.codec == "mp4v" and not mp4v.browser_playable  # stored, but the UI offers a download
    avi = inspect("video/x-msvideo", tiny_avi())
    assert avi.container == "avi" and avi.codec == "MJPG" and not avi.browser_playable


@pytest.mark.parametrize(
    ("media_type", "data", "code"),
    [
        ("video/mp4", b"", "empty"),
        ("video/mp4", b"not a video at all, just text" * 4, "not_mp4"),
        ("video/mp4", tiny_mp4()[:-10], "truncated_mp4"),  # interrupted upload
        ("video/mp4", tiny_mp4()[:40] + b"\x00" * 10, "truncated_mp4"),
        ("video/mp4", tiny_avi(), "not_mp4"),  # AVI declared as MP4
        ("video/x-msvideo", tiny_mp4(), "not_avi"),  # MP4 declared as AVI
        ("video/x-msvideo", tiny_avi()[:-20], "truncated_avi"),
        ("image/jpeg", b"\xff\xd8\xff\xe0", "unsupported_type"),
    ],
)
def test_bad_files_are_rejected(media_type, data, code):
    with pytest.raises(InvalidMedia) as exc:
        inspect(media_type, data)
    assert exc.value.code == code and exc.value.message_ru


def test_mp4_without_moov_is_rejected():
    ftyp = tiny_mp4()[:24]
    with pytest.raises(InvalidMedia) as exc:
        inspect("video/mp4", ftyp + b"\x00\x00\x00\x10mdat" + b"\x00" * 8)
    assert exc.value.code == "mp4_without_index"


@pytest.mark.parametrize(
    ("header", "expected"),
    [
        (None, None),
        ("bytes=0-99", (0, 99)),
        ("bytes=100-", (100, 999)),
        ("bytes=-100", (900, 999)),
        ("bytes=900-5000", (900, 999)),
        ("bytes=0-0", (0, 0)),
        ("bytes=0-1,5-9", None),  # multi-range: whole file
        ("items=0-1", None),
        ("bytes=-", None),
    ],
)
def test_parse_range(header, expected):
    assert parse_range(header, 1000) == expected


@pytest.mark.parametrize("header", ["bytes=1000-", "bytes=5-2", "bytes=-0"])
def test_unsatisfiable_ranges(header):
    with pytest.raises(RangeNotSatisfiable):
        parse_range(header, 1000)

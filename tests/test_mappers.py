"""Response mappers: upstream QQ Music objects -> the published schema.

Fixtures mirror the shapes captured from the real API (2026-08-18), so
the mappers are pinned against upstream rather than an invented shape.
The schema is the SAME one netease-cli publishes — that is the whole
point of two resolvers behind one panel, so the normalisations that
make them agree (seconds -> milliseconds, mid as the track id) are the
contract, not incidental.
"""

from __future__ import annotations

import pytest

from qq_cli._mappers import (
    SCHEMA_VERSION,
    error_envelope,
    map_lyric,
    map_search,
    map_url,
    success_envelope,
)


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


class _Song:
    def __init__(self, mid, name, singers, album, interval):
        self.mid = mid
        self.name = name
        self.singer = [_Named(s) for s in singers]
        self.album = _Named(album)
        self.interval = interval


class _UrlItem:
    def __init__(self, mid, purl, result=0):
        self.mid = mid
        self.purl = purl
        self.result = result


class _UrlResponse:
    def __init__(self, items):
        self.data = items


def test_map_search_publishes_the_shared_schema():
    songs = [
        _Song("003aAYrm3GE0Ac", "稻香", ["周杰伦"], "魔杰座", 223),
        _Song("mid2", "晴天", ["周杰伦", "费玉清"], "叶惠美", 269),
    ]

    tracks = map_search(songs)

    assert tracks[0] == {
        "id": "003aAYrm3GE0Ac",
        "title": "稻香",
        "artist": "周杰伦",
        "album": "魔杰座",
        "duration": 223_000,
    }
    # Multiple singers join the same way netease-cli joins them.
    assert tracks[1]["artist"] == "周杰伦 / 费玉清"


def test_map_search_normalises_seconds_to_milliseconds():
    # QQ reports whole seconds; netease reports milliseconds. The panel
    # must not have to know which resolver answered.
    tracks = map_search([_Song("m", "t", ["a"], "al", 90)])
    assert tracks[0]["duration"] == 90_000


def test_map_search_empty_is_empty():
    assert map_search([]) == []


def test_map_url_returns_the_playable_url():
    resp = _UrlResponse([_UrlItem("mid1", "https://cdn.example/a.mp3?vkey=x")])

    resolved = map_url("mid1", resp)

    assert resolved == {
        "id": "mid1",
        "url": "https://cdn.example/a.mp3?vkey=x",
        "quality": "128k",
    }


def test_map_url_empty_purl_means_login_required():
    # The anonymous probe answered result=104003 with an empty purl.
    # Publishing "" would hand the player nothing; this is a refusal.
    resp = _UrlResponse([_UrlItem("mid1", "", result=104003)])

    with pytest.raises(ValueError) as exc:
        map_url("mid1", resp)
    assert "credential" in str(exc.value).lower()


def test_map_url_no_entry_raises():
    with pytest.raises(ValueError):
        map_url("mid1", _UrlResponse([]))


def test_map_lyric_extracts_the_lrc():
    class _Lyric:
        lyric = "[00:00.00]稻香\n[00:07.73]词：周杰伦"

    assert map_lyric(_Lyric()) == "[00:00.00]稻香\n[00:07.73]词：周杰伦"


def test_map_lyric_missing_is_empty():
    class _Lyric:
        lyric = ""

    assert map_lyric(_Lyric()) == ""


def test_success_envelope_carries_schema_version():
    env = success_envelope([{"id": "1"}])
    assert env["schema_version"] == SCHEMA_VERSION
    assert env["data"] == [{"id": "1"}]


def test_error_envelope_shape():
    env = error_envelope("upstream_rejected", "needs credential")
    assert env == {"error_class": "upstream_rejected", "message": "needs credential"}

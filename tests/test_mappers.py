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
    QUALITY_TIERS,
    SCHEMA_VERSION,
    TIER_BITRATES,
    credential_is_refreshable,
    error_envelope,
    extract_stream_url,
    ladder_from,
    map_credential,
    map_lyric,
    map_playlists,
    map_qr,
    map_qr_status,
    map_search,
    map_url,
    map_vip_info,
    success_envelope,
)


class _Named:
    def __init__(self, name: str) -> None:
        self.name = name


class _Album:
    def __init__(self, name: str, mid: str = "") -> None:
        self.name = name
        self.mid = mid


class _Song:
    def __init__(self, mid, name, singers, album, interval, album_mid=""):
        self.mid = mid
        self.name = name
        self.singer = [_Named(s) for s in singers]
        self.album = _Album(album, album_mid)
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
        _Song("003aAYrm3GE0Ac", "稻香", ["周杰伦"], "魔杰座", 223, album_mid="002eFUFm2XYZ7z"),
        _Song("mid2", "晴天", ["周杰伦", "费玉清"], "叶惠美", 269),
    ]

    tracks = map_search(songs)

    assert tracks[0] == {
        "id": "003aAYrm3GE0Ac",
        "title": "稻香",
        "artist": "周杰伦",
        "album": "魔杰座",
        "cover": "https://y.gtimg.cn/music/photo_new/T002R300x300M000002eFUFm2XYZ7z.jpg",
        "duration": 223_000,
    }
    # Multiple singers join the same way netease-cli joins them.
    assert tracks[1]["artist"] == "周杰伦 / 费玉清"
    # No album mid -> no fabricated art URL, same as netease-cli.
    assert tracks[1]["cover"] == ""


def test_map_search_normalises_seconds_to_milliseconds():
    # QQ reports whole seconds; netease reports milliseconds. The panel
    # must not have to know which resolver answered.
    tracks = map_search([_Song("m", "t", ["a"], "al", 90)])
    assert tracks[0]["duration"] == 90_000


def test_map_search_empty_is_empty():
    assert map_search([]) == []


def test_extract_stream_url_returns_the_playable_url():
    resp = _UrlResponse([_UrlItem("mid1", "https://cdn.example/a.mp3?vkey=x")])

    assert extract_stream_url(resp) == "https://cdn.example/a.mp3?vkey=x"


def test_extract_stream_url_prefixes_relative_purls():
    resp = _UrlResponse([_UrlItem("mid1", "M500001.mp3?vkey=x")])

    assert extract_stream_url(resp) == (
        "https://ws.stream.qqmusic.qq.com/M500001.mp3?vkey=x"
    )


def test_extract_stream_url_empty_purl_is_none():
    # The quality ladder probes several file types; an empty purl means
    # "this rendition is not playable for this session", which is a
    # probe miss, not an error — the caller refuses only after the
    # LAST rung comes back empty.
    assert extract_stream_url(_UrlResponse([_UrlItem("mid1", "", result=104003)])) is None
    assert extract_stream_url(_UrlResponse([])) is None


class _VipInfo:
    def __init__(self, svip=0, nickname=""):
        self.svip = svip
        self.userinfo = type("U", (), {"nick": nickname})() if nickname else None


def test_map_vip_info_publishes_the_session_identity():
    info = map_vip_info(_VipInfo(svip=1, nickname="Jim"))

    assert info == {"logged_in": True, "nickname": "Jim", "vip": True}


def test_map_vip_info_without_vip_or_nick():
    assert map_vip_info(_VipInfo()) == {"logged_in": True, "nickname": "", "vip": False}


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


class _Credential:
    """Stand-in for the library's pydantic credential model."""

    def __init__(self, refresh_token="", refresh_key="", **rest):
        self.refresh_token = refresh_token
        self.refresh_key = refresh_key
        self._rest = rest

    def model_dump(self, mode="python"):
        return {
            "refresh_token": self.refresh_token,
            "refresh_key": self.refresh_key,
            **self._rest,
        }


def test_credential_from_a_qr_login_is_refreshable():
    # A QR login hands back the renewal tokens; that is the whole reason
    # the store keeps the full model instead of the musicid/musickey pair.
    assert credential_is_refreshable(_Credential(refresh_token="rt", refresh_key="rk"))
    assert credential_is_refreshable(_Credential(refresh_key="rk"))


def test_hand_typed_credential_is_not_refreshable():
    # musicid + musickey authenticate today and can never be renewed:
    # the renewal request keys on tokens a hand-typed pair never carries.
    # Answering False here is what turns that into a named refusal
    # instead of a request upstream rejects with empty parameters.
    assert not credential_is_refreshable(_Credential())


def test_map_credential_publishes_the_whole_model():
    # Not just the pair: a caller that stored only musicid/musickey
    # would authenticate today and lose the ability to renew.
    published = map_credential(_Credential(refresh_token="rt", musicid=42, musickey="W_X"))

    assert published == {
        "refresh_token": "rt",
        "refresh_key": "",
        "musicid": 42,
        "musickey": "W_X",
    }


class _QR:
    def __init__(self, data=b"\x89PNG\r\n", mimetype="image/png", identifier="qrsig-1"):
        self.data = data
        self.mimetype = mimetype
        self.identifier = identifier


def test_map_qr_publishes_a_renderable_image_and_the_poll_token():
    published = map_qr(_QR(), "qq")

    assert published == {
        "identifier": "qrsig-1",
        "login_type": "qq",
        "mimetype": "image/png",
        "image_base64": "iVBORw0K",
    }


def test_map_qr_without_image_data_publishes_an_empty_string():
    # An identifier with no image is still pollable; the panel decides
    # whether it has anything to render.
    assert map_qr(_QR(data=b""), "qq")["image_base64"] == ""


class _Event:
    def __init__(self, name):
        self.name = name


class _QRResult:
    def __init__(self, event_name, credential=None):
        self.event = _Event(event_name)
        self.credential = credential


@pytest.mark.parametrize(
    ("event_name", "state"),
    [
        ("SCAN", "pending"),
        ("CONF", "scanned"),
        ("TIMEOUT", "expired"),
        ("REFUSE", "refused"),
    ],
)
def test_map_qr_status_names_every_non_terminal_event(event_name, state):
    # Every upstream event maps to a state the caller can branch on:
    # "keep polling" (pending/scanned) vs "start over" (expired/refused).
    # An unmapped event must never read as one of these.
    assert map_qr_status(_QRResult(event_name)) == {"state": state, "credential": None}


def test_map_qr_status_publishes_the_credential_on_done():
    result = _QRResult("DONE", credential=_Credential(refresh_token="rt", musicid=7))

    published = map_qr_status(result)

    assert published["state"] == "done"
    assert published["credential"]["musicid"] == 7
    # The renewal token rides along — losing it here is exactly the trap
    # the refresh verb cannot recover from.
    assert published["credential"]["refresh_token"] == "rt"


def test_map_qr_status_rejects_an_unknown_event():
    # A new upstream event must stop the flow, not silently read as
    # "keep polling" — that would spin forever on a dead code.
    with pytest.raises(ValueError, match="unknown"):
        map_qr_status(_QRResult("SOMETHING_NEW"))


class _PlaylistSummary:
    def __init__(self, id, title, picurl="", songnum=0, desc=""):
        self.id = id
        self.title = title
        self.picurl = picurl
        self.songnum = songnum
        self.desc = desc


def test_map_playlists_publishes_the_shared_row_shape():
    # The panel renders one playlist row for either resolver, so the
    # field names are the contract — same posture as the track row.
    rows = map_playlists(
        [
            _PlaylistSummary(
                7001, "私人雷达", "https://y.gtimg.cn/p.jpg", 30, "每日更新"
            )
        ]
    )

    assert rows == [
        {
            "id": "7001",
            "title": "私人雷达",
            "cover": "https://y.gtimg.cn/p.jpg",
            "count": 30,
            "description": "每日更新",
        }
    ]


def test_map_playlists_stringifies_the_id():
    # Ids are per-source opaque tokens the caller hands straight back;
    # netease's are numeric too but the panel must never do arithmetic
    # on either, so both publish strings.
    assert map_playlists([_PlaylistSummary(42, "t")])[0]["id"] == "42"


def test_map_playlists_empty_is_empty():
    assert map_playlists([]) == []


def test_quality_tiers_are_the_shared_vocabulary():
    # The SAME three words netease-cli accepts and answers in. A caller
    # asks for a tier, never a bitrate: this upstream exposes file types
    # with no bitrate knob, so bps could never have been the shared word.
    assert QUALITY_TIERS == ("lossless", "high", "standard")


def test_ladder_from_starts_at_the_requested_tier():
    # A caller that picked the cheap tier gets the cheap tier; only the
    # fallback direction (down) is automatic.
    assert ladder_from("standard") == ("standard",)
    assert ladder_from("high") == ("high", "standard")
    assert ladder_from(None) == QUALITY_TIERS


def test_ladder_from_unknown_tier_is_empty():
    # Caller error, reported by the verb — never coerced to a default
    # the caller did not ask for.
    assert ladder_from("ultra") == ()


def test_map_url_publishes_the_tier_and_its_bitrate():
    resolved = map_url("mid1", "https://cdn.example/a.flac?vkey=x", "lossless")

    assert resolved == {
        "id": "mid1",
        "url": "https://cdn.example/a.flac?vkey=x",
        "quality": "lossless",
        "bitrate": TIER_BITRATES["lossless"],
    }


def test_map_url_bitrate_is_nominal_for_this_source():
    # QQ answers a file TYPE, not a measured bitrate, so the published
    # number is this tier's nominal rate. Marked here because it is a
    # real difference from netease-cli, which reports what upstream
    # measured — the field means "kbps you are getting", and for this
    # source that is the rung's nominal value.
    assert map_url("m", "u", "standard")["bitrate"] == 128

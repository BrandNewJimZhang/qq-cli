"""Upstream QQ Music objects -> the resolver schema the panel consumes.

Kept free of network and of the qqmusic_api import so every shape
decision is unit-testable against plain fixtures. The published schema
is the SAME one netease-cli emits: the music panel must not have to
know which resolver answered, so the two normalisations that make them
agree live here and are pinned by tests.
"""

from __future__ import annotations

from typing import Any

#: Envelope version the AutoSkill runner unwraps. Kept in lockstep with
#: netease-cli's SchemaVersion — one schema, two resolvers.
SCHEMA_VERSION = 1

#: The bitrate label for the file type this CLI requests (MP3_128).
#: Widening to lossless needs a credential, so the label is fixed until
#: a caller asks for the quality axis.
_QUALITY_LABEL = "128k"

#: QQ serves album art off a stable CDN path keyed by album mid; the
#: 300x300 rendition matches the panel's hero size. Same field name as
#: netease-cli's cover — one schema, two resolvers.
_COVER_URL = "https://y.gtimg.cn/music/photo_new/T002R300x300M000{mid}.jpg"


def success_envelope(data: Any) -> dict[str, Any]:
    return {"schema_version": SCHEMA_VERSION, "data": data}


def error_envelope(error_class: str, message: str) -> dict[str, str]:
    return {"error_class": error_class, "message": message}


def map_search(songs: list[Any]) -> list[dict[str, Any]]:
    """Publish search rows.

    ``mid`` is the id, not the numeric ``id``: both the url and lyric
    endpoints key on mid, so publishing the numeric one would hand the
    caller a token the other two verbs cannot use.

    ``interval`` is whole seconds upstream; netease reports
    milliseconds. Normalised here so one panel renders both.
    """
    rows: list[dict[str, Any]] = []
    for song in songs:
        singers = [s.name for s in getattr(song, "singer", []) if getattr(s, "name", "")]
        album = getattr(song, "album", None)
        album_mid = getattr(album, "mid", "") if album else ""
        rows.append(
            {
                "id": song.mid,
                "title": song.name,
                "artist": " / ".join(singers),
                "album": getattr(album, "name", "") if album else "",
                "cover": _COVER_URL.format(mid=album_mid) if album_mid else "",
                "duration": int(getattr(song, "interval", 0) or 0) * 1000,
            }
        )
    return rows


def map_url(mid: str, response: Any) -> dict[str, Any]:
    """Publish one playable stream, or refuse plainly.

    QQ answers 200 with an empty ``purl`` when the request carries no
    credential (observed ``result=104003``). Publishing an empty url
    would hand the player nothing and read as our bug, so it raises.
    """
    entries = getattr(response, "data", None) or []
    if not entries:
        raise ValueError(f"url response carries no entry for {mid}")
    entry = entries[0]
    purl = getattr(entry, "purl", "") or ""
    if not purl:
        raise ValueError(
            f"QQ Music returned no playable url for {mid} "
            f"(result={getattr(entry, 'result', '?')}); most tracks need a "
            "credential — set QQ_MUSIC_MUSICID and QQ_MUSIC_MUSICKEY"
        )
    url = purl if purl.startswith("http") else f"https://ws.stream.qqmusic.qq.com/{purl}"
    return {"id": mid, "url": url, "quality": _QUALITY_LABEL}


def map_lyric(response: Any) -> str:
    """Publish the LRC document, or ``""`` when the track has none."""
    return getattr(response, "lyric", "") or ""

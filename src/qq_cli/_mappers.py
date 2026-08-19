"""Upstream QQ Music objects -> the resolver schema the panel consumes.

Kept free of network and of the qqmusic_api import so every shape
decision is unit-testable against plain fixtures. The published schema
is the SAME one netease-cli emits: the music panel must not have to
know which resolver answered, so the two normalisations that make them
agree live here and are pinned by tests.
"""

from __future__ import annotations

import base64
from typing import Any

#: Envelope version the AutoSkill runner unwraps. Kept in lockstep with
#: netease-cli's SchemaVersion — one schema, two resolvers.
SCHEMA_VERSION = 1

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


def extract_stream_url(response: Any) -> str | None:
    """The playable URL in a song-url response, or None.

    QQ answers 200 with an empty ``purl`` when the session may not play
    this rendition (no credential, or the tier is above the account's
    plan — observed ``result=104003``). The quality ladder in __main__
    probes several file types, so an empty purl is a probe miss here,
    not an error: the caller refuses only after the LAST rung is empty.
    """
    entries = getattr(response, "data", None) or []
    if not entries:
        return None
    purl = getattr(entries[0], "purl", "") or ""
    if not purl:
        return None
    return purl if purl.startswith("http") else f"https://ws.stream.qqmusic.qq.com/{purl}"


def map_url(mid: str, url: str, quality_label: str) -> dict[str, Any]:
    """Publish one playable stream with the quality rung that answered."""
    return {"id": mid, "url": url, "quality": quality_label}


def map_vip_info(response: Any) -> dict[str, Any]:
    """Publish the session identity behind a successful vip-info call.

    Reaching this mapper at all means the credential authenticated
    (the call requires login), so ``logged_in`` is True by construction;
    the anonymous/expired paths short-circuit in __main__. ``svip`` is
    QQ's premium-membership flag; the nickname rides the userinfo
    summary when the response carries one.
    """
    userinfo = getattr(response, "userinfo", None)
    nickname = (getattr(userinfo, "nick", "") or "") if userinfo else ""
    return {
        "logged_in": True,
        "nickname": nickname,
        "vip": bool(getattr(response, "svip", 0)),
    }


def map_lyric(response: Any) -> str:
    """Publish the LRC document, or ``""`` when the track has none."""
    return getattr(response, "lyric", "") or ""


def credential_is_refreshable(credential: Any) -> bool:
    """Whether *credential* carries the tokens a renewal request needs.

    Upstream's renewal keys on ``refresh_token`` / ``refresh_key``,
    which only a QR login produces. A hand-typed musicid/musickey pair
    carries neither: it authenticates today and can never be renewed.
    Saying so here is what turns that into a named refusal instead of a
    request upstream rejects with empty parameters.
    """
    return bool(
        (getattr(credential, "refresh_token", "") or "")
        or (getattr(credential, "refresh_key", "") or "")
    )


def map_credential(credential: Any) -> dict[str, Any]:
    """Publish a credential as the JSON blob the caller stores back.

    The WHOLE model, not the musicid/musickey pair the url verb needs:
    a caller that kept only the pair could authenticate today and would
    lose the renewal tokens with it, which is exactly the trap this
    resolver's refresh verb exists to avoid.
    """
    return credential.model_dump(mode="json")


def map_qr(qr: Any, login_type: str) -> dict[str, Any]:
    """Publish a login QR: the image to render, the token to poll with.

    ``identifier`` is the whole of the poll state — upstream's status
    check keys on it alone — so the caller can render this code, exit,
    and poll from a separate process. That is why this verb pair works
    at all across a CLI boundary.
    """
    data = getattr(qr, "data", b"") or b""
    return {
        "identifier": qr.identifier,
        "login_type": login_type,
        "mimetype": getattr(qr, "mimetype", "") or "",
        "image_base64": base64.b64encode(data).decode("ascii") if data else "",
    }


#: upstream QR event -> the state the caller branches on. Keyed by enum
#: NAME so this module stays free of the qqmusic_api import. Two states
#: mean "keep polling" (pending, scanned) and two mean "start over"
#: (expired, refused); netease-cli publishes the same five.
_QR_STATES = {
    "SCAN": "pending",
    "CONF": "scanned",
    "DONE": "done",
    "TIMEOUT": "expired",
    "REFUSE": "refused",
}


def map_qr_status(result: Any) -> dict[str, Any]:
    """Publish one poll result: the state, plus the credential on DONE.

    An event this mapper does not know raises rather than defaulting to
    "pending": a caller told to keep polling a code upstream has stopped
    honouring would spin until its own timeout with nothing to show.
    """
    name = getattr(getattr(result, "event", None), "name", "")
    state = _QR_STATES.get(name)
    if state is None:
        raise ValueError(
            f"unknown QR login event {name!r}; known: {', '.join(sorted(_QR_STATES))}"
        )
    credential = getattr(result, "credential", None)
    return {
        "state": state,
        "credential": map_credential(credential) if credential is not None else None,
    }

"""qq-cli — an agent-native resolver for QQ Music.

Read-only resolution (search / url / lyric / whoami) plus the login
lifecycle its credentials need (login start / login poll / refresh),
each answering one JSON envelope on stdout. The command surface and the
published schema match netease-cli field for field: the music panel
fans out to both and merges the rows without knowing which answered.

Contract:
  - stdout: {"schema_version":1,"data":<payload>}
  - stderr: one {"error_class","message"} line on failure
  - exit 0 ok / 3 bad input / 4 upstream refused

Credentials: search and lyric work anonymously; stream URLs do not (QQ
answers result=104003 with an empty purl). Two shapes are accepted, and
which one you have decides whether the key can ever be renewed:

  - QQ_MUSIC_CREDENTIAL — the full JSON blob ``login poll`` published.
    Carries the renewal tokens, so ``refresh`` can extend it.
  - QQ_MUSIC_MUSICID / QQ_MUSIC_MUSICKEY — a pair copied out of a
    browser session. Authenticates, but carries no renewal tokens, so
    ``refresh`` refuses it by name rather than firing a doomed request.

A signed-in session walks the quality ladder (flac -> 320k -> 128k) and
``whoami`` reports who the credential authenticates as.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from qq_cli._mappers import (
    credential_is_refreshable,
    error_envelope,
    extract_stream_url,
    map_credential,
    map_lyric,
    map_qr,
    map_qr_status,
    map_search,
    map_url,
    map_vip_info,
    success_envelope,
)

EXIT_OK = 0
EXIT_BAD_INPUT = 3
EXIT_UPSTREAM_REFUSED = 4


class CredentialFault(Exception):
    """A fault in the credential this process was handed.

    Carried as its own class so the dispatcher reports it with the name
    the caller must branch on — a corrupt stored blob and an expired key
    are not "upstream rejected", and a caller that renews on the wrong
    one loops forever.
    """

    def __init__(self, error_class: str, message: str, exit_code: int) -> None:
        super().__init__(message)
        self.error_class = error_class
        self.exit_code = exit_code


def _emit(data: Any) -> int:
    print(json.dumps(success_envelope(data), ensure_ascii=False))
    return EXIT_OK


def _fail(error_class: str, message: str, exit_code: int) -> int:
    print(json.dumps(error_envelope(error_class, message), ensure_ascii=False), file=sys.stderr)
    return exit_code


def _credential():
    """Build a Credential from the environment, or None when unset.

    Absent credentials are a valid anonymous session (search and lyric
    still work), so this returns None rather than raising — the url verb
    is where the refusal surfaces, with a message naming these vars.

    The full blob wins over the pair when both are exported: it is a
    superset, and it is the only one that can be renewed.
    """
    from pydantic import ValidationError
    from qqmusic_api import Credential

    blob = os.environ.get("QQ_MUSIC_CREDENTIAL", "").strip()
    if blob:
        try:
            return Credential.model_validate_json(blob)
        except ValidationError as exc:
            raise CredentialFault(
                "bad_credential",
                f"QQ_MUSIC_CREDENTIAL is not a valid credential document: {exc}",
                EXIT_BAD_INPUT,
            ) from exc
    musicid = os.environ.get("QQ_MUSIC_MUSICID", "").strip()
    musickey = os.environ.get("QQ_MUSIC_MUSICKEY", "").strip()
    if not musicid or not musickey:
        return None
    if not musicid.isdigit():
        raise CredentialFault(
            "bad_credential",
            f"QQ_MUSIC_MUSICID must be numeric, got {musicid!r}",
            EXIT_BAD_INPUT,
        )
    return Credential(musicid=int(musicid), musickey=musickey)


async def _run_search(keyword: str, limit: int) -> list[dict[str, Any]]:
    from qqmusic_api import Client
    from qqmusic_api.modules.search import SearchApi, SearchType

    async with Client(_credential()) as client:
        response = await SearchApi(client).search_by_type(
            keyword, SearchType.SONG, num=limit
        )
        return map_search(response.song)


async def _run_url(mid: str) -> dict[str, Any]:
    """Resolve a stream, walking the quality ladder top-down.

    A signed-in session may be entitled to lossless; anonymous sessions
    only ever get the 128k probe (higher rungs always answer empty
    without a credential, so probing them would just burn requests).
    An empty purl on every rung is a refusal, reported plainly.
    """
    from qqmusic_api import Client
    from qqmusic_api.modules.song import SongApi, SongFileInfo, SongFileType

    credential = _credential()
    ladder = (
        [(SongFileType.FLAC, "flac"), (SongFileType.MP3_320, "320k"), (SongFileType.MP3_128, "128k")]
        if credential
        else [(SongFileType.MP3_128, "128k")]
    )
    async with Client(credential) as client:
        api = SongApi(client)
        for file_type, label in ladder:
            response = await api.get_song_urls([SongFileInfo(mid=mid)], file_type)
            url = extract_stream_url(response)
            if url:
                return map_url(mid, url, label)
    raise ValueError(
        f"QQ Music returned no playable url for {mid}; most tracks need a "
        "credential — set QQ_MUSIC_MUSICID and QQ_MUSIC_MUSICKEY (a track "
        "above the account's plan is also refused)"
    )


async def _run_whoami() -> dict[str, Any]:
    """Who the stored credential authenticates as.

    Anonymous (no env credential) and rejected/expired credentials both
    answer ``logged_in: false`` — that is the verdict the caller's login
    verification needs. Network/API faults still exit 4: a transient
    outage must not read as "your login is invalid".
    """
    from qqmusic_api import Client
    from qqmusic_api.modules.login import LoginApi
    from qqmusic_api.modules.user import UserApi

    credential = _credential()
    if credential is None:
        return {"logged_in": False, "nickname": "", "vip": False}
    async with Client(credential) as client:
        # check_expired is the ONE server-side verdict on the pair:
        # get_vip_info answers a defaulted (all-zero) model for a bogus
        # credential instead of raising, so it cannot carry the verdict.
        if await LoginApi(client).check_expired():
            return {"logged_in": False, "nickname": "", "vip": False}
        return map_vip_info(await UserApi(client).get_vip_info())


async def _run_lyric(mid: str) -> dict[str, Any]:
    from qqmusic_api import Client
    from qqmusic_api.modules.lyric import LyricApi

    async with Client(_credential()) as client:
        response = await LyricApi(client).get_lyric(mid)
        return {"id": mid, "lrc": map_lyric(response)}


def _qr_login_type(name: str):
    from qqmusic_api.models.login import QRLoginType

    try:
        return QRLoginType(name)
    except ValueError:
        raise CredentialFault(
            "bad_input",
            f"unknown login type {name!r}; known: "
            f"{', '.join(t.value for t in QRLoginType)}",
            EXIT_BAD_INPUT,
        ) from None


async def _run_login_start(login_type: str) -> dict[str, Any]:
    """Mint a login QR and publish it with the token to poll it by.

    No credential is read here: this verb is how one is obtained.
    """
    from qqmusic_api import Client
    from qqmusic_api.modules.login import LoginApi

    async with Client() as client:
        qr = await LoginApi(client).get_qrcode(_qr_login_type(login_type))
        return map_qr(qr, login_type)


async def _run_login_poll(identifier: str, login_type: str) -> dict[str, Any]:
    """Check one minted code, publishing its state and — on DONE — the
    credential to store.

    The QR is rebuilt from the identifier alone because that is all
    upstream's status check reads; the image bytes were the caller's
    business and are not needed again. That is what lets ``start`` and
    ``poll`` be separate processes.
    """
    from qqmusic_api import Client
    from qqmusic_api.models.login import QR
    from qqmusic_api.modules.login import LoginApi

    qr = QR(
        data=b"",
        qr_type=_qr_login_type(login_type),
        mimetype="",
        identifier=identifier,
    )
    async with Client() as client:
        return map_qr_status(await LoginApi(client).check_qrcode(qr))


async def _run_refresh() -> dict[str, Any]:
    """Renew the stored credential, publishing the replacement.

    QQ's musickey expires; renewal keys on tokens only a QR login
    produces. The two ways this cannot work are named rather than sent
    upstream: nothing stored, and a hand-typed pair that never carried
    renewal tokens. An upstream refusal means the key is past renewal —
    the caller must sign in again, and telling it so by class is what
    keeps it from retrying forever.
    """
    from qqmusic_api import Client
    from qqmusic_api.core.exceptions import CredentialRefreshError
    from qqmusic_api.modules.login import LoginApi

    credential = _credential()
    if credential is None:
        raise CredentialFault(
            "credential_missing",
            "nothing to refresh: export QQ_MUSIC_CREDENTIAL from a login poll",
            EXIT_BAD_INPUT,
        )
    if not credential_is_refreshable(credential):
        raise CredentialFault(
            "credential_not_refreshable",
            "this credential carries no renewal tokens — a musicid/musickey "
            "pair copied by hand can be used but never renewed; sign in with "
            "'login start' to get one that can",
            EXIT_BAD_INPUT,
        )
    async with Client(credential) as client:
        try:
            renewed = await LoginApi(client).refresh_credential(credential)
        except CredentialRefreshError as exc:
            raise CredentialFault(
                "credential_expired",
                f"QQ Music refused to renew this credential: {exc}",
                EXIT_UPSTREAM_REFUSED,
            ) from exc
    return map_credential(renewed)


def _dispatch(args: argparse.Namespace) -> int:
    """Run one verb, translating every upstream failure to exit 4.

    One handling site: the verbs above raise (ValueError from the
    mappers, or the library's own exceptions) and the class is preserved
    in the message rather than being remapped to an empty result.
    ``CredentialFault`` is caught FIRST and keeps its own class: a
    caller that cannot tell "renew this" from "upstream is down" either
    retries forever or drops a working login.
    """
    try:
        if args.command == "search":
            return _emit(asyncio.run(_run_search(args.keyword, args.limit)))
        if args.command == "url":
            return _emit(asyncio.run(_run_url(args.id)))
        if args.command == "whoami":
            return _emit(asyncio.run(_run_whoami()))
        if args.command == "refresh":
            return _emit(asyncio.run(_run_refresh()))
        if args.command == "login":
            if args.login_command == "start":
                return _emit(asyncio.run(_run_login_start(args.type)))
            return _emit(asyncio.run(_run_login_poll(args.identifier, args.type)))
        return _emit(asyncio.run(_run_lyric(args.id)))
    except CredentialFault as exc:
        return _fail(exc.error_class, str(exc), exc.exit_code)
    except ValueError as exc:
        return _fail("upstream_rejected", str(exc), EXIT_UPSTREAM_REFUSED)
    except Exception as exc:  # upstream library / transport failure
        return _fail(
            "upstream_rejected",
            f"{type(exc).__name__}: {exc}",
            EXIT_UPSTREAM_REFUSED,
        )


def _parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="qq-cli", description="QQ Music resolver")
    subs = parser.add_subparsers(dest="command", required=True)

    search = subs.add_parser("search", help="search songs by keyword")
    search.add_argument("--keyword", required=True)
    search.add_argument("--limit", type=int, default=20)

    url = subs.add_parser("url", help="resolve a playable url for a track mid")
    url.add_argument("--id", required=True, help="track mid (from search)")
    url.add_argument("--quality", default=None, help="accepted and ignored (MP3_128 only)")

    lyric = subs.add_parser("lyric", help="fetch the LRC document for a track mid")
    lyric.add_argument("--id", required=True, help="track mid (from search)")

    whoami = subs.add_parser("whoami", help="who the stored credential authenticates as")

    refresh = subs.add_parser("refresh", help="renew the stored credential")

    login = subs.add_parser("login", help="obtain a credential by QR scan")
    login_subs = login.add_subparsers(dest="login_command", required=True)
    login_start = login_subs.add_parser("start", help="mint a login QR code")
    login_poll = login_subs.add_parser("poll", help="check a minted QR code once")
    login_poll.add_argument(
        "--identifier", required=True, help="the token 'login start' published"
    )
    for sub in (login_start, login_poll):
        sub.add_argument("--type", default="qq", help="qr channel: qq / wx / mobile")

    for sub in (search, url, lyric, whoami, refresh, login_start, login_poll):
        sub.add_argument("--format", default="json", help="output format (json only)")
    return parser


def main() -> int:
    parser = _parser()
    try:
        args = parser.parse_args()
    except SystemExit:
        # argparse already wrote usage; answer with our own exit code so
        # the caller sees the same 3 every bad-input path uses.
        return EXIT_BAD_INPUT
    if args.format != "json":
        return _fail(
            "bad_input",
            f"unsupported --format {args.format!r}; this CLI emits json only",
            EXIT_BAD_INPUT,
        )
    if args.command == "search" and args.limit <= 0:
        return _fail("bad_input", "--limit must be positive", EXIT_BAD_INPUT)
    return _dispatch(args)


if __name__ == "__main__":
    sys.exit(main())

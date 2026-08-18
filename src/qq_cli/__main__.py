"""qq-cli — an agent-native resolver for QQ Music.

Three read-only verbs (search / url / lyric) over qqmusic-api-python,
each answering one JSON envelope on stdout. The command surface and the
published schema match netease-cli field for field: the music panel
fans out to both and merges the rows without knowing which answered.

Contract:
  - stdout: {"schema_version":1,"data":<payload>}
  - stderr: one {"error_class","message"} line on failure
  - exit 0 ok / 3 bad input / 4 upstream refused

Credentials: search and lyric work anonymously; stream URLs do not (QQ
answers result=104003 with an empty purl). Export QQ_MUSIC_MUSICID and
QQ_MUSIC_MUSICKEY from an existing login — this tool deliberately
implements no login flow of its own. A signed-in session walks the
quality ladder (flac -> 320k -> 128k) and ``whoami`` reports who the
credential authenticates as.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import os
import sys
from typing import Any

from qq_cli._mappers import (
    error_envelope,
    extract_stream_url,
    map_lyric,
    map_search,
    map_url,
    map_vip_info,
    success_envelope,
)

EXIT_OK = 0
EXIT_BAD_INPUT = 3
EXIT_UPSTREAM_REFUSED = 4


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
    """
    musicid = os.environ.get("QQ_MUSIC_MUSICID", "").strip()
    musickey = os.environ.get("QQ_MUSIC_MUSICKEY", "").strip()
    if not musicid or not musickey:
        return None
    from qqmusic_api import Credential

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


def _dispatch(args: argparse.Namespace) -> int:
    """Run one verb, translating every upstream failure to exit 4.

    One handling site: the verbs above raise (ValueError from the
    mappers, or the library's own exceptions) and the class is preserved
    in the message rather than being remapped to an empty result.
    """
    try:
        if args.command == "search":
            return _emit(asyncio.run(_run_search(args.keyword, args.limit)))
        if args.command == "url":
            return _emit(asyncio.run(_run_url(args.id)))
        if args.command == "whoami":
            return _emit(asyncio.run(_run_whoami()))
        return _emit(asyncio.run(_run_lyric(args.id)))
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

    for sub in (search, url, lyric, whoami):
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

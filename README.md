# qq-cli

An agent-native resolver for QQ Music: three read-only verbs that answer
JSON. The command surface and published schema match
[`netease-cli`](../netease-cli) field for field, so one panel can fan
out to both and merge the rows.

Wraps [qqmusic-api-python](https://pypi.org/project/qqmusic-api-python/).

## Install

```bash
pipx install .          # or: pip install .
```

## Verbs

```bash
qq-cli search --keyword "周杰伦 稻香" --limit 20
qq-cli url    --id 003aAYrm3GE0Ac
qq-cli lyric  --id 003aAYrm3GE0Ac
```

The `--id` every verb takes is the track **mid** returned by `search`
(both `url` and `lyric` key on mid, so that is what search publishes).

## Contract

```json
{"schema_version": 1, "data": ...}
```

| Verb     | `data` shape |
|----------|--------------|
| `search` | `[{id, title, artist, album, cover, duration}]` — duration in **milliseconds** (QQ reports seconds; normalised so it matches netease-cli), `cover` an album-art URL or `""`, multiple singers joined with ` / ` |
| `url`    | `{id, url, quality}` |
| `lyric`  | `{id, lrc}` — LRC document, `""` when the track has none |

Failures print one line on stderr and exit non-zero:

```json
{"error_class": "upstream_rejected", "message": "..."}
```

| Exit | Meaning |
|------|---------|
| 0    | ok |
| 3    | bad input (missing/invalid flag, unknown command) |
| 4    | upstream refused (no playable url, transport or API failure) |

## Credentials

Search and lyric work anonymously. **Stream URLs do not**: QQ answers
`result=104003` with an empty `purl` for an anonymous request. Export an
existing login:

```bash
export QQ_MUSIC_MUSICID=<your musicid>
export QQ_MUSIC_MUSICKEY=<your musickey>
```

This tool implements no login flow of its own — same posture netease-cli
takes with `MUSICFOX_COOKIE`.

## Scope and limits

- **MP3 128k only.** Higher bitrates need a credential and a wider file
  type; the quality axis stays closed until a caller asks for it
  (`--quality` is accepted and ignored so the two resolvers share one
  argv shape).
- **No unlocking.** A track this account may not play answers exit 4.
- **Reverse-engineered.** QQ Music publishes no personal-use API;
  upstream can change or break at any time.
- **Line-level lyrics only.** The `qrc` word-level field is not
  published.

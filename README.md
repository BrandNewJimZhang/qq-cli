# qq-cli

An agent-native resolver for QQ Music: read-only verbs that answer JSON,
plus the login lifecycle their credentials need. The command surface and
published schema match [`netease-cli`](../netease-cli) field for field,
so one panel can fan out to both and merge the rows.

Wraps [qqmusic-api-python](https://pypi.org/project/qqmusic-api-python/).

## Install

```bash
pipx install .          # or: pip install .
```

## Verbs

```bash
qq-cli search --keyword "周杰伦 稻香" --limit 20
qq-cli whoami
qq-cli url    --id 003aAYrm3GE0Ac
qq-cli lyric  --id 003aAYrm3GE0Ac

qq-cli login start --type qq
qq-cli login poll  --identifier <token from start>
qq-cli refresh
```

The `--id` every resolution verb takes is the track **mid** returned by
`search` (both `url` and `lyric` key on mid, so that is what search
publishes).

## Contract

```json
{"schema_version": 1, "data": ...}
```

| Verb           | `data` shape |
|----------------|--------------|
| `search`       | `[{id, title, artist, album, cover, duration}]` — duration in **milliseconds** (QQ reports seconds; normalised so it matches netease-cli), `cover` an album-art URL or `""`, multiple singers joined with ` / ` |
| `url`          | `{id, url, quality}` — quality is the ladder rung that answered: `flac` / `320k` / `128k` |
| `lyric`        | `{id, lrc}` — LRC document, `""` when the track has none |
| `whoami`       | `{logged_in, nickname, vip}` — server-verified session verdict (anonymous and rejected credentials both answer `logged_in: false`) |
| `login start`  | `{identifier, login_type, mimetype, image_base64}` — the QR to render and the token to poll it by |
| `login poll`   | `{state, credential}` — `state` is one of `pending` / `scanned` / `done` / `expired` / `refused`; `credential` is the storable blob, non-null only on `done` |
| `refresh`      | the renewed credential blob, same shape `login poll` publishes on `done` |

Failures print one line on stderr and exit non-zero:

```json
{"error_class": "upstream_rejected", "message": "..."}
```

| Exit | Meaning |
|------|---------|
| 0    | ok |
| 3    | bad input (missing/invalid flag, unknown command, unusable credential) |
| 4    | upstream refused (no playable url, transport or API failure) |

| `error_class`               | Exit | Meaning |
|-----------------------------|------|---------|
| `bad_input`                 | 3    | malformed flag or unknown login type |
| `bad_credential`            | 3    | `QQ_MUSIC_CREDENTIAL` is not a valid credential document |
| `credential_missing`        | 3    | `refresh` with nothing stored |
| `credential_not_refreshable`| 3    | a hand-typed pair carries no renewal tokens |
| `credential_expired`        | 4    | upstream refused the renewal — sign in again |
| `upstream_rejected`         | 4    | no playable url, transport or API failure |

## Credentials

Search and lyric work anonymously. **Stream URLs do not**: QQ answers
`result=104003` with an empty `purl` for an anonymous request.

Two shapes are accepted, and which one you have decides whether the key
can ever be renewed:

```bash
# Preferred: the blob `login poll` published on `done`. Carries the
# renewal tokens, so `refresh` can extend it before it expires.
export QQ_MUSIC_CREDENTIAL='{"musicid":...,"musickey":"...","refresh_key":"..."}'

# Fallback: a pair copied out of a browser session. Authenticates, but
# carries no renewal tokens — `refresh` refuses it by name rather than
# firing a request upstream would reject with empty parameters.
export QQ_MUSIC_MUSICID=<your musicid>
export QQ_MUSIC_MUSICKEY=<your musickey>
```

The blob wins when both are exported: it is a superset of the pair.

### QR login

`login start` mints a code and publishes its `identifier`; `login poll`
checks that identifier once and answers a state. The identifier is the
whole of the poll state — upstream's status check reads nothing else —
so the two verbs can run as separate processes, which is what lets a
caller render the code, hand control back to its own event loop, and
poll on its own schedule. Poll until `done` (store the credential),
`expired`, or `refused` (mint a new code).

## Scope and limits

- **Quality ladder.** A signed-in session probes `flac` -> `320k` ->
  `128k` and answers the first rung the account may play; anonymous
  sessions only probe `128k` (higher rungs always answer empty without
  a credential). `--quality` is accepted and ignored so the two
  resolvers share one argv shape.
- **No unlocking.** A track this account may not play answers exit 4.
  This resolver will never route around an entitlement it was refused.
- **QR channels.** `--type` accepts `qq` / `wx` / `mobile`; only `qq` is
  exercised end to end. The other two reach the same upstream call and
  are published for it, not verified against it.
- **Reverse-engineered.** QQ Music publishes no personal-use API;
  upstream can change or break at any time.
- **Line-level lyrics only.** The `qrc` word-level field is not
  published.

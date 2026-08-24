# qq-cli

[English](README.md) | **简体中文**

![Python](https://img.shields.io/badge/Python-3.10+-3776AB?logo=python&logoColor=white)
![License: MIT](https://img.shields.io/badge/license-MIT-green)
![Schema](https://img.shields.io/badge/schema__version-2-blue)

面向 agent 的 QQ 音乐 resolver：以 JSON 应答的只读动词，加上凭据
所需的登录生命周期。命令表面与发布 schema 同
[`netease-cli`](https://github.com/BrandNewJimZhang/netease-cli)
逐字段对齐，所以一个面板可以并发扇出到两个源并合并结果行。

封装 [qqmusic-api-python](https://pypi.org/project/qqmusic-api-python/)。

## 安装

```bash
pipx install git+https://github.com/BrandNewJimZhang/qq-cli
```

或从源码检出安装：

```bash
pipx install .          # or: pip install .
```

## 发布产物

`scripts/build-artifact.sh` 把独立可执行文件打包到 `dist/`，并打印
marketplace 条目所需的平台标识与 sha256：

```bash
scripts/build-artifact.sh
```

选 PyInstaller onefile 而不是 zipapp，因为目标主机不假设装有任何
Python —— 商店交付二进制的意义正在于此。它捆绑当前运行的解释器，
所以无法交叉编译：每个平台的产物都在该平台上构建。

## 动词

```bash
qq-cli search --keyword "周杰伦 稻香" --limit 20
qq-cli whoami
qq-cli url    --id 003aAYrm3GE0Ac [--quality lossless|high|standard]
qq-cli lyric  --id 003aAYrm3GE0Ac

qq-cli playlists
qq-cli playlist --id 7364061161 [--limit 50]
qq-cli daily

qq-cli login start --type qq
qq-cli login poll  --identifier <token from start>
qq-cli refresh
```

所有解析动词接受的 `--id` 是 `search` 返回的曲目 **mid**（`url` 与
`lyric` 都以 mid 为键，所以 search 发布的就是它）。

## 契约

```json
{"schema_version": 2, "data": ...}
```

| 动词           | `data` 形状 |
|----------------|--------------|
| `search`       | `[{id, title, artist, album, cover, duration}]` —— `duration` 为**毫秒**（QQ 上报的是秒；已归一化以对齐 netease-cli），`cover` 为专辑封面 URL 或 `""`，多位歌手以 ` / ` 连接 |
| `url`          | `{id, url, quality, bitrate}` —— `quality` 是实际应答的档位（`lossless` / `high` / `standard`），`bitrate` 是你实际获得的 kbps |
| `lyric`        | `{id, lrc}` —— LRC 文档，曲目无歌词时为 `""` |
| `whoami`       | `{logged_in, nickname, vip}` —— 服务端校验的会话裁决（匿名与被拒凭据都应答 `logged_in: false`） |
| `playlists`    | `[{id, title, cover, count, description}]` —— 该账号自己的歌单书架 |
| `playlist`     | `[{id, title, artist, album, cover, duration}]` —— 与 `search` 发布的同一行形状，书架无需第二种形状即可播放、入队 |
| `daily`        | 同样的曲目行 —— 今日推荐 |
| `login start`  | `{identifier, login_type, mimetype, image_base64}` —— 要渲染的二维码与轮询它的 token |
| `login poll`   | `{state, credential}` —— `state` 为 `pending` / `scanned` / `done` / `expired` / `refused` 之一；`credential` 是可存储的凭据块，仅在 `done` 时非空 |
| `refresh`      | 续期后的凭据块，与 `login poll` 在 `done` 时发布的形状相同 |

失败在 stderr 打印一行并以非零退出：

```json
{"error_class": "upstream_rejected", "message": "..."}
```

| 退出码 | 含义 |
|------|---------|
| 0    | 成功 |
| 3    | 输入错误（标志缺失/非法、未知命令、凭据不可用） |
| 4    | 上游拒绝（无可播放 url、传输或 API 失败） |

| `error_class`               | 退出码 | 含义 |
|-----------------------------|------|---------|
| `bad_input`                 | 3    | 标志格式错误或未知登录类型 |
| `bad_credential`            | 3    | `QQ_MUSIC_CREDENTIAL` 不是合法的凭据文档 |
| `credential_missing`        | 3    | 未登录会话下调用 `refresh` / `playlists` / `daily` |
| `credential_not_refreshable`| 3    | 手工粘贴的键值对不携带续期 token |
| `credential_expired`        | 4    | 上游拒绝续期 —— 请重新登录 |
| `upstream_rejected`         | 4    | 无可播放 url、传输或 API 失败 |

## 凭据

搜索与歌词匿名可用。**取流 URL 不行**：匿名请求 QQ 应答
`result=104003` 且 `purl` 为空。

接受两种形状，持有哪一种决定了密钥能否续期：

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

两者都导出时凭据块胜出：它是键值对的超集。

### 扫码登录

`login start` 铸造二维码并发布其 `identifier`；`login poll` 对该
identifier 查询一次并应答一个状态。identifier 就是轮询状态的全部
—— 上游的状态检查不读其他任何东西 —— 所以两个动词可以作为独立
进程运行，这正是让调用方渲染二维码、把控制权交还自己的事件循环、
按自己的节奏轮询的原因。轮询直到 `done`（保存凭据）、`expired` 或
`refused`（重新铸码）。

## 范围与边界

- **音质档位。** `--quality` 指的是档位（TIER），永远不是码率：该
  上游只暴露文件类型、没有码率旋钮，所以 bps 不可能成为共享词汇。
  请求从指定档位起步、只会向下回落 —— 请求 `standard` 永远不会被
  静默升档。匿名会话封顶 `standard`（付费档位在无凭据时总是应答为
  空，试探它们只是白烧请求）。
- **名义码率。** `bitrate` 是该档位的名义 kbps，因为 QQ 应答的是
  文件类型而不是实测速率。netease-cli 发布上游实测值 —— 两边这个
  字段都表示「你获得的 kbps」，但只有一边是实测的。
- **账号域动词。** `playlists` 与 `daily` 描述的是某一个账号，所以
  匿名会话会被点名拒绝而不是应答一个空书架 —— 「你没有收藏」和
  「我们不知道你是谁」不能看起来一样。`playlist` 接受公开 id，未登录
  可用。
- **不解锁。** 该账号无权播放的曲目应答退出码 4。本 resolver 绝不
  绕行一个已被拒绝的权益。
- **扫码通道。** `--type` 接受 `qq` / `wx` / `mobile`；只有 `qq`
  经过端到端验证。另外两个到达同一个上游调用，按接口发布，未经实测
  验证。
- **逆向实现。** QQ 音乐不发布面向个人使用的 API；上游随时可能变更
  或失效。
- **仅行级歌词。** 不发布 `qrc` 字级字段。

## 法律声明

非官方项目，与 QQ 音乐或腾讯无从属或背书关系。仅限个人、非商业
使用。它以你的身份认证，读取官方客户端服务于你自己账号的相同端点
—— 在账号权益之内，绝不绕过权益（见上文「不解锁」）。不捆绑、
不缓存、不再分发任何音频；CLI 发布的是服务为你的会话铸造的 URL。
你有责任遵守所在司法辖区内该服务的条款。权利方可通过 issue 提出
下架请求，维护者将及时配合。

## 许可证

[MIT](LICENSE)

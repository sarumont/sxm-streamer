# sxm-streamer

Lightweight SiriusXM streaming server. Serves HLS pass-through and transcoded MP3 streams on a single port.

Built on [sxm](https://pypi.org/project/sxm/) — a simplified alternative to [sxm-player](https://github.com/AngellusMortworker/sxm-player) that eliminates the multiprocessing architecture in favor of a single async process.

## Quick Start (Docker)

```bash
cp .env.example .env
# Edit .env with your SXM credentials

podman build -t sxm-streamer .
podman run --rm -it --env-file .env -p 9999:9999 sxm-streamer
```

Or with docker-compose:

```bash
docker-compose up -d
```

## Endpoints

| Endpoint | Description |
|---|---|
| `GET /{channel}.m3u8` | HLS playlist (pass-through from SXM) |
| `GET /{channel}.mp3` | MP3 audio stream (ffmpeg transcoding) |
| `GET /channels/` | Channel list (JSON) |
| `GET /now-playing/{channel}.json` | Now playing metadata (JSON) |

### Query Parameters

- `?bitrate=128k` — Override MP3 output bitrate (default derived from `--quality`)

### ICY Metadata

MP3 clients that send `Icy-MetaData: 1` will receive inline ICY metadata with artist/title information.

## Configuration

| Env Var | CLI Flag | Default | Description |
|---|---|---|---|
| `SXM_USERNAME` | `--username` | *(required)* | SXM username |
| `SXM_PASSWORD` | `--password` | *(required)* | SXM password |
| `SXM_PORT` | `--port` | `9999` | Server port |
| `SXM_HOST` | `--host` | `0.0.0.0` | Bind address |
| `SXM_REGION` | `--region` | `US` | SXM region (US, CA) |
| `SXM_QUALITY` | `--quality` | `LARGE_256k` | Stream quality |
| `SXM_PRECACHE` | `--no-precache` | `true` | Precache AAC chunks |
| `SXM_DEBUG` | `--verbose` | `false` | Debug logging |

## Architecture

Single async process using aiohttp. One port serves everything:

```
Client → aiohttp (:9999)
           ├── /*.m3u8, *.aac, /key/1, /channels/ → sxm.make_http_handler (HLS pass-through)
           ├── /*.mp3                              → ffmpeg HLS→MP3 transcoding + ICY metadata
           ├── /now-playing/*.json                 → now-playing JSON
           └── /                                   → index page
```

ffmpeg reads HLS from `http://127.0.0.1:{port}/{channel}.m3u8` (loopback to the same server). The `SXMClientAsync.update_handler` callback updates now-playing metadata directly in-process — no queues, no polling.

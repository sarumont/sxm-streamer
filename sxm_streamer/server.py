"""StreamServer: single-process aiohttp server for SXM HLS + MP3 streaming."""

import asyncio
import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Dict, Optional

from aiohttp import web
from sxm import QualitySize, make_http_handler
from sxm.models import XMLiveChannel, XMSong

log = logging.getLogger(__name__)

QUALITY_BITRATE_MAP = {
    QualitySize.SMALL_64k: "64k",
    QualitySize.MEDIUM_128k: "128k",
    QualitySize.LARGE_256k: "256k",
}

ICY_INTERVAL = 16000  # bytes between metadata blocks


@dataclass
class NowPlaying:
    title: str = ""
    artist: str = ""
    art_url: str = ""
    channel_name: str = ""
    updated_at: float = field(default_factory=time.monotonic)


class StreamServer:
    """Serves HLS (pass-through) and MP3 (ffmpeg transcoding + ICY) on one port."""

    def __init__(self, client, *, quality, port, host, precache):
        self._client = client
        self._quality = quality
        self._port = port
        self._host = host
        self._precache = precache

        # ffmpeg always connects via loopback
        self._loopback = "127.0.0.1" if host == "0.0.0.0" else host

        self._active_processes: Dict[int, asyncio.subprocess.Process] = {}
        self._now_playing: Dict[str, NowPlaying] = {}

    # -- Metadata handling (update_handler callback) --

    def handle_metadata_update(self, data: dict) -> None:
        """Called by SXMClientAsync when an HLS playlist is fetched."""

        log.debug("Raw metadata update:\n%s", json.dumps(data, indent=2, default=str))

        try:
            live = XMLiveChannel.from_dict(data)
        except Exception as e:
            log.warning(f"Failed to parse live channel data: {e}")
            return

        channel_id = live.id
        now = datetime.now(timezone.utc)

        if live.tune_time is not None:
            time_offset = now - live.tune_time
            radio_time = now - time_offset
        else:
            radio_time = now

        latest_cut = live.get_latest_cut(radio_time)

        if latest_cut:
            cut = latest_cut.cut
            log.debug(
                "Channel %s latest_cut: type=%s, cut_type=%s, title=%r, "
                "cut attrs=%s",
                channel_id,
                type(cut).__name__,
                getattr(cut, "cut_type", None),
                getattr(cut, "title", None),
                [a for a in dir(cut) if not a.startswith("_")],
            )
            if isinstance(cut, XMSong):
                log.debug(
                    "  Song detail: artists=%r, album=%r, "
                    "album.arts=%r, itunes_id=%r",
                    cut.artists,
                    cut.album,
                    cut.album.arts if cut.album else None,
                    getattr(cut, "itunes_id", None),
                )
        else:
            log.debug("Channel %s: no latest_cut at radio_time=%s", channel_id, radio_time)

        if latest_cut and isinstance(latest_cut.cut, XMSong):
            song = latest_cut.cut
            artist = (
                ", ".join(a.name for a in song.artists) if song.artists else ""
            )
            art_url = ""
            if song.album and song.album.arts:
                art_url = song.album.arts[0].url
            if not art_url:
                art_url = self._extract_cut_art(data, latest_cut.guid)
            self._now_playing[channel_id] = NowPlaying(
                title=song.title,
                artist=artist,
                art_url=art_url,
                channel_name=channel_id,
                updated_at=time.monotonic(),
            )
        elif latest_cut:
            self._now_playing[channel_id] = NowPlaying(
                title=latest_cut.cut.title,
                artist="",
                channel_name=channel_id,
                updated_at=time.monotonic(),
            )

    @staticmethod
    def _extract_cut_art(data: dict, guid: str) -> str:
        """Fallback: extract art URL from raw cut-level creativeArts."""
        try:
            markers = (
                data.get("moduleResponse", {})
                .get("liveChannelData", {})
                .get("cutMarker", [])
            )
            for marker in markers:
                if marker.get("assetGUID") != guid:
                    continue
                for art in marker.get("cut", {}).get("creativeArts", []):
                    if art.get("type") == "IMAGE" and art.get("url"):
                        return art["url"]
        except (KeyError, TypeError):
            pass
        return ""

    # -- Helpers --

    def _resolve_bitrate(self, override: Optional[str] = None) -> str:
        if override:
            return override
        return QUALITY_BITRATE_MAP.get(self._quality, "128k")

    def _build_ffmpeg_cmd(self, hls_url: str, bitrate: str) -> list:
        return [
            "ffmpeg",
            "-loglevel", "warning",
            "-re",
            "-f", "hls",
            "-i", hls_url,
            "-c:a", "libmp3lame",
            "-b:a", bitrate,
            "-f", "mp3",
            "pipe:1",
        ]

    def _build_icy_block(self, channel_id: str) -> bytes:
        """Build an ICY metadata block for the given channel."""
        np = self._now_playing.get(channel_id)
        if np and (time.monotonic() - np.updated_at) < 60:
            if np.artist:
                stream_title = f"{np.artist} - {np.title}"
            else:
                stream_title = np.title
        else:
            stream_title = f"SiriusXM - {channel_id}"

        stream_url = ""
        if np and np.art_url and (time.monotonic() - np.updated_at) < 60:
            stream_url = np.art_url

        meta_str = f"StreamTitle='{stream_title}';"
        if stream_url:
            meta_str += f"StreamUrl='{stream_url}';"
        meta_bytes = meta_str.encode("utf-8")

        # Length byte = ceil(len / 16), actual block padded to length * 16
        length = (len(meta_bytes) + 15) // 16
        if length > 255:
            length = 255
            meta_bytes = meta_bytes[: 255 * 16]

        return bytes([length]) + meta_bytes.ljust(length * 16, b"\x00")

    # -- HTTP handlers --

    async def _handle_mp3_stream(self, request: web.Request) -> web.StreamResponse:
        """Handle GET /{channel}.mp3 -- spawn ffmpeg and stream MP3."""

        channel_id = request.match_info.get("channel", "")
        if not channel_id:
            return web.Response(status=400, text="Missing channel ID")

        bitrate_override = request.query.get("bitrate")
        resolved_bitrate = self._resolve_bitrate(bitrate_override)
        icy_requested = request.headers.get("Icy-MetaData") == "1"

        hls_url = f"http://{self._loopback}:{self._port}/{channel_id}.m3u8"
        ffmpeg_cmd = self._build_ffmpeg_cmd(hls_url, resolved_bitrate)

        log.info(
            f"Starting MP3 stream for '{channel_id}' at {resolved_bitrate} "
            f"(client: {request.remote}, icy: {icy_requested})"
        )

        process: Optional[asyncio.subprocess.Process] = None
        try:
            process = await asyncio.create_subprocess_exec(
                *ffmpeg_cmd,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )

            pid = process.pid
            if pid is not None:
                self._active_processes[pid] = process

            # Give ffmpeg a moment to fail on invalid channels
            await asyncio.sleep(1.0)

            if process.returncode is not None:
                stderr_output = b""
                if process.stderr:
                    stderr_output = await process.stderr.read()
                log.warning(
                    f"ffmpeg exited immediately for '{channel_id}': "
                    f"{stderr_output.decode('utf-8', errors='replace').strip()}"
                )
                return web.Response(
                    status=404,
                    text=f"Channel '{channel_id}' not found or unavailable",
                )

            response_headers = {
                "Content-Type": "audio/mpeg",
                "Cache-Control": "no-cache, no-store",
                "Connection": "keep-alive",
                "icy-name": f"SiriusXM - {channel_id}",
            }

            if icy_requested:
                response_headers["icy-metaint"] = str(ICY_INTERVAL)
                response_headers["icy-br"] = resolved_bitrate.replace("k", "")
                response_headers["icy-pub"] = "0"

            response = web.StreamResponse(status=200, headers=response_headers)
            await response.prepare(request)

            assert process.stdout is not None

            if not icy_requested:
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break
                    await response.write(chunk)
            else:
                bytes_since_meta = 0
                while True:
                    chunk = await process.stdout.read(4096)
                    if not chunk:
                        break

                    pos = 0
                    while pos < len(chunk):
                        remaining = ICY_INTERVAL - bytes_since_meta
                        available = len(chunk) - pos

                        if available <= remaining:
                            await response.write(chunk[pos:])
                            bytes_since_meta += available
                            pos = len(chunk)
                        else:
                            await response.write(chunk[pos : pos + remaining])
                            pos += remaining
                            bytes_since_meta = 0

                            meta_block = self._build_icy_block(channel_id)
                            await response.write(meta_block)

            return response

        except (ConnectionResetError, asyncio.CancelledError):
            log.info(f"Client disconnected from '{channel_id}'")
            raise
        except Exception as e:
            log.error(f"Error streaming '{channel_id}': {e}")
            return web.Response(status=500, text="Internal server error")
        finally:
            if process is not None:
                pid = process.pid
                try:
                    process.kill()
                except ProcessLookupError:
                    pass
                await process.wait()
                if pid is not None:
                    self._active_processes.pop(pid, None)
                log.debug(f"Cleaned up ffmpeg (pid={pid}) for '{channel_id}'")

    async def _handle_now_playing(self, request: web.Request) -> web.Response:
        """GET /now-playing/{channel}.json"""
        channel_id = request.match_info.get("channel", "")
        np = self._now_playing.get(channel_id)
        if np:
            return web.json_response({
                "channel": channel_id,
                "title": np.title,
                "artist": np.artist,
                "art_url": np.art_url or None,
                "stale": (time.monotonic() - np.updated_at) > 60,
            })
        return web.json_response({
            "channel": channel_id,
            "title": None,
            "artist": None,
        })

    async def _handle_index(self, request: web.Request) -> web.Response:
        """Simple index page listing available endpoints."""
        return web.Response(
            status=200,
            text=(
                "sxm-streamer\n"
                "============\n\n"
                "Endpoints:\n"
                "  GET /{channel}.m3u8              - HLS playlist (pass-through)\n"
                "  GET /{channel}.mp3               - MP3 audio stream\n"
                "  GET /channels/                   - Channel list (JSON)\n"
                "  GET /now-playing/{channel}.json   - Now playing (JSON)\n\n"
                "Query params:\n"
                "  ?bitrate=128k  - Override MP3 output bitrate\n\n"
                "Example:\n"
                f"  http://{self._loopback}:{self._port}/octane.mp3\n"
            ),
            headers={"Content-Type": "text/plain"},
        )

    # -- App lifecycle --

    def create_app(self) -> web.Application:
        """Build the aiohttp Application with all routes."""

        app = web.Application()

        # Specific routes first (matched before catch-all)
        app.router.add_get("/", self._handle_index)
        app.router.add_get("/now-playing/{channel}.json", self._handle_now_playing)
        app.router.add_get("/{channel}.mp3", self._handle_mp3_stream)

        # Catch-all: HLS pass-through via sxm library
        app.router.add_get(
            "/{path:.*}",
            make_http_handler(self._client, precache=self._precache),
        )

        app.on_startup.append(self._on_startup)
        app.on_shutdown.append(self._on_shutdown)

        return app

    async def _on_startup(self, app: web.Application) -> None:
        log.info("Authenticating with SiriusXM...")
        await self._client.authenticate()
        log.info(
            f"Authenticated. Serving on http://{self._host}:{self._port} "
            f"(quality: {self._quality.name}, bitrate: {self._resolve_bitrate()})"
        )

    async def _on_shutdown(self, app: web.Application) -> None:
        for pid, process in list(self._active_processes.items()):
            log.info(f"Killing ffmpeg process (pid={pid})")
            try:
                process.kill()
            except ProcessLookupError:
                pass
            await process.wait()
        self._active_processes.clear()

"""CLI entry point for sxm-streamer."""

import logging

import typer
from aiohttp import web
from sxm import QualitySize, RegionChoice, SXMClientAsync

from .server import StreamServer

app = typer.Typer(add_completion=False)


@app.command()
def main(
    username: str = typer.Option(
        ..., "--username", "-U", prompt=True, envvar="SXM_USERNAME",
        help="SXM username",
    ),
    password: str = typer.Option(
        ..., "--password", "-P", prompt=True, hide_input=True, envvar="SXM_PASSWORD",
        help="SXM password",
    ),
    port: int = typer.Option(
        9999, "--port", "-p", envvar="SXM_PORT",
        help="Port to run server on",
    ),
    host: str = typer.Option(
        "0.0.0.0", "--host", "-h", envvar="SXM_HOST",  # nosec
        help="IP to bind server to",
    ),
    region: RegionChoice = typer.Option(
        RegionChoice.US, "--region", "-r", envvar="SXM_REGION",
        help="SXM client region",
    ),
    quality: QualitySize = typer.Option(
        QualitySize.LARGE_256k, "--quality", "-q", envvar="SXM_QUALITY",
        help="Stream quality (SMALL_64k, MEDIUM_128k, LARGE_256k)",
    ),
    precache: bool = typer.Option(
        True, "--no-precache", "-n", envvar="SXM_PRECACHE",
        help="Turn off precaching AAC chunks",
    ),
    verbose: bool = typer.Option(
        False, "--verbose", "-v", envvar="SXM_DEBUG",
        help="Enable debug logging",
    ),
):
    """Lightweight SiriusXM streaming server.

    Serves HLS pass-through and transcoded MP3 streams on a single port.
    """

    logging.basicConfig(
        level=logging.DEBUG if verbose else logging.INFO,
        format="%(asctime)s [%(name)s] %(levelname)s: %(message)s",
    )

    client = SXMClientAsync(
        username=username,
        password=password,
        region=region,
        quality=quality,
    )

    server = StreamServer(
        client, quality=quality, port=port, host=host, precache=precache,
    )
    client.update_handler = server.handle_metadata_update

    web.run_app(server.create_app(), host=host, port=port, print=None)

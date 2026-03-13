"""Entry point for the Reolink Enhanced ONVIF Proxy."""

import argparse
import asyncio
import logging
import signal
import sys

from aiohttp import web

from .config import load_config
from .onvif_server import ONVIFServer
from .reolink_api import ReolinkAPI

logger = logging.getLogger("reolink_onvif_proxy")


async def start_proxy(config_path: str):
    """Start the proxy with all configured cameras."""
    config = load_config(config_path)

    runners: list[web.AppRunner] = []
    apis: list[ReolinkAPI] = []

    for cam in config.cameras:
        api = ReolinkAPI(cam.host, cam.port)
        apis.append(api)

        server = ONVIFServer(cam, api)
        app = server.create_app()
        runner = web.AppRunner(app)
        await runner.setup()

        site = web.TCPSite(runner, "0.0.0.0", cam.listen_port)
        await site.start()

        logger.info(
            "Camera '%s' (%s:%d) → ONVIF proxy on port %d",
            cam.name,
            cam.host,
            cam.port,
            cam.listen_port,
        )
        runners.append(runner)

    logger.info("Proxy started with %d camera(s)", len(config.cameras))

    # Wait for shutdown signal
    stop_event = asyncio.Event()

    def _signal_handler():
        logger.info("Shutdown signal received")
        stop_event.set()

    loop = asyncio.get_running_loop()
    for sig in (signal.SIGINT, signal.SIGTERM):
        loop.add_signal_handler(sig, _signal_handler)

    await stop_event.wait()

    # Cleanup
    logger.info("Shutting down...")
    for runner in runners:
        await runner.cleanup()
    for api in apis:
        await api.close()


def main():
    parser = argparse.ArgumentParser(description="Reolink Enhanced ONVIF Proxy")
    parser.add_argument(
        "-c", "--config",
        default="/config.yml",
        help="Path to configuration file (default: /config.yml)",
    )
    parser.add_argument(
        "-v", "--verbose",
        action="store_true",
        help="Enable debug logging",
    )
    args = parser.parse_args()

    logging.basicConfig(
        level=logging.DEBUG if args.verbose else logging.INFO,
        format="%(asctime)s %(levelname)s [%(name)s] %(message)s",
        stream=sys.stdout,
    )

    try:
        asyncio.run(start_proxy(args.config))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()

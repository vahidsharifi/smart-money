from __future__ import annotations

import asyncio
import logging
import signal


def install_shutdown_handlers(stop_event: asyncio.Event, logger: logging.Logger | None = None) -> None:
    loop = asyncio.get_running_loop()

    def _handle_signal(sig: signal.Signals) -> None:
        if logger:
            logger.info("shutdown_signal_received signal=%s", sig.name)
        stop_event.set()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _handle_signal, sig)
        except NotImplementedError:
            signal.signal(sig, lambda *_: _handle_signal(sig))

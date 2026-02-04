import asyncio
import logging

from app.config import settings, validate_chain_config
from app.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


async def run_worker() -> None:
    validate_chain_config()
    logger.info("listener_started", extra={"chains": list(settings.chain_config.keys())})
    while True:
        await asyncio.sleep(5)
        logger.debug("listener_heartbeat")


if __name__ == "__main__":
    asyncio.run(run_worker())

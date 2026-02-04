import asyncio
import logging

from app.config import validate_chain_config
from app.logging import configure_logging

configure_logging()
logger = logging.getLogger(__name__)


async def run_worker() -> None:
    validate_chain_config()
    logger.info("risk_worker_started")
    while True:
        await asyncio.sleep(5)
        logger.debug("risk_worker_heartbeat")


if __name__ == "__main__":
    asyncio.run(run_worker())

import logging
import os
import socket


def setup_logging(service: str) -> None:
    host = socket.gethostname()
    logging.basicConfig(
        level=os.getenv("LOG_LEVEL", "INFO"),
        format=f"%(asctime)s [{service}@{host}] %(levelname)s %(name)s: %(message)s",
    )

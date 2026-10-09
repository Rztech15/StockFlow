"""Worker entry point (same image, different command: `python -m app.worker`).
Phase 0 registers no jobs and adds no queue library; it only proves config + shutdown handling."""

import logging
import signal
import threading

from app.config import load_settings_or_exit

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger("stockflow.worker")


def main() -> None:
    settings = load_settings_or_exit()
    log.info("worker started; no jobs registered in Phase 0 (config: %s)", settings.redacted())
    stop = threading.Event()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: stop.set())
    stop.wait()
    log.info("worker stopped")


if __name__ == "__main__":
    main()

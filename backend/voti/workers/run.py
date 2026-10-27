"""Worker entry point: `python -m voti.workers.run --queues fetch,audio`.

Handler modules are imported individually and guarded, so a worker without optional dependencies
(e.g. no torch on a fetch-only box) still serves the queues whose handlers did load.
"""
from __future__ import annotations

import argparse
import importlib
import logging
import os
import sys

HANDLER_MODULES = ["voti.sources.handlers", "voti.pipeline.handlers", "voti.pipeline.profile", "voti.datasets"]


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="voti.workers.run")
    ap.add_argument("--queues", default=os.environ.get("VOTI_QUEUES", "fetch,audio,ml,export"),
                    help="comma separated: fetch,audio,ml,export")
    ap.add_argument("--log-level", default=os.environ.get("LOG_LEVEL", "INFO"))
    args = ap.parse_args(argv)
    logging.basicConfig(level=args.log_level.upper(), stream=sys.stdout,
                        format="%(asctime)s %(levelname)s %(name)s: %(message)s")
    log = logging.getLogger("voti.worker")

    from voti import queue

    queues = [q.strip() for q in args.queues.split(",") if q.strip()]
    unknown = set(queues) - set(queue.KINDS.values())
    if unknown:
        log.error("unknown queues: %s", ", ".join(sorted(unknown)))
        return 2
    for mod in HANDLER_MODULES:
        try:
            importlib.import_module(mod)
            log.info("loaded handlers from %s", mod)
        except Exception as e:  # noqa: BLE001 - ImportError or a module failing at import time
            log.warning("handlers from %s not loaded: %s: %s", mod, type(e).__name__, e)
    served = sorted(k for k, q in queue.KINDS.items() if q in queues)
    missing = [k for k in served if k not in queue.HANDLERS]
    log.info("queues %s: handlers for %s", queues, ", ".join(k for k in served if k in queue.HANDLERS) or "-")
    if missing:
        log.warning("no handler on this worker for: %s (such jobs fail with no_handler)", ", ".join(missing))
    try:
        from voti.storage import ensure_bucket
        ensure_bucket()
    except Exception as e:  # noqa: BLE001
        log.warning("bucket check failed: %s", e)
    queue.worker_loop(queues)
    return 0


if __name__ == "__main__":
    sys.exit(main())

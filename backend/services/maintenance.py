"""Background housekeeping the app runs on its own.

A tailoring run whose worker died is only noticed when somebody opens that run's page, so a
run nobody revisits stays `running` forever and the user is left watching a spinner that
will never resolve. `sweep-tailoring-runs` fixes that, but a command nothing calls is a
command that never runs.

This is an in-process timer rather than a cron entry because there is nowhere to put a cron
entry yet — deploy is deliberately last. When there is somewhere, the command is still the
real interface and this can be switched off with `MAINTENANCE_SWEEP=0`.

Safe to run in several processes at once: the sweep is a single conditional UPDATE, so the
worst case is one of them updating nothing.
"""

import logging
import os
from config import env_flag, env_int
import threading

logger = logging.getLogger(__name__)

# a run is abandoned after 3 minutes of silence, so checking every few minutes is enough to
# keep the wait short without waking up constantly
SWEEP_INTERVAL_SECONDS = env_int("MAINTENANCE_SWEEP_SECONDS", 300)
SWEEP_ENABLED = env_flag("MAINTENANCE_SWEEP", default=True)

_started = False
_lock = threading.Lock()


def sweep_once():
    """One pass. Returns the ids closed, and never raises — a failed sweep is not worth
    taking the process down for, and the next tick will try again."""
    from db import get_cursor
    from services.tailoring_agent import sweep_abandoned_runs

    try:
        with get_cursor(commit=True) as cur:
            swept = sweep_abandoned_runs(cur)
        if swept:
            logger.info("closed %d abandoned tailoring run(s): %s", len(swept), ", ".join(swept))
        return swept
    except Exception:
        logger.exception("the maintenance sweep failed (it will run again)")
        return []


def _loop(stop):
    # wait first: at boot the database may not be reachable yet, and a run started seconds
    # ago is not abandoned
    while not stop.wait(SWEEP_INTERVAL_SECONDS):
        sweep_once()


def start(app=None):
    """Start the sweeper once per process. Silently does nothing when disabled or already
    running, so importing the app twice cannot start two of them."""
    global _started
    if not SWEEP_ENABLED:
        return None
    if app is not None and app.config.get("TESTING"):
        return None

    with _lock:
        if _started:
            return None
        _started = True

    stop = threading.Event()
    thread = threading.Thread(
        target=_loop, args=(stop,), name="maintenance-sweep", daemon=True,
    )
    thread.start()
    logger.info("maintenance sweep running every %ds", SWEEP_INTERVAL_SECONDS)
    return stop

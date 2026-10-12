"""Keep a free Render instance awake.

Render's free web services sleep after 15 minutes with no incoming requests and take
about a minute to wake. While the app runs on Render, a background thread requests the
app's own public /healthz every 10 minutes; that request comes back in through Render's
front door, so it counts as a visitor and the service never goes to sleep. /healthz
doesn't touch the database, so the (separately billed) database can still idle.

Render sets RENDER_EXTERNAL_URL automatically. Elsewhere set PMM_KEEP_AWAKE_URL, or
PMM_KEEP_AWAKE=0 to turn this off. One always-on free instance uses about 744 of
Render's 750 free hours a month.
"""
import os
import threading
import time
import urllib.request

_started = False
_lock = threading.Lock()
INTERVAL_SECONDS = 10 * 60


def ping_url() -> str | None:
    if os.environ.get("PMM_KEEP_AWAKE", "1") == "0":
        return None
    base = os.environ.get("PMM_KEEP_AWAKE_URL") or os.environ.get("RENDER_EXTERNAL_URL")
    return base.rstrip("/") + "/healthz" if base else None


def ping(url: str, logger=None) -> bool:
    try:
        with urllib.request.urlopen(url, timeout=30) as resp:
            return resp.status == 200
    except Exception as exc:  # noqa: BLE001 — a failed ping is logged and retried next time
        if logger:
            logger.warning("keep-awake ping to %s failed: %s", url, exc)
        return False


def start(app) -> bool:
    """Start the pinger once per worker process. Returns True if a thread was started."""
    global _started
    url = ping_url()
    if not url or app.config.get("TESTING"):
        return False
    with _lock:
        if _started:
            return False
        _started = True

    interval = int(os.environ.get("PMM_KEEP_AWAKE_SECONDS", INTERVAL_SECONDS))

    def loop():
        while True:
            time.sleep(interval)
            ping(url, app.logger)

    threading.Thread(target=loop, name="keep-awake", daemon=True).start()
    app.logger.info("keep-awake: pinging %s every %d seconds", url, interval)
    return True

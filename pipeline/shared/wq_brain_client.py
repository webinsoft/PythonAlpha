"""
WQ Brain Platform Client — unified API interaction module.

Covers all platform interactions for the pipeline:
  - Authentication & credential management
  - Session lifecycle (thread-safe, auto-refresh on expiry)
  - Rate-limit handling (429 / SIMULATION_LIMIT_EXCEEDED)
  - Simulation submission (dead-loop retry) + polling (Retry-After)
  - Alpha detail fetch (exponential backoff)
  - Generalized data-fetching with rate-limit backoff

Robustness patterns ported from platform_backtest.py (resimulate_alphas.py lineage):
  - Dead-loop submission: infinite retry on 429, only fatal on non-recoverable errors
  - Session auto-refresh on 401 expiry
  - Retry-After header parsing in polling
  - Terminal state detection (COMPLETE / ERROR / FAIL / CANCELLED / WARNING)
  - Exponential backoff on transient network errors
"""

import os
import time
import threading
import logging
import requests
import dotenv
from pathlib import Path
from typing import Optional, Tuple, Dict, Any

# ---------------------------------------------------------------------------
# Environment & constants
# ---------------------------------------------------------------------------

dotenv.load_dotenv(Path.cwd() / ".env")

BRAIN_API_URL = os.environ.get("BRAIN_API_URL", "https://api.worldquantbrain.com").rstrip("/")

logger = logging.getLogger("wq_brain_client")
logger.setLevel(logging.INFO)
if not logger.handlers:
    _h = logging.StreamHandler()
    _h.setFormatter(logging.Formatter("%(asctime)s [%(levelname)s] %(message)s"))
    logger.addHandler(_h)


# ---------------------------------------------------------------------------
# Credentials
# ---------------------------------------------------------------------------

def get_brain_credentials() -> Tuple[Optional[str], Optional[str]]:
    """Retrieve Brain credentials from environment variables.

    Checks in order: BRAIN_EMAIL → EMAIL → WQB_EMAIL
                      BRAIN_PASSWORD → PASSWORD → WQB_PASSWORD
    """
    email = (
        os.environ.get("BRAIN_EMAIL")
        or os.environ.get("EMAIL")
        or os.environ.get("WQB_EMAIL")
    )
    password = (
        os.environ.get("BRAIN_PASSWORD")
        or os.environ.get("PASSWORD")
        or os.environ.get("WQB_PASSWORD")
    )
    return email, password


# ---------------------------------------------------------------------------
# SessionManager — thread-safe, auto-refresh
# ---------------------------------------------------------------------------

class SessionManager:
    """Thread-safe session wrapper with auto-refresh on expiry.

    Ported from platform_backtest.py (originally from resimulate_alphas.py).

    Parameters
    ----------
    session : requests.Session
        Authenticated session.
    start_time : float
        ``time.time()`` at session creation.
    expiry_time : int
        Session lifetime in seconds (default 3 hours).
    """

    def __init__(self, session: requests.Session, start_time: float, expiry_time: int = 10800):
        self.session = session
        self.start_time = start_time
        self.expiry_time = expiry_time
        self._lock = threading.Lock()

    def refresh_session(self):
        """Re-authenticate and replace the current session (thread-safe)."""
        with self._lock:
            if self.session:
                try:
                    self.session.close()
                except Exception:
                    pass
            logger.info("Session expired or invalid, re-authenticating...")
            self.session = _login()
            self.start_time = time.time()

    def ensure_valid(self):
        """Check session validity and refresh if expired."""
        if not self.session or (time.time() - self.start_time > self.expiry_time):
            self.refresh_session()


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------

def _login() -> requests.Session:
    """Create and authenticate a new requests.Session.

    Returns
    -------
    requests.Session
        Authenticated session (Basic Auth).

    Raises
    ------
    ValueError
        If credentials are missing.
    SystemExit
        If authentication fails.
    """
    email, password = get_brain_credentials()
    if not email or not password:
        raise ValueError(
            "WQ Brain credentials missing. Set BRAIN_EMAIL/BRAIN_PASSWORD "
            "in .env or environment variables."
        )

    s = requests.Session()
    s.auth = (email, password)
    try:
        resp = s.post(f"{BRAIN_API_URL}/authentication")
        resp.raise_for_status()
        logger.info("WQ Brain authentication successful.")
    except requests.RequestException as e:
        logger.error(f"Authentication failed: {e}")
        raise SystemExit(1) from e
    return s


def create_session() -> SessionManager:
    """Authenticate and return a managed session.

    Convenience wrapper: ``SessionManager(_login(), time.time())``.
    """
    return SessionManager(_login(), time.time())


def authenticate_session() -> Tuple[bool, Optional[SessionManager], str]:
    """Test authentication and return a managed session.

    Used by Node 00 (pre-authentication gate).

    Returns
    -------
    (authenticated: bool, session_manager: SessionManager | None, error_message: str)
    """
    email, password = get_brain_credentials()
    if not email or not password:
        return False, None, (
            "Credentials missing in .env. "
            "Please set BRAIN_EMAIL/BRAIN_PASSWORD or EMAIL/PASSWORD."
        )

    try:
        sm = create_session()
        return True, sm, ""
    except SystemExit:
        return False, None, "Authentication failed (SystemExit). Check credentials."
    except Exception as e:
        return False, None, f"Authentication error: {e}"


# ---------------------------------------------------------------------------
# Generalized request with rate-limit backoff
# ---------------------------------------------------------------------------

def make_request(
    sm: SessionManager,
    method: str,
    url: str,
    max_retries: int = 10,
    **kwargs,
) -> requests.Response:
    """Perform an authenticated request with exponential backoff on 429.

    For data-fetching operations (data-sets, data-fields, alpha details).
    Not intended for simulation submission — use :func:`create_simulation` for that.

    Parameters
    ----------
    sm : SessionManager
        Managed session (auto-refreshed on 401).
    method : str
        HTTP method (GET, POST, etc.).
    url : str
        Full URL.
    max_retries : int
        Maximum retry attempts (default 10).
    **kwargs
        Passed to ``requests.Session.request()``.

    Returns
    -------
    requests.Response

    Raises
    ------
    RuntimeError
        If all retries are exhausted.
    """
    timeout = kwargs.pop("timeout", 60)
    for attempt in range(max_retries):
        sm.ensure_valid()
        try:
            resp = sm.session.request(method, url, timeout=timeout, **kwargs)

            is_rate_limited = False
            if resp.status_code == 429:
                is_rate_limited = True
            else:
                try:
                    detail = (
                        resp.json().get("detail", "")
                        if resp.status_code >= 400
                        else ""
                    )
                    if "SIMULATION_LIMIT_EXCEEDED" in str(detail):
                        is_rate_limited = True
                except Exception:
                    pass

            if is_rate_limited:
                wait_time = min(2**attempt * 5, 60)
                logger.warning(
                    "Rate limited (429/SIMULATION_LIMIT_EXCEEDED). "
                    "Waiting %ss (attempt %s/%s)...",
                    wait_time,
                    attempt + 1,
                    max_retries,
                )
                time.sleep(wait_time)
                continue

            if resp.status_code == 401:
                logger.warning("Session expired (401). Refreshing...")
                sm.refresh_session()
                continue

            return resp

        except (requests.ConnectionError, requests.Timeout) as e:
            wait_time = 10
            logger.warning(
                "Connection error: %s. Waiting %ss (attempt %s/%s)...",
                e,
                wait_time,
                attempt + 1,
                max_retries,
            )
            time.sleep(wait_time)

    raise RuntimeError(
        f"Failed to perform {method} {url} after {max_retries} attempts."
    )


# ---------------------------------------------------------------------------
# Simulation submission — dead-loop 429 retry
# ---------------------------------------------------------------------------

def create_simulation(sm: SessionManager, payload: Dict[str, Any]) -> str:
    """Submit a simulation task with dead-loop retry on 429.

    Uses the same infinite-retry pattern as platform_backtest.py /
    resimulate_alphas.py. Only non-recoverable errors (non-429, non-401,
    non-network) will raise.

    Parameters
    ----------
    sm : SessionManager
        Managed session.
    payload : dict
        Simulation payload (must include ``type``, ``regular``/``expression``,
        ``settings``).

    Returns
    -------
    str
        ``progress_url`` (the Location header from the 201 response).

    Raises
    ------
    ValueError
        On non-recoverable submission errors.
    """
    url = f"{BRAIN_API_URL}/simulations"
    logger.info("Submitting simulation task...")

    while True:
        sm.ensure_valid()
        try:
            resp = sm.session.post(url, json=payload)

            if resp.status_code == 201:
                progress_url = resp.headers.get("Location")
                simulation_id = progress_url.split("/")[-1] if progress_url else "unknown"
                logger.info("Simulation submitted successfully. ID: %s", simulation_id)
                return progress_url

            # Parse error detail
            try:
                data = resp.json()
                detail = (
                    data[0].get("detail", "")
                    if isinstance(data, list)
                    else data.get("detail", "")
                )
            except Exception:
                detail = ""

            # Rate limit → retry forever
            if "SIMULATION_LIMIT_EXCEEDED" in str(detail) or resp.status_code == 429:
                logger.warning(
                    "Rate limited (429/SIMULATION_LIMIT_EXCEEDED). Waiting 5s..."
                )
                time.sleep(5)
                continue

            # Session expired → refresh
            if resp.status_code == 401:
                logger.warning("Session expired (401). Refreshing...")
                sm.refresh_session()
                continue

            # Non-recoverable
            error_msg = (
                f"Submission failed ({resp.status_code}): {resp.text[:500]}"
            )
            logger.error(error_msg)
            raise ValueError(error_msg)

        except (requests.ConnectionError, requests.Timeout) as e:
            logger.warning("Connection error during submission: %s. Retrying in 10s...", e)
            time.sleep(10)
            continue

        except ValueError:
            raise  # Re-raise fatal submission failures


# ---------------------------------------------------------------------------
# Simulation polling — Retry-After + terminal state detection
# ---------------------------------------------------------------------------

def poll_alpha(
    sm: SessionManager,
    progress_url: str,
    timeout: int = 1200,
) -> Dict[str, Any]:
    """Poll a simulation task until completion, timeout, or terminal failure.

    Parameters
    ----------
    sm : SessionManager
        Managed session.
    progress_url : str
        The Location URL returned by submission (or simulation URL).
    timeout : int
        Maximum polling time in seconds (default 1200 = 20 min).

    Returns
    -------
    dict
        Raw simulation result data from the API.

    Raises
    ------
    TimeoutError
        If polling exceeds ``timeout`` seconds.
    ValueError
        If the simulation enters an unknown terminal state.
    """
    start_wait = time.time()
    data: Dict[str, Any] = {}

    logger.info("Polling simulation: %s", progress_url)

    while time.time() - start_wait < timeout:
        sm.ensure_valid()
        try:
            resp = sm.session.get(progress_url)
            data = resp.json()

            if resp.status_code == 200:
                status = data.get("status")
                alpha_id = data.get("alpha")

                # Terminal states
                if status in ("COMPLETE", "ERROR", "FAIL", "WARNING", "CANCELLED"):
                    logger.info("Simulation ended with status: %s", status)
                    break
                if alpha_id:
                    logger.info("Alpha ID found, simulation complete.")
                    break

                # Check for unknown states
                if status not in ("QUEUED", "PROCESSING", "PENDING", None):
                    err_msg = (
                        data.get("error", "")
                        or data.get("message", "")
                        or str(data)
                    )
                    # Clean up the stuck simulation
                    try:
                        sm.session.delete(progress_url)
                    except Exception:
                        pass
                    raise ValueError(
                        f"Simulation stuck in unknown state: {status} ({err_msg})"
                    )

            # Respect Retry-After header
            retry_after_val = resp.headers.get("Retry-After", 1)
            try:
                sleep_time = max(1.0, float(retry_after_val))
            except (ValueError, TypeError):
                sleep_time = 1.0
            time.sleep(sleep_time)

        except ValueError:
            raise
        except Exception as e:
            logger.error("Poll error: %s. Retrying in 10s...", e)
            time.sleep(10)
    else:
        raise TimeoutError(f"Simulation timed out after {timeout}s")

    return data


# ---------------------------------------------------------------------------
# Alpha detail fetch — exponential backoff retry
# ---------------------------------------------------------------------------

def fetch_alpha_details(sm: SessionManager, alpha_id: str) -> Dict[str, Any]:
    """Fetch alpha details with exponential backoff retry (3 attempts).

    Parameters
    ----------
    sm : SessionManager
        Managed session.
    alpha_id : str
        Alpha ID returned by the simulation.

    Returns
    -------
    dict
        Alpha detail JSON from ``GET /alphas/{alpha_id}``.

    Raises
    ------
    Exception
        After 3 failed retries.
    """
    url = f"{BRAIN_API_URL}/alphas/{alpha_id}"
    for retry in range(3):
        try:
            sm.ensure_valid()
            resp = sm.session.get(url)
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if retry == 2:
                raise
            logger.warning(
                "Failed to fetch alpha details for %s, retry %s/3: %s",
                alpha_id,
                retry + 1,
                e,
            )
            time.sleep(2**retry)

    # Should be unreachable; satisfy type checker
    raise RuntimeError(f"Failed to fetch alpha details for {alpha_id}")


# ---------------------------------------------------------------------------
# Concurrent simulation submission — parallel single-POST calls
# ---------------------------------------------------------------------------

def create_simulations_concurrent(
    sm: SessionManager,
    payloads: list,
    max_workers: int = 3,
    poll_timeout: int = 1200,
    fetch_details: bool = True,
) -> list:
    """Submit multiple simulation tasks concurrently via ThreadPoolExecutor.

    Each payload is submitted independently with dead-loop 429 retry, then
    polled for completion in the same worker thread.  Use this for Python
    Alpha submissions (batch mode is not supported for PYTHON language).

    Parameters
    ----------
    sm : SessionManager
        Managed session (shared across threads — SessionManager is thread-safe).
    payloads : list
        List of simulation payload dicts, one per alpha.
    max_workers : int
        Maximum number of concurrent worker threads (default 3).
    poll_timeout : int
        Seconds to wait per simulation (default 1200 = 20 min).
    fetch_details : bool
        If True, fetch alpha metrics after completion (default True).

    Returns
    -------
    list of dict
        Each result dict contains:
          - index: int
          - payload: dict (the original payload)
          - status: "OK" | "FAIL"
          - sim_id: str | None
          - alpha_id: str | None
          - metrics: dict | None (sharpe, fitness, turnover, ...)
          - error: str | None
    """
    from concurrent.futures import ThreadPoolExecutor, as_completed

    results = [None] * len(payloads)

    def _submit_and_poll(idx: int, payload: dict) -> dict:
        try:
            progress_url = create_simulation(sm, payload)
            sim_id = progress_url.split("/")[-1]

            data = poll_alpha(sm, progress_url, timeout=poll_timeout)
            alpha_id = data.get("alpha")

            if alpha_id and fetch_details:
                details = fetch_alpha_details(sm, alpha_id)
                metrics = {
                    "sharpe": details.get("is", {}).get("sharpe"),
                    "fitness": details.get("is", {}).get("fitness"),
                    "turnover": details.get("is", {}).get("turnover"),
                    "returns": details.get("is", {}).get("returns"),
                    "drawdown": details.get("is", {}).get("drawdown"),
                }
            else:
                metrics = None

            return {
                "index": idx,
                "payload": payload,
                "status": "OK" if alpha_id else "FAIL",
                "sim_id": sim_id,
                "alpha_id": alpha_id,
                "metrics": metrics,
                "error": None if alpha_id else data.get("error") or data.get("message", ""),
            }
        except Exception as e:
            return {
                "index": idx,
                "payload": payload,
                "status": "ERR",
                "sim_id": None,
                "alpha_id": None,
                "metrics": None,
                "error": str(e)[:500],
            }

    logger.info(
        "Starting %d concurrent simulations (max_workers=%d)...",
        len(payloads),
        max_workers,
    )
    with ThreadPoolExecutor(max_workers=max_workers) as pool:
        futures = {
            pool.submit(_submit_and_poll, i, p): i for i, p in enumerate(payloads)
        }
        for f in as_completed(futures):
            r = f.result()
            results[r["index"]] = r
            logger.info(
                "[%d/%d] sim=%s status=%s",
                r["index"] + 1,
                len(payloads),
                r.get("sim_id", "?"),
                r["status"],
            )

    ok_count = sum(1 for r in results if r["status"] == "OK")
    logger.info(
        "Concurrent batch complete: %d OK, %d failed out of %d",
        ok_count,
        len(payloads) - ok_count,
        len(payloads),
    )
    return results

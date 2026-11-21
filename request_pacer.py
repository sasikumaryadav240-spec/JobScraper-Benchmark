"""
Centralized Request Pacer, Rate Limiter, and Circuit Breaker.

Provides thread-safe, per-host request pacing and resilient rate-limit handling:
- Enforces a minimum interval (default 10.0 seconds) between consecutive outbound requests
  to the same platform/host.
- Hooks transparently into requests.Session and curl_cffi.requests.Session so all outbound
  requests (search, pagination, detail, retries) are strictly paced immediately before execution.
- Prevents concurrent or sequential requests from bypassing the pacing mechanism.
- Supports deterministic fake clocks for high-speed testing without real sleeping.
- Intelligently handles HTTP 429 (Retry-After, exponential backoff with jitter).
- Implements a configurable Circuit Breaker for graceful degradation and error isolation.
"""

from __future__ import annotations

import email.utils
import logging
import random
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Optional, Tuple
from urllib.parse import urlsplit

logger = logging.getLogger("RequestPacer")
if not logger.handlers:
    handler = logging.StreamHandler()
    formatter = logging.Formatter("%(asctime)s [%(levelname)s] [RequestPacer] %(message)s")
    handler.setFormatter(formatter)
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)


def normalize_host(url_or_host: str) -> str:
    """Extract and normalize host domain from URL or bare hostname."""
    if not url_or_host:
        return "default"
    s = str(url_or_host).strip()
    if "://" not in s:
        s = f"http://{s}"
    try:
        parts = urlsplit(s)
        netloc = parts.netloc.lower()
        # Remove port if present
        host = netloc.split(":")[0]
        # Remove leading www. for uniform per-platform pacing
        if host.startswith("www."):
            host = host[4:]
        return host or "default"
    except Exception:
        return "default"


def parse_retry_after(header_val: Optional[str], now_ts: Optional[float] = None) -> Optional[float]:
    """
    Parse HTTP Retry-After header.
    Can be integer seconds (e.g. '120') or HTTP-date (e.g. 'Wed, 21 Oct 2026 07:28:00 GMT').
    Returns wait duration in seconds, or None if invalid/absent.
    """
    if not header_val:
        return None
    val = str(header_val).strip()
    # Try integer seconds
    try:
        seconds = float(val)
        return max(0.0, seconds)
    except ValueError:
        pass

    # Try HTTP date
    try:
        parsed_tuple = email.utils.parsedate_to_datetime(val)
        if parsed_tuple:
            target_ts = parsed_tuple.timestamp()
            current_ts = now_ts if now_ts is not None else time.time()
            return max(0.0, target_ts - current_ts)
    except Exception:
        pass

    return None


def calculate_backoff(
    attempt: int,
    base_backoff: float = 30.0,
    max_backoff: float = 120.0,
    jitter_range: Tuple[float, float] = (1.0, 5.0),
) -> float:
    """Calculate exponential backoff duration with random jitter."""
    exp = 2 ** max(0, attempt - 1)
    duration = min(max_backoff, base_backoff * exp)
    jitter = random.uniform(*jitter_range)
    return duration + jitter


class RequestPacer:
    """
    Thread-safe per-host rate limiter ensuring a minimum time gap between requests to the same host.
    """

    def __init__(self, default_interval: float = 10.0) -> None:
        self.default_interval = float(default_interval)
        self.host_intervals: Dict[str, float] = {}
        self._last_request_times: Dict[str, float] = {}
        self._host_retry_after: Dict[str, float] = {}
        self._lock = threading.RLock()
        self._host_locks: Dict[str, threading.Lock] = {}
        self._time_func: Callable[[], float] = time.time
        self._sleep_func: Callable[[float], None] = time.sleep
        self.history: List[Dict[str, Any]] = []

    def record_retry_after(self, url_or_host: str, seconds: float) -> None:
        """Record Retry-After duration for a host and adjust its next allowed request timestamp."""
        with self._lock:
            host = normalize_host(url_or_host)
            now = self._time_func()
            self._last_request_times[host] = max(
                self._last_request_times.get(host, 0.0),
                now + float(seconds) - self.get_host_interval(host),
            )
            self._host_retry_after[host] = float(seconds)
            logger.info(f"Recorded Retry-After for '{host}': {seconds:.1f}s. Pacing adjusted.")

    def get_retry_after(self, url_or_host: str) -> Optional[float]:
        """Get the last recorded Retry-After duration for a host, if any."""
        with self._lock:
            host = normalize_host(url_or_host)
            return self._host_retry_after.get(host)

    def _get_host_lock(self, host: str) -> threading.Lock:
        with self._lock:
            if host not in self._host_locks:
                self._host_locks[host] = threading.Lock()
            return self._host_locks[host]

    def set_host_interval(self, host: str, interval: float) -> None:
        """Set custom minimum pacing interval for a specific host."""
        with self._lock:
            norm = normalize_host(host)
            self.host_intervals[norm] = float(interval)

    def get_host_interval(self, host: str) -> float:
        """Get the configured pacing interval for a host."""
        with self._lock:
            norm = normalize_host(host)
            return self.host_intervals.get(norm, self.default_interval)

    def set_clock(
        self,
        time_func: Callable[[], float],
        sleep_func: Callable[[float], None],
    ) -> None:
        """Inject deterministic fake clock functions for rapid testing."""
        with self._lock:
            self._time_func = time_func
            self._sleep_func = sleep_func

    def reset_clock(self) -> None:
        """Restore standard real-time clock functions."""
        with self._lock:
            self._time_func = time.time
            self._sleep_func = time.sleep

    def reset(self) -> None:
        """Reset all tracking timestamps and history."""
        with self._lock:
            self._last_request_times.clear()
            self._host_locks.clear()
            self._host_retry_after.clear()
            self.history.clear()

    def wait_before_request(self, url_or_host: str) -> float:
        """
        Enforce pacing immediately before an outbound request.
        Returns the duration waited in seconds.
        """
        host = normalize_host(url_or_host)
        host_lock = self._get_host_lock(host)
        with host_lock:
            interval = self.get_host_interval(host)
            now = self._time_func()
            with self._lock:
                last_ts = self._last_request_times.get(host)
            wait_time = 0.0

            if last_ts is not None:
                elapsed = now - last_ts
                if elapsed < interval:
                    wait_time = interval - elapsed
                    logger.info(
                        f"Pacing '{host}': elapsed {elapsed:.2f}s < interval {interval:.2f}s. "
                        f"Waiting {wait_time:.2f}s..."
                    )
                    self._sleep_func(wait_time)
                    # Update 'now' after waiting
                    now = self._time_func()

            with self._lock:
                self._last_request_times[host] = now
                self.history.append({
                    "host": host,
                    "url_or_host": url_or_host,
                    "waited_seconds": wait_time,
                    "timestamp": now,
                })
            return wait_time


class CircuitBreaker:
    """
    Per-source circuit breaker to detect persistent rate limits, blocks, or server errors.
    """

    def __init__(
        self,
        source_name: str,
        consecutive_failure_threshold: int = 3,
        cooldown_seconds: float = 60.0,
    ) -> None:
        self.source_name = source_name
        self.threshold = consecutive_failure_threshold
        self.cooldown_seconds = cooldown_seconds

        self.consecutive_failures = 0
        self.is_open = False
        self.last_trip_time: Optional[float] = None

        self.http_429_count = 0
        self.http_403_406_count = 0
        self.http_5xx_count = 0
        self.network_errors_count = 0
        self.retry_count = 0
        self.circuit_breaker_events = 0
        self.recovery_attempts = 0

    def record_success(self) -> None:
        """Record a successful request or search, resetting consecutive failures."""
        self.consecutive_failures = 0
        if self.is_open:
            self.is_open = False
            self.recovery_attempts += 1
            logger.info(f"Circuit breaker for {self.source_name} closed after successful recovery.")

    def record_failure(
        self,
        category: str,
        status_code: Optional[int] = None,
        is_retry: bool = False,
    ) -> bool:
        """
        Record a failed request or search. Returns True if circuit breaker tripped.
        """
        if is_retry:
            self.retry_count += 1

        if status_code == 429 or "429" in category:
            self.http_429_count += 1
        elif status_code in (403, 406) or any(c in category for c in ("403", "406", "recaptcha", "captcha")):
            self.http_403_406_count += 1
        elif (status_code and status_code >= 500) or "5xx" in category or "server" in category:
            self.http_5xx_count += 1
        elif "network" in category or "timeout" in category:
            self.network_errors_count += 1

        self.consecutive_failures += 1

        if self.consecutive_failures >= self.threshold and not self.is_open:
            self.is_open = True
            self.circuit_breaker_events += 1
            self.last_trip_time = time.time()
            logger.warning(
                f"Circuit breaker TRIPPED for {self.source_name} after "
                f"{self.consecutive_failures} consecutive failures "
                f"(429: {self.http_429_count}, 403/406: {self.http_403_406_count})."
            )
            return True
        return False

    def can_proceed(self) -> bool:
        """Check if requests are allowed to proceed through the breaker."""
        return not self.is_open

    def to_dict(self) -> Dict[str, Any]:
        """Export circuit breaker metrics."""
        return {
            "source_name": self.source_name,
            "is_open": self.is_open,
            "consecutive_failures": self.consecutive_failures,
            "failure_threshold": self.threshold,
            "http_429_count": self.http_429_count,
            "http_403_406_count": self.http_403_406_count,
            "http_5xx_count": self.http_5xx_count,
            "network_errors_count": self.network_errors_count,
            "retry_count": self.retry_count,
            "circuit_breaker_events": self.circuit_breaker_events,
            "recovery_attempts": self.recovery_attempts,
        }


# Global Singleton Instance
_GLOBAL_PACER: Optional[RequestPacer] = None
_PACER_LOCK = threading.Lock()
_HOOKS_INSTALLED = False
_ORIG_REQUESTS_SESSION_REQUEST: Optional[Any] = None
_ORIG_CURL_SESSION_REQUEST: Optional[Any] = None


def get_global_pacer(default_interval: float = 10.0) -> RequestPacer:
    """Obtain or initialize the global singleton RequestPacer."""
    global _GLOBAL_PACER
    with _PACER_LOCK:
        if _GLOBAL_PACER is None:
            _GLOBAL_PACER = RequestPacer(default_interval=default_interval)
            # Default 10.0s intervals for all benchmark platforms
            for host in (
                "linkedin.com",
                "apis.indeed.com",
                "indeed.com",
                "naukri.com",
                "boards-api.greenhouse.io",
                "api.lever.co",
                "jobs.ashbyhq.com",
            ):
                _GLOBAL_PACER.set_host_interval(host, default_interval)
        return _GLOBAL_PACER


def install_request_pacer(pacer: Optional[RequestPacer] = None) -> RequestPacer:
    """
    Hook RequestPacer into requests.Session.request and curl_cffi.requests.Session.request.
    Guarantees every outbound request passes through pacing immediately before sending.
    """
    global _HOOKS_INSTALLED, _ORIG_REQUESTS_SESSION_REQUEST, _ORIG_CURL_SESSION_REQUEST
    active_pacer = pacer or get_global_pacer()

    with _PACER_LOCK:
        if _HOOKS_INSTALLED:
            return active_pacer

        import requests.sessions

        _ORIG_REQUESTS_SESSION_REQUEST = requests.sessions.Session.request

        def _paced_requests_request(self_session: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
            active_pacer.wait_before_request(url)
            resp = _ORIG_REQUESTS_SESSION_REQUEST(self_session, method, url, *args, **kwargs)
            if hasattr(resp, "status_code") and resp.status_code == 429:
                ra_hdr = getattr(resp, "headers", {}).get("Retry-After")
                if ra_hdr:
                    ra_val = parse_retry_after(ra_hdr)
                    if ra_val:
                        active_pacer.record_retry_after(url, ra_val)
            return resp

        requests.sessions.Session.request = _paced_requests_request  # type: ignore[assignment]

        try:
            import curl_cffi.requests.session

            _ORIG_CURL_SESSION_REQUEST = curl_cffi.requests.session.Session.request

            def _paced_curl_request(self_session: Any, method: str, url: str, *args: Any, **kwargs: Any) -> Any:
                active_pacer.wait_before_request(url)
                resp = _ORIG_CURL_SESSION_REQUEST(self_session, method, url, *args, **kwargs)
                if hasattr(resp, "status_code") and resp.status_code == 429:
                    ra_hdr = getattr(resp, "headers", {}).get("Retry-After")
                    if ra_hdr:
                        ra_val = parse_retry_after(ra_hdr)
                        if ra_val:
                            active_pacer.record_retry_after(url, ra_val)
                return resp

            curl_cffi.requests.session.Session.request = _paced_curl_request  # type: ignore[assignment]
        except (ImportError, AttributeError) as e:
            logger.debug(f"curl_cffi not hooked: {e}")

        _HOOKS_INSTALLED = True
        logger.info(f"RequestPacer hooks successfully installed (default_interval={active_pacer.default_interval}s).")
        return active_pacer


def uninstall_request_pacer() -> None:
    """Restore original un-paced request methods (useful in isolated tests)."""
    global _HOOKS_INSTALLED, _ORIG_REQUESTS_SESSION_REQUEST, _ORIG_CURL_SESSION_REQUEST
    with _PACER_LOCK:
        if not _HOOKS_INSTALLED:
            return

        if _ORIG_REQUESTS_SESSION_REQUEST:
            import requests.sessions
            requests.sessions.Session.request = _ORIG_REQUESTS_SESSION_REQUEST  # type: ignore[assignment]
            _ORIG_REQUESTS_SESSION_REQUEST = None

        if _ORIG_CURL_SESSION_REQUEST:
            try:
                import curl_cffi.requests.session
                curl_cffi.requests.session.Session.request = _ORIG_CURL_SESSION_REQUEST  # type: ignore[assignment]
            except Exception:
                pass
            _ORIG_CURL_SESSION_REQUEST = None

        _HOOKS_INSTALLED = False
        logger.info("RequestPacer hooks uninstalled.")

"""Custom HTTPXRequest subclass instrumenting Telegram long-polling telemetry.

Hooks getUpdates HTTP requests to track polling latency, success rates,
and HTTP 409 Conflict events without altering python-telegram-bot internals.
"""

from typing import Any

from telegram.error import Conflict
from telegram.request import BaseRequest, HTTPXRequest
from telegram.request._requestdata import RequestData

from app.bot.polling_tracker import PollingTracker, get_polling_tracker


class MonitoredHTTPXRequest(HTTPXRequest):
    """HTTPXRequest subclass instrumenting Telegram getUpdates polling roundtrips."""

    def __init__(
        self,
        *args: Any,
        polling_tracker: PollingTracker | None = None,
        **kwargs: Any,
    ) -> None:
        super().__init__(*args, **kwargs)
        self._tracker: PollingTracker = polling_tracker or get_polling_tracker()

    @property
    def polling_tracker(self) -> PollingTracker:
        """Return the bound PollingTracker instance."""
        return self._tracker

    async def do_request(
        self,
        url: str,
        method: str,
        request_data: RequestData | None = None,
        read_timeout: Any = BaseRequest.DEFAULT_NONE,
        write_timeout: Any = BaseRequest.DEFAULT_NONE,
        connect_timeout: Any = BaseRequest.DEFAULT_NONE,
        pool_timeout: Any = BaseRequest.DEFAULT_NONE,
    ) -> tuple[int, bytes]:
        """Intercept and instrument Telegram API HTTP requests."""
        clean_url = url.split("?")[0].rstrip("/")
        is_get_updates = clean_url.endswith(("/getUpdates", "getUpdates"))

        if is_get_updates:
            self._tracker.record_attempt()

        try:
            code, payload = await super().do_request(
                url=url,
                method=method,
                request_data=request_data,
                read_timeout=read_timeout,
                write_timeout=write_timeout,
                connect_timeout=connect_timeout,
                pool_timeout=pool_timeout,
            )
        except Exception as exc:
            if is_get_updates:
                if isinstance(exc, Conflict) or "conflict" in str(exc).lower():
                    self._tracker.record_conflict()
                else:
                    self._tracker.record_error(exc)
            raise

        if is_get_updates:
            if 200 <= code <= 299:
                # Validate that Telegram JSON payload indicates ok: true
                if b'"ok":true' in payload or b'"ok": true' in payload:
                    self._tracker.record_success()
                else:
                    self._tracker.record_error("api_ok_false")
            elif code == 409:
                self._tracker.record_conflict()
            else:
                self._tracker.record_error(f"http_{code}")

        return code, payload

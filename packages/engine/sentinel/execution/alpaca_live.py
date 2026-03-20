"""
AlpacaLiveBroker — Live broker adapter for Alpaca Markets.

This adapter is intentionally conservative:
- Never submits an order without a valid order_id
- Logs every submission attempt
- Handles Alpaca-specific error codes
- Rate limiting: respects Alpaca's 200 orders/min limit
- Order deduplication via client_order_id

IMPORTANT: Requires ALPACA_API_KEY and ALPACA_API_SECRET in environment.
Never use this adapter in 'development' app_env — the firewall enforces this.
"""
from __future__ import annotations

import asyncio
import logging
import os
import time
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any

import httpx

from sentinel.config import Settings
from sentinel.domain.types import OrderSide, OrderStatus, OrderType, TimeInForce
from sentinel.execution.broker import OrderRequest, OrderUpdate

logger = logging.getLogger(__name__)

_ALPACA_BASE_URL_LIVE = "https://api.alpaca.markets"
_ALPACA_BASE_URL_PAPER = "https://paper-api.alpaca.markets"
_ALPACA_DATA_URL = "https://data.alpaca.markets"

# Rate limiting: 200 orders/min => 1 order per 0.3s minimum
_MIN_ORDER_INTERVAL_SECONDS = 0.30
_MAX_RETRIES = 3
_RETRY_BACKOFF_BASE = 1.0  # seconds


def _map_alpaca_status(alpaca_status: str) -> OrderStatus:
    """Map Alpaca order status string to our OrderStatus enum."""
    mapping: dict[str, OrderStatus] = {
        "new": OrderStatus.ACCEPTED,
        "partially_filled": OrderStatus.PARTIALLY_FILLED,
        "filled": OrderStatus.FILLED,
        "done_for_day": OrderStatus.CANCELED,
        "canceled": OrderStatus.CANCELED,
        "expired": OrderStatus.EXPIRED,
        "replaced": OrderStatus.CANCELED,
        "pending_cancel": OrderStatus.PENDING_CANCEL,
        "pending_replace": OrderStatus.ACCEPTED,
        "accepted": OrderStatus.ACCEPTED,
        "pending_new": OrderStatus.SUBMITTED,
        "accepted_for_bidding": OrderStatus.ACCEPTED,
        "stopped": OrderStatus.REJECTED,
        "rejected": OrderStatus.REJECTED,
        "suspended": OrderStatus.REJECTED,
        "calculated": OrderStatus.ACCEPTED,
    }
    return mapping.get(alpaca_status.lower(), OrderStatus.REJECTED)


def _map_order_side(side: OrderSide) -> str:
    return "buy" if side == OrderSide.BUY else "sell"


def _map_order_type(order_type: OrderType) -> str:
    mapping = {
        OrderType.MARKET: "market",
        OrderType.LIMIT: "limit",
        OrderType.STOP: "stop",
        OrderType.STOP_LIMIT: "stop_limit",
    }
    return mapping.get(order_type, "market")


def _map_tif(tif: TimeInForce) -> str:
    mapping = {
        TimeInForce.DAY: "day",
        TimeInForce.GTC: "gtc",
        TimeInForce.IOC: "ioc",
        TimeInForce.FOK: "fok",
        TimeInForce.OPG: "opg",
        TimeInForce.CLS: "cls",
    }
    return mapping.get(tif, "day")


class AlpacaLiveBroker:
    """
    Live broker adapter for Alpaca Markets v2 API.

    Raises EnvironmentError on init if ALPACA_API_KEY or ALPACA_API_SECRET
    are missing, or if app_env is 'development'.
    """

    def __init__(self, settings: Settings) -> None:
        app_env = getattr(settings, "app_env", "development")
        if app_env.lower() == "development":
            raise OSError(
                "AlpacaLiveBroker cannot be used in 'development' environment. "
                "Use PaperBroker instead."
            )

        api_key = os.environ.get("ALPACA_API_KEY") or getattr(settings, "alpaca_api_key", None)
        api_secret = os.environ.get("ALPACA_API_SECRET") or getattr(settings, "alpaca_api_secret", None)

        if not api_key or not api_secret:
            raise OSError(
                "ALPACA_API_KEY and ALPACA_API_SECRET must be set in the environment."
            )

        use_paper = getattr(settings, "alpaca_paper_trading", False)
        self._base_url = _ALPACA_BASE_URL_PAPER if use_paper else _ALPACA_BASE_URL_LIVE
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Content-Type": "application/json",
        }
        self._settings = settings

        # Rate limiting state
        self._last_order_time: float = 0.0
        self._order_lock = asyncio.Lock()

    # ------------------------------------------------------------------
    # BrokerAdapter interface
    # ------------------------------------------------------------------

    async def submit_order(self, request: OrderRequest) -> OrderUpdate:
        """Submit to Alpaca v2 orders API. Maps our types to Alpaca's format."""
        now = datetime.now(tz=UTC)

        if not request.client_order_id:
            logger.error("AlpacaLiveBroker: refusing to submit order without client_order_id")
            return OrderUpdate(
                broker_order_id="",
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason="client_order_id is required",
                timestamp=now,
            )

        logger.info(
            "AlpacaLiveBroker: submitting order client_id=%s symbol=%s side=%s qty=%d type=%s",
            request.client_order_id,
            request.symbol,
            request.side.value,
            request.quantity,
            request.order_type.value,
        )

        payload: dict[str, Any] = {
            "symbol": request.symbol,
            "qty": str(request.quantity),
            "side": _map_order_side(request.side),
            "type": _map_order_type(request.order_type),
            "time_in_force": _map_tif(request.time_in_force),
            "client_order_id": request.client_order_id,
        }

        if request.limit_price is not None:
            payload["limit_price"] = str(request.limit_price)
        if request.stop_price is not None:
            payload["stop_price"] = str(request.stop_price)

        async with self._order_lock:
            # Enforce rate limit
            elapsed = time.monotonic() - self._last_order_time
            if elapsed < _MIN_ORDER_INTERVAL_SECONDS:
                await asyncio.sleep(_MIN_ORDER_INTERVAL_SECONDS - elapsed)
            self._last_order_time = time.monotonic()

        try:
            response_data = await self._post_with_retry(
                "/v2/orders", payload, context=f"submit_order:{request.client_order_id}"
            )
        except httpx.HTTPStatusError as exc:
            status_code = exc.response.status_code
            error_body = exc.response.text
            logger.error(
                "AlpacaLiveBroker: order submission failed HTTP %d for %s: %s",
                status_code,
                request.client_order_id,
                error_body,
            )
            rejection = self._extract_rejection_reason(status_code, error_body)
            return OrderUpdate(
                broker_order_id="",
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=rejection,
                timestamp=now,
            )
        except Exception as exc:
            logger.exception(
                "AlpacaLiveBroker: unexpected error submitting order %s",
                request.client_order_id,
            )
            return OrderUpdate(
                broker_order_id="",
                client_order_id=request.client_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Unexpected error: {exc}",
                timestamp=now,
            )

        return self._parse_order_response(response_data)

    async def cancel_order(self, broker_order_id: str) -> OrderUpdate:
        """Cancel an order by Alpaca order ID."""
        now = datetime.now(tz=UTC)
        logger.info("AlpacaLiveBroker: cancelling order %s", broker_order_id)

        try:
            async with httpx.AsyncClient() as client:
                resp = await client.delete(
                    f"{self._base_url}/v2/orders/{broker_order_id}",
                    headers=self._headers,
                    timeout=10.0,
                )
                if resp.status_code == 204:
                    return OrderUpdate(
                        broker_order_id=broker_order_id,
                        status=OrderStatus.CANCELED,
                        timestamp=now,
                    )
                resp.raise_for_status()
                data = resp.json()
                return self._parse_order_response(data)
        except httpx.HTTPStatusError as exc:
            logger.error(
                "AlpacaLiveBroker: cancel failed HTTP %d for order %s",
                exc.response.status_code,
                broker_order_id,
            )
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Cancel failed: HTTP {exc.response.status_code}",
                timestamp=now,
            )
        except Exception as exc:
            logger.exception("AlpacaLiveBroker: unexpected error cancelling order %s", broker_order_id)
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"Cancel error: {exc}",
                timestamp=now,
            )

    async def get_order(self, broker_order_id: str) -> OrderUpdate:
        """Fetch current order status from Alpaca."""
        now = datetime.now(tz=UTC)
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/v2/orders/{broker_order_id}",
                    headers=self._headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                return self._parse_order_response(resp.json())
        except httpx.HTTPStatusError as exc:
            logger.error(
                "AlpacaLiveBroker: get_order HTTP %d for %s",
                exc.response.status_code,
                broker_order_id,
            )
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=f"HTTP {exc.response.status_code}",
                timestamp=now,
            )
        except Exception as exc:
            logger.exception("AlpacaLiveBroker: error fetching order %s", broker_order_id)
            return OrderUpdate(
                broker_order_id=broker_order_id,
                status=OrderStatus.REJECTED,
                rejection_reason=str(exc),
                timestamp=now,
            )

    async def get_positions(self) -> list[dict]:
        """Fetch all open positions from Alpaca."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/v2/positions",
                    headers=self._headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            logger.exception("AlpacaLiveBroker: failed to fetch positions")
            return []

    async def get_account(self) -> dict:
        """Fetch account summary from Alpaca."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/v2/account",
                    headers=self._headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                return resp.json()
        except Exception:
            logger.exception("AlpacaLiveBroker: failed to fetch account")
            return {}

    async def is_market_open(self) -> bool:
        """Check Alpaca clock API for market status."""
        try:
            async with httpx.AsyncClient() as client:
                resp = await client.get(
                    f"{self._base_url}/v2/clock",
                    headers=self._headers,
                    timeout=10.0,
                )
                resp.raise_for_status()
                data = resp.json()
                return bool(data.get("is_open", False))
        except Exception:
            logger.exception("AlpacaLiveBroker: failed to check market clock")
            return False

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    async def _post_with_retry(
        self,
        path: str,
        payload: dict,
        context: str = "",
    ) -> dict:
        """POST with retry on 429 (rate limit) and transient 5xx errors."""
        url = f"{self._base_url}{path}"
        last_exc: Exception | None = None

        for attempt in range(1, _MAX_RETRIES + 1):
            try:
                async with httpx.AsyncClient() as client:
                    resp = await client.post(
                        url,
                        json=payload,
                        headers=self._headers,
                        timeout=15.0,
                    )
                    if resp.status_code == 429:
                        retry_after = float(resp.headers.get("Retry-After", _RETRY_BACKOFF_BASE * attempt))
                        logger.warning(
                            "AlpacaLiveBroker: rate limited (429) on %s attempt %d/%d. Sleeping %.1fs.",
                            context,
                            attempt,
                            _MAX_RETRIES,
                            retry_after,
                        )
                        await asyncio.sleep(retry_after)
                        continue
                    if resp.status_code >= 500:
                        backoff = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                        logger.warning(
                            "AlpacaLiveBroker: server error %d on %s attempt %d/%d. Retry in %.1fs.",
                            resp.status_code,
                            context,
                            attempt,
                            _MAX_RETRIES,
                            backoff,
                        )
                        await asyncio.sleep(backoff)
                        continue
                    resp.raise_for_status()
                    return resp.json()
            except httpx.HTTPStatusError:
                raise
            except Exception as exc:
                last_exc = exc
                backoff = _RETRY_BACKOFF_BASE * (2 ** (attempt - 1))
                logger.warning(
                    "AlpacaLiveBroker: transient error on %s attempt %d/%d: %s. Retry in %.1fs.",
                    context,
                    attempt,
                    _MAX_RETRIES,
                    exc,
                    backoff,
                )
                await asyncio.sleep(backoff)

        raise last_exc or RuntimeError(f"All {_MAX_RETRIES} retries exhausted for {context}")

    def _parse_order_response(self, data: dict) -> OrderUpdate:
        """Parse Alpaca order response dict into our OrderUpdate model."""
        now = datetime.now(tz=UTC)
        broker_order_id = data.get("id", "")
        client_order_id = data.get("client_order_id", "")
        alpaca_status = data.get("status", "rejected")
        status = _map_alpaca_status(alpaca_status)

        filled_qty_raw = data.get("filled_qty", "0") or "0"
        filled_qty = int(Decimal(str(filled_qty_raw)))

        filled_avg_price: Decimal | None = None
        if data.get("filled_avg_price"):
            try:
                filled_avg_price = Decimal(str(data["filled_avg_price"]))
            except Exception:
                filled_avg_price = None

        submitted_at = data.get("submitted_at") or data.get("created_at")
        timestamp = now
        if submitted_at:
            try:
                timestamp = datetime.fromisoformat(submitted_at.replace("Z", "+00:00"))
            except ValueError:
                timestamp = now

        rejection_reason: str | None = None
        if status == OrderStatus.REJECTED:
            rejection_reason = data.get("reject_reason") or alpaca_status

        return OrderUpdate(
            broker_order_id=broker_order_id,
            client_order_id=client_order_id,
            status=status,
            filled_qty=filled_qty,
            filled_avg_price=filled_avg_price,
            rejection_reason=rejection_reason,
            timestamp=timestamp,
        )

    def _extract_rejection_reason(self, status_code: int, body: str) -> str:
        """Map Alpaca HTTP error codes to human-readable rejection reasons."""
        if status_code == 403:
            return "Forbidden: account not authorized for this action. Check account permissions."
        if status_code == 422:
            return f"Unprocessable order: {body[:200]}"
        if status_code == 400:
            return f"Bad request: {body[:200]}"
        if status_code == 409:
            return "Conflict: duplicate client_order_id or order already exists."
        return f"HTTP {status_code}: {body[:200]}"

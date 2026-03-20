"""Alpaca Markets market data provider."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any

import httpx
import structlog
from tenacity import (
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from sentinel.market.provider import (
    Bar,
    ProviderError,
    Quote,
    RateLimitError,
    Snapshot,
    SymbolNotFoundError,
)

logger = structlog.get_logger(__name__)

_DATA_BASE = "https://data.alpaca.markets"


def _is_retryable(exc: BaseException) -> bool:
    if isinstance(exc, RateLimitError):
        return True
    if isinstance(exc, ProviderError) and "5" in str(exc)[:3]:
        return True
    return False


def _parse_alpaca_dt(value: str) -> datetime:
    """Parse Alpaca's ISO 8601 timestamps (may include Z or offset)."""
    value = value.replace("Z", "+00:00")
    return datetime.fromisoformat(value)


class AlpacaProvider:
    """Alpaca Markets REST API provider."""

    def __init__(
        self,
        api_key: str,
        api_secret: str,
        base_url: str = "https://paper-api.alpaca.markets",
    ) -> None:
        self._base_url = base_url.rstrip("/")
        self._headers = {
            "APCA-API-KEY-ID": api_key,
            "APCA-API-SECRET-KEY": api_secret,
            "Accept": "application/json",
        }
        self._client = httpx.AsyncClient(
            headers=self._headers,
            timeout=httpx.Timeout(10.0, connect=5.0),
        )

    async def _get(self, url: str, params: dict[str, Any] | None = None) -> Any:
        response = await self._client.get(url, params=params)
        if response.status_code == 404:
            raise SymbolNotFoundError(f"Not found: {url}")
        if response.status_code == 429:
            raise RateLimitError("Alpaca rate limit exceeded")
        if response.status_code >= 500:
            raise ProviderError(f"Alpaca server error {response.status_code}: {response.text}")
        if response.status_code >= 400:
            raise ProviderError(f"Alpaca client error {response.status_code}: {response.text}")
        return response.json()

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def get_bars(
        self,
        symbol: str,
        timeframe: str,
        start: datetime,
        end: datetime,
        limit: int = 500,
    ) -> list[Bar]:
        url = f"{_DATA_BASE}/v2/stocks/{symbol.upper()}/bars"
        params: dict[str, Any] = {
            "timeframe": timeframe,
            "start": start.isoformat(),
            "end": end.isoformat(),
            "limit": limit,
            "adjustment": "raw",
            "feed": "iex",
        }
        data = await self._get(url, params=params)
        bars_raw: list[dict[str, Any]] = data.get("bars") or []
        bars: list[Bar] = []
        for b in bars_raw:
            bars.append(
                Bar(
                    symbol=symbol.upper(),
                    timestamp=_parse_alpaca_dt(b["t"]),
                    open=Decimal(str(b["o"])),
                    high=Decimal(str(b["h"])),
                    low=Decimal(str(b["l"])),
                    close=Decimal(str(b["c"])),
                    volume=int(b["v"]),
                    vwap=Decimal(str(b["vw"])) if b.get("vw") else None,
                    trade_count=b.get("n"),
                )
            )
        return bars

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def get_quote(self, symbol: str) -> Quote:
        url = f"{_DATA_BASE}/v2/stocks/{symbol.upper()}/quotes/latest"
        data = await self._get(url, params={"feed": "iex"})
        q = data["quote"]
        return Quote(
            symbol=symbol.upper(),
            timestamp=_parse_alpaca_dt(q["t"]),
            bid=Decimal(str(q["bp"])),
            ask=Decimal(str(q["ap"])),
            bid_size=int(q["bs"]),
            ask_size=int(q["as"]),
        )

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def get_snapshot(self, symbol: str) -> Snapshot:
        url = f"{_DATA_BASE}/v2/stocks/{symbol.upper()}/snapshot"
        data = await self._get(url, params={"feed": "iex"})
        return self._parse_snapshot(symbol.upper(), data)

    @retry(
        retry=retry_if_exception(_is_retryable),
        wait=wait_exponential(multiplier=1, min=1, max=30),
        stop=stop_after_attempt(4),
        reraise=True,
    )
    async def get_snapshots(self, symbols: list[str]) -> dict[str, Snapshot]:
        url = f"{_DATA_BASE}/v2/stocks/snapshots"
        params: dict[str, Any] = {
            "symbols": ",".join(s.upper() for s in symbols),
            "feed": "iex",
        }
        data = await self._get(url, params=params)
        result: dict[str, Snapshot] = {}
        for sym, snap_data in data.items():
            try:
                result[sym] = self._parse_snapshot(sym, snap_data)
            except Exception as exc:
                logger.warning("alpaca.snapshot_parse_error", symbol=sym, error=str(exc))
        return result

    async def validate_symbol(self, symbol: str) -> bool:
        url = f"{self._base_url}/v2/assets/{symbol.upper()}"
        try:
            data = await self._get(url)
            return bool(data.get("tradable", False))
        except SymbolNotFoundError:
            return False
        except ProviderError:
            return False

    async def get_tradeable_assets(self, asset_class: str = "us_equity") -> list[str]:
        url = f"{self._base_url}/v2/assets"
        params: dict[str, Any] = {
            "status": "active",
            "asset_class": asset_class,
            "tradable": True,
        }
        data = await self._get(url, params=params)
        assets: list[dict[str, Any]] = data if isinstance(data, list) else []
        return [a["symbol"] for a in assets if a.get("tradable")]

    async def close(self) -> None:
        await self._client.aclose()

    def _parse_snapshot(self, symbol: str, data: dict[str, Any]) -> Snapshot:
        lq = data.get("latestQuote") or data.get("latest_quote") or {}
        lb = data.get("minuteBar") or data.get("minute_bar") or data.get("latestBar") or {}
        db = data.get("dailyBar") or data.get("daily_bar")
        pc = data.get("prevDailyBar") or data.get("prev_daily_bar")

        quote = Quote(
            symbol=symbol,
            timestamp=_parse_alpaca_dt(lq.get("t", datetime.utcnow().isoformat())),
            bid=Decimal(str(lq.get("bp", "0"))),
            ask=Decimal(str(lq.get("ap", "0"))),
            bid_size=int(lq.get("bs", 0)),
            ask_size=int(lq.get("as", 0)),
        )

        def _parse_bar(raw: dict[str, Any]) -> Bar:
            return Bar(
                symbol=symbol,
                timestamp=_parse_alpaca_dt(raw.get("t", datetime.utcnow().isoformat())),
                open=Decimal(str(raw.get("o", "0"))),
                high=Decimal(str(raw.get("h", "0"))),
                low=Decimal(str(raw.get("l", "0"))),
                close=Decimal(str(raw.get("c", "0"))),
                volume=int(raw.get("v", 0)),
                vwap=Decimal(str(raw["vw"])) if raw.get("vw") else None,
                trade_count=raw.get("n"),
            )

        latest_bar = (
            _parse_bar(lb)
            if lb
            else Bar(
                symbol=symbol,
                timestamp=datetime.utcnow(),
                open=quote.mid,
                high=quote.mid,
                low=quote.mid,
                close=quote.mid,
                volume=0,
            )
        )

        prev_close: Decimal | None = None
        if pc:
            prev_close = Decimal(str(pc.get("c", "0")))

        return Snapshot(
            symbol=symbol,
            quote=quote,
            latest_bar=latest_bar,
            daily_bar=_parse_bar(db) if db else None,
            prev_close=prev_close,
        )

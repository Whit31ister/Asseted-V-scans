"""Multi-source passive subdomain discovery."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any

import aiohttp

LOGGER = logging.getLogger(__name__)
SourceCallback = Callable[[str, bool, int, str | None], None]
CRT_SH_URL = "https://crt.sh/"
CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"
OTX_URL = "https://otx.alienvault.com/api/v1/indicators/domain/{target}/passive_dns"
HACKERTARGET_URL = "https://api.hackertarget.com/hostsearch/"


def normalize_subdomain(value: str, target: str) -> str | None:
    """Return a safe, normalized hostname belonging to target."""
    hostname = value.strip().lower().rstrip(".")
    if hostname.startswith("*."):
        hostname = hostname[2:]
    if not hostname or hostname == target:
        return None
    if hostname.endswith(f".{target}") and "*" not in hostname and " " not in hostname:
        return hostname
    return None


def parse_crtsh(records: list[dict[str, Any]], target: str) -> set[str]:
    return {
        normalized
        for record in records
        for value in str(record.get("name_value", "")).splitlines()
        if (normalized := normalize_subdomain(value, target))
    }


def parse_certspotter(records: list[dict[str, Any]], target: str) -> set[str]:
    return {
        normalized
        for record in records
        for value in record.get("dns_names", [])
        if (normalized := normalize_subdomain(str(value), target))
    }


def parse_otx(payload: dict[str, Any], target: str) -> set[str]:
    names: set[str] = set()
    for record in payload.get("passive_dns", []):
        value = record.get("hostname") or record.get("subdomain")
        if value and (normalized := normalize_subdomain(str(value), target)):
            names.add(normalized)
    return names


def parse_hackertarget(body: str, target: str) -> set[str]:
    names: set[str] = set()
    for line in body.splitlines():
        value = line.split(",", 1)[0].strip()
        if value and (normalized := normalize_subdomain(value, target)):
            names.add(normalized)
    return names


async def _fetch_json(
    session: aiohttp.ClientSession,
    url: str,
    params: dict[str, str],
    parser: Callable[[Any, str], set[str]],
    target: str,
) -> set[str]:
    async with session.get(url, params=params) as response:
        response.raise_for_status()
        payload = await response.json(content_type=None)
    return parser(payload, target)


async def _fetch_hackertarget(
    session: aiohttp.ClientSession, target: str
) -> set[str]:
    async with session.get(HACKERTARGET_URL, params={"q": target}) as response:
        response.raise_for_status()
        body = await response.text()
    if body.startswith("error"):
        raise RuntimeError(body.strip())
    return parse_hackertarget(body, target)


async def discover_subdomains(
    target: str,
    timeout: float = 10.0,
    *,
    on_source: SourceCallback | None = None,
) -> list[str]:
    """Aggregate and deduplicate names from independent passive DNS sources.

    A source outage is isolated from the others. The scan fails only when every
    source fails, preventing one rate-limited provider from hiding valid results.
    ``on_source`` receives ``(name, ok, count, error)`` after each source attempt
    finishes and is optional.
    """
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    source_requests: tuple[tuple[str, Callable[[], Awaitable[set[str]]]], ...] = (
        (
            "crt.sh",
            lambda: _fetch_json(
                session,
                CRT_SH_URL,
                {"q": f"%.{target}", "output": "json"},
                parse_crtsh,
                target,
            ),
        ),
        (
            "Cert Spotter",
            lambda: _fetch_json(
                session,
                CERTSPOTTER_URL,
                {"domain": target, "include_subdomains": "true", "expand": "dns_names"},
                parse_certspotter,
                target,
            ),
        ),
        (
            "AlienVault OTX",
            lambda: _fetch_json(
                session,
                OTX_URL.format(target=target),
                {},
                parse_otx,
                target,
            ),
        ),
        ("HackerTarget", lambda: _fetch_hackertarget(session, target)),
    )
    discovered: set[str] = set()
    successful_sources = 0
    async with aiohttp.ClientSession(timeout=request_timeout) as session:
        async def fetch_source(
            name: str, fetch: Callable[[], Awaitable[set[str]]]
        ) -> tuple[bool, set[str]]:
            last_error = "unknown error"
            for attempt in range(2):
                try:
                    result = await fetch()
                    LOGGER.info("%s returned %d subdomains", name, len(result))
                    if on_source is not None:
                        on_source(name, True, len(result), None)
                    return True, result
                except (
                    aiohttp.ClientError,
                    asyncio.TimeoutError,
                    RuntimeError,
                    TypeError,
                    ValueError,
                ) as exc:
                    last_error = str(exc) or type(exc).__name__
                    LOGGER.warning("%s request failed (attempt %d/2): %s", name, attempt + 1, exc)
            if on_source is not None:
                on_source(name, False, 0, last_error)
            return False, set()

        results = await asyncio.gather(
            *(fetch_source(name, fetch) for name, fetch in source_requests)
        )
    for succeeded, result in results:
        if succeeded:
            successful_sources += 1
            discovered.update(result)
    if successful_sources == 0:
        raise RuntimeError("all passive discovery sources failed")
    return sorted(discovered)

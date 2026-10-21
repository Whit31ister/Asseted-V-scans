"""Certificate-transparency subdomain discovery."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

import aiohttp

LOGGER = logging.getLogger(__name__)
CRT_SH_URL = "https://crt.sh/"
CERTSPOTTER_URL = "https://api.certspotter.com/v1/issuances"


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


async def discover_subdomains(target: str, timeout: float = 10.0) -> list[str]:
    """Fetch unique names from public CT APIs, using Cert Spotter as a fallback."""
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    requests = (
        (CRT_SH_URL, {"q": f"%.{target}", "output": "json"}, "name_value"),
        (
            CERTSPOTTER_URL,
            {"domain": target, "include_subdomains": "true", "expand": "dns_names"},
            "dns_names",
        ),
    )
    errors: list[Exception] = []
    async with aiohttp.ClientSession(timeout=request_timeout) as session:
        for url, params, field in requests:
            for attempt in range(2):
                try:
                    async with session.get(url, params=params) as response:
                        response.raise_for_status()
                        records: list[dict[str, Any]] = await response.json(content_type=None)
                    values = (
                        name
                        for record in records
                        for name in (
                            record.get(field, [])
                            if field == "dns_names"
                            else str(record.get(field, "")).splitlines()
                        )
                    )
                    names = {
                        normalized
                        for value in values
                        if (normalized := normalize_subdomain(str(value), target))
                    }
                    return sorted(names)
                except (aiohttp.ClientError, asyncio.TimeoutError, ValueError) as exc:
                    errors.append(exc)
                    LOGGER.warning(
                        "Certificate transparency request failed for %s (attempt %d/2): %s",
                        url,
                        attempt + 1,
                        exc,
                    )
    error_summary = "; ".join(str(error) for error in errors[-2:])
    raise RuntimeError(f"could not query certificate transparency logs: {error_summary}")

"""Lightweight HTTP and HTTPS metadata probing."""

from __future__ import annotations

import asyncio
import re
from collections.abc import Callable
from typing import TypedDict

import aiohttp


class ProbeResult(TypedDict):
    url: str
    status: int
    title: str | None
    server: str | None


ProbeCallback = Callable[[int, int, str, list[ProbeResult]], None]
TITLE_RE = re.compile(r"<title[^>]*>(.*?)</title\s*>", re.IGNORECASE | re.DOTALL)
MAX_BODY_BYTES = 256 * 1024


def extract_title(body: str) -> str | None:
    match = TITLE_RE.search(body)
    if not match:
        return None
    title = " ".join(match.group(1).split())
    return title[:300] or None


async def probe_one(
    hostname: str, timeout: float = 10.0, session: aiohttp.ClientSession | None = None
) -> list[ProbeResult]:
    """Try HTTPS then HTTP and return metadata for reachable endpoints."""
    own_session = session is None
    if own_session:
        session = aiohttp.ClientSession(
            timeout=aiohttp.ClientTimeout(total=timeout),
            headers={"User-Agent": "asset-monitor/1.0"},
        )
    assert session is not None
    results: list[ProbeResult] = []
    try:
        for scheme in ("https", "http"):
            url = f"{scheme}://{hostname}/"
            try:
                async with session.get(url, allow_redirects=True) as response:
                    body = await response.content.read(MAX_BODY_BYTES)
                    charset = response.charset or "utf-8"
                    results.append(
                        {
                            "url": str(response.url),
                            "status": response.status,
                            "title": extract_title(body.decode(charset, errors="replace")),
                            "server": response.headers.get("Server"),
                        }
                    )
            except (aiohttp.ClientError, asyncio.TimeoutError, UnicodeError):
                continue
    finally:
        if own_session:
            await session.close()
    return results


async def probe_assets(
    hostnames: list[str],
    concurrency: int = 50,
    timeout: float = 10.0,
    *,
    on_result: ProbeCallback | None = None,
) -> dict[str, list[ProbeResult]]:
    """Probe active hostnames concurrently without failing the whole scan.

    ``on_result`` receives ``(completed, total, hostname, probes)`` after each
    hostname and is optional.
    """
    semaphore = asyncio.Semaphore(concurrency)
    total = len(hostnames)
    completed = 0
    results: dict[str, list[ProbeResult]] = {}
    async with aiohttp.ClientSession(
        timeout=aiohttp.ClientTimeout(total=timeout),
        headers={"User-Agent": "asset-monitor/1.0"},
    ) as session:

        async def limited(hostname: str) -> None:
            nonlocal completed
            async with semaphore:
                probes = await probe_one(hostname, timeout, session)
            completed += 1
            if probes:
                results[hostname] = probes
            if on_result is not None:
                on_result(completed, total, hostname, probes)

        await asyncio.gather(*(limited(hostname) for hostname in hostnames))
    return dict(sorted(results.items()))

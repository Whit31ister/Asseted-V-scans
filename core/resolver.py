"""Concurrent DNS resolution."""

from __future__ import annotations

import asyncio
from typing import TypedDict

import dns.asyncresolver
import dns.exception


class Asset(TypedDict):
    hostname: str
    addresses: list[str]


async def resolve_one(hostname: str, timeout: float) -> Asset | None:
    resolver = dns.asyncresolver.Resolver()
    addresses: set[str] = set()
    for record_type in ("A", "AAAA"):
        try:
            answer = await resolver.resolve(hostname, record_type, lifetime=timeout)
            addresses.update(str(item) for item in answer)
        except (dns.exception.DNSException, asyncio.TimeoutError):
            continue
    if not addresses:
        return None
    return {"hostname": hostname, "addresses": sorted(addresses)}


async def resolve_subdomains(
    hostnames: list[str], concurrency: int = 50, timeout: float = 10.0
) -> list[Asset]:
    """Resolve hostnames concurrently, excluding names with no A/AAAA records."""
    semaphore = asyncio.Semaphore(concurrency)

    async def limited(hostname: str) -> Asset | None:
        async with semaphore:
            return await resolve_one(hostname, timeout)

    results = await asyncio.gather(*(limited(hostname) for hostname in hostnames))
    return sorted((result for result in results if result), key=lambda item: item["hostname"])

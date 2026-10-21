"""Webhook notifications."""

from __future__ import annotations

import aiohttp

from core.resolver import Asset


async def send_webhook(
    url: str, target: str, assets: list[Asset], timeout: float = 10.0
) -> None:
    content = "\n".join(
        f"`{asset['hostname']}` -> {', '.join(asset['addresses'])}" for asset in assets
    )
    payload = {"content": f"New assets for **{target}**:\n{content}"}
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=request_timeout) as session:
        async with session.post(url, json=payload) as response:
            response.raise_for_status()

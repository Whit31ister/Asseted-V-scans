"""Webhook notifications."""

from __future__ import annotations

import aiohttp

from core.prober import ProbeResult
from core.resolver import Asset


def _split_message(message: str, limit: int) -> list[str]:
    chunks: list[str] = []
    current = ""
    for line in message.splitlines():
        if len(line) > limit:
            line = line[: limit - 3] + "..."
        if current and len(current) + len(line) + 1 > limit:
            chunks.append(current)
            current = ""
        current = f"{current}\n{line}".strip()
    if current:
        chunks.append(current)
    return chunks


async def send_webhook(
    url: str,
    target: str,
    assets: list[Asset],
    probes: dict[str, list[ProbeResult]] | None = None,
    timeout: float = 10.0,
) -> None:
    lines = [
        f"`{asset['hostname']}` -> {', '.join(asset['addresses'])}" for asset in assets
    ]
    for hostname, results in (probes or {}).items():
        for result in results:
            lines.append(
                f"`{hostname}` [{result['status']}] {result['url']}"
                f" title={result['title'] or '-'} server={result['server'] or '-'}"
            )
    message = f"Asset monitor update for {target}:\n" + "\n".join(lines)
    is_telegram = "/bot" in url and "api.telegram.org" in url
    if is_telegram:
        try:
            token, chat_id = url.rsplit("/bot", 1)[1].split("/", 1)
        except ValueError as exc:
            raise ValueError("--telegram must use BOT_TOKEN/CHAT_ID") from exc
        if not token or not chat_id or "/" in chat_id:
            raise ValueError("--telegram must use BOT_TOKEN/CHAT_ID")
        url = f"https://api.telegram.org/bot{token}/sendMessage"
        messages = _split_message(message, 4000)
    else:
        messages = _split_message(message, 1900)
    request_timeout = aiohttp.ClientTimeout(total=timeout)
    async with aiohttp.ClientSession(timeout=request_timeout) as session:
        for text in messages:
            payload = {"chat_id": chat_id, "text": text} if is_telegram else {"content": text}
            async with session.post(url, json=payload) as response:
                response.raise_for_status()

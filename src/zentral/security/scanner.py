"""Malware scanning for attachments.

``clamav`` talks to a clamd daemon over TCP with the INSTREAM command: the file
is streamed in chunks and never touches a shared disk. ``none`` marks every
file clean and is refused in production.
"""

from __future__ import annotations

import asyncio
import struct
from dataclasses import dataclass

from zentral.config import Settings
from zentral.connectors.base import ConnectorError

CHUNK = 64 * 1024
TIMEOUT_SECONDS = 60


@dataclass(frozen=True, slots=True)
class ScanResult:
    clean: bool
    signature: str = ""  # the scanner's name for what it found (no content)


class Scanner:
    name = "none"

    async def scan(self, data: bytes) -> ScanResult:
        return ScanResult(clean=True)


class ClamdScanner(Scanner):
    name = "clamav"

    def __init__(self, host: str, port: int) -> None:
        self.host, self.port = host, port

    async def scan(self, data: bytes) -> ScanResult:
        try:
            async with asyncio.timeout(TIMEOUT_SECONDS):
                reader, writer = await asyncio.open_connection(self.host, self.port)
                try:
                    writer.write(b"zINSTREAM\0")
                    for start in range(0, len(data), CHUNK):
                        chunk = data[start : start + CHUNK]
                        writer.write(struct.pack(">I", len(chunk)) + chunk)
                        await writer.drain()
                    writer.write(struct.pack(">I", 0))
                    await writer.drain()
                    reply = (await reader.read(4096)).rstrip(b"\0").decode("ascii", "replace")
                finally:
                    writer.close()
        except (OSError, TimeoutError) as exc:
            # Retried by the worker; the file stays unavailable meanwhile.
            raise ConnectorError(f"malware scanner unreachable: {type(exc).__name__}") from None
        # "stream: OK" | "stream: <Signature> FOUND" | "... ERROR"
        if reply.endswith(" OK"):
            return ScanResult(clean=True)
        if reply.endswith(" FOUND"):
            signature = reply.removeprefix("stream: ").removesuffix(" FOUND")[:100]
            return ScanResult(clean=False, signature=signature)
        raise ConnectorError("malware scanner returned an error")


def build_scanner(settings: Settings) -> Scanner:
    if settings.attachment_scanner == "clamav":
        return ClamdScanner(settings.clamav_host, settings.clamav_port)
    return Scanner()

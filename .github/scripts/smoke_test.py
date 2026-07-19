"""CI smoke test: real end-to-end synthesis against a running container.

Not a pytest test (pytest never boots a container) -- invoked directly by
job-docker.yml's smoke-test job after the image is up and healthy.
"""

import asyncio
import sys

from wyoming.audio import AudioChunk
from wyoming.client import AsyncTcpClient
from wyoming.tts import Synthesize, SynthesizeStopped

# Bounds each individual read, not the whole synthesis: if the container
# wedges mid-request instead of closing the connection or erroring, an
# unbounded `read_event()` would otherwise hang until GitHub's default
# 360-minute job timeout.
READ_TIMEOUT_SECONDS = 120


async def main() -> None:
    async with AsyncTcpClient("127.0.0.1", 10200) as client:
        await client.write_event(Synthesize(text="This is a smoke test.").event())
        chunk_count = 0
        total_bytes = 0
        while True:
            try:
                event = await asyncio.wait_for(
                    client.read_event(), timeout=READ_TIMEOUT_SECONDS
                )
            except TimeoutError:
                print(
                    f"No event received within {READ_TIMEOUT_SECONDS}s -- synthesis wedged",
                    file=sys.stderr,
                )
                sys.exit(1)
            if event is None:
                print("Connection closed before SynthesizeStopped", file=sys.stderr)
                sys.exit(1)
            if AudioChunk.is_type(event.type):
                chunk_count += 1
                total_bytes += len(AudioChunk.from_event(event).audio)
            elif SynthesizeStopped.is_type(event.type):
                break

    if chunk_count == 0 or total_bytes == 0:
        print(f"FAIL: {chunk_count} chunk(s), {total_bytes} byte(s)", file=sys.stderr)
        sys.exit(1)
    print(f"OK: {chunk_count} chunk(s), {total_bytes} byte(s)")


if __name__ == "__main__":
    asyncio.run(main())

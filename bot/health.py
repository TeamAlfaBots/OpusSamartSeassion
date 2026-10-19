"""Tiny HTTP health endpoint (GET/HEAD on any path) for Render, standard library only."""
import asyncio
import json
import logging

log = logging.getLogger("smart-session-bot")

_BODY = json.dumps({"status": "ok", "service": "Smart String Session Generator"}).encode()


async def _handle(reader, writer):
    try:
        request_line = await asyncio.wait_for(reader.readline(), 5)
        while True:  # drain the headers
            line = await asyncio.wait_for(reader.readline(), 5)
            if line in (b"\r\n", b"\n", b""):
                break
        method = request_line.split(b" ", 1)[0].upper()
        head = (
            b"HTTP/1.1 200 OK\r\nContent-Type: application/json\r\n"
            b"Content-Length: " + str(len(_BODY)).encode() + b"\r\nConnection: close\r\n\r\n"
        )
        writer.write(head + (b"" if method == b"HEAD" else _BODY))
        await writer.drain()
    except Exception:
        pass
    finally:
        writer.close()
        try:
            await writer.wait_closed()
        except Exception:
            pass


async def start_health_server(port):
    server = await asyncio.start_server(_handle, "0.0.0.0", port)
    log.info("Health server listening on port %s", port)
    return server
  

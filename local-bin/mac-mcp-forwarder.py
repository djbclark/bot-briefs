#!/usr/bin/env python3
"""Plain-HTTP -> HTTPS reverse forwarder for the Mac MCP endpoints over Tailscale.

Listens on 127.0.0.1:18800 (only). Each client connection carries ONE request:
the request head is rewritten (Host -> real upstream host, Connection: close),
then bytes are piped verbatim both ways to
https://<mac>.<tailnet>.ts.net:8443 via the tailscaled SOCKS5 proxy
(localhost:1055) with full TLS verification + SNI. The response is streamed
byte-for-byte (SSE / chunked pass through unbuffered); only its Connection
header is forced to "close". No headers or bodies are logged.
GET /__fwd_health is answered locally (200 "ok").
"""
import asyncio, os, ssl, struct, sys, time

LISTEN_HOST = "127.0.0.1"
LISTEN_PORT = int(os.environ.get("MAC_FWD_PORT", "18800"))
UP_HOST = os.environ.get("MAC_FWD_UPSTREAM_HOST", "<mac>.<tailnet>.ts.net")
UP_PORT = int(os.environ.get("MAC_FWD_UPSTREAM_PORT", "8443"))
SOCKS_HOST, SOCKS_PORT = "127.0.0.1", 1055
MAX_HEAD = 64 * 1024
CONNECT_TIMEOUT = 20
# After the box resumes from a pause, tailscaled holds dead DERP/control
# connections for ~45-60s and SOCKS connects fail. Retry within this window,
# nudging tailscaled with `debug rebind` (rate-limited) so it reconnects sooner.
RETRY_WINDOW = float(os.environ.get("MAC_FWD_RETRY_WINDOW", "60"))
ATTEMPT_TIMEOUT = 8
REBIND_MIN_INTERVAL = 15
TS_CLI = ["/home/box/.local/bin/tailscale", "--socket=/home/box/.local/share/tailscale/tailscaled.sock"]
_last_rebind = 0.0
HOP = {b"connection", b"keep-alive", b"proxy-connection", b"proxy-authorization", b"te", b"upgrade"}

SSL_CTX = ssl.create_default_context()  # verifies certs + hostname
SSL_CTX.set_alpn_protocols(["http/1.1"])


def log(msg):
    print(f"{time.strftime('%Y-%m-%dT%H:%M:%S%z')} {msg}", flush=True)


async def read_head(reader):
    data = await reader.readuntil(b"\r\n\r\n")
    if len(data) > MAX_HEAD:
        raise ValueError("head too large")
    return data


def rewrite_request_head(head: bytes) -> tuple[bytes, bytes, bytes]:
    lines = head[:-4].split(b"\r\n")
    method, target, version = lines[0].split(b" ", 2)
    if target.startswith(b"http://") or target.startswith(b"https://"):
        # absolute-form -> origin-form
        rest = target.split(b"://", 1)[1]
        target = b"/" + rest.split(b"/", 1)[1] if b"/" in rest else b"/"
    out = [b" ".join((method, target, b"HTTP/1.1"))]
    conn_tokens = set()
    for l in lines[1:]:
        if b":" in l and l.split(b":", 1)[0].strip().lower() == b"connection":
            conn_tokens |= {t.strip().lower() for t in l.split(b":", 1)[1].split(b",")}
    for l in lines[1:]:
        if not l or b":" not in l:
            continue
        name = l.split(b":", 1)[0].strip().lower()
        if name in HOP or name == b"host" or name in conn_tokens:
            continue
        out.append(l)
    hostval = UP_HOST.encode() + (b"" if UP_PORT == 443 else b":%d" % UP_PORT)
    out.insert(1, b"Host: " + hostval)
    out.append(b"Connection: close")
    return b"\r\n".join(out) + b"\r\n\r\n", method, target


def rewrite_response_head(head: bytes) -> bytes:
    lines = head[:-4].split(b"\r\n")
    out = [lines[0]]
    for l in lines[1:]:
        if b":" in l and l.split(b":", 1)[0].strip().lower() in (b"connection", b"keep-alive"):
            continue
        out.append(l)
    out.append(b"Connection: close")
    return b"\r\n".join(out) + b"\r\n\r\n"


async def socks5_tls_connect(timeout=CONNECT_TIMEOUT):
    CONNECT_TIMEOUT = timeout
    r, w = await asyncio.wait_for(asyncio.open_connection(SOCKS_HOST, SOCKS_PORT), CONNECT_TIMEOUT)
    try:
        w.write(b"\x05\x01\x00")
        await w.drain()
        resp = await asyncio.wait_for(r.readexactly(2), CONNECT_TIMEOUT)
        if resp != b"\x05\x00":
            raise ConnectionError(f"socks5 auth negotiation failed: {resp!r}")
        hb = UP_HOST.encode()
        w.write(b"\x05\x01\x00\x03" + bytes([len(hb)]) + hb + struct.pack(">H", UP_PORT))
        await w.drain()
        hdr = await asyncio.wait_for(r.readexactly(4), CONNECT_TIMEOUT)
        if hdr[1] != 0:
            raise ConnectionError(f"socks5 connect failed, rep={hdr[1]}")
        atyp = hdr[3]
        if atyp == 1:
            await r.readexactly(4 + 2)
        elif atyp == 4:
            await r.readexactly(16 + 2)
        elif atyp == 3:
            n = (await r.readexactly(1))[0]
            await r.readexactly(n + 2)
        await asyncio.wait_for(w.start_tls(SSL_CTX, server_hostname=UP_HOST), CONNECT_TIMEOUT)
        return r, w
    except BaseException:
        w.close()
        raise


async def ts_rebind():
    global _last_rebind
    now = time.monotonic()
    if now - _last_rebind < REBIND_MIN_INTERVAL:
        return
    _last_rebind = now
    try:
        p = await asyncio.create_subprocess_exec(*TS_CLI, "debug", "rebind",
                                                 stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL)
        await asyncio.wait_for(p.wait(), 10)
        log(f"tailscale debug rebind -> rc={p.returncode}")
    except Exception as e:
        log(f"tailscale debug rebind failed: {type(e).__name__}: {e}")


async def connect_with_retry():
    deadline = time.monotonic() + RETRY_WINDOW
    attempt = 0
    while True:
        attempt += 1
        left = deadline - time.monotonic()
        try:
            rw = await socks5_tls_connect(max(2.0, min(ATTEMPT_TIMEOUT, left)))
            if attempt > 1:
                log(f"upstream connect ok after {attempt} attempts")
            return rw
        except ssl.SSLCertVerificationError:
            raise
        except Exception as e:
            left = deadline - time.monotonic()
            log(f"upstream connect error (attempt {attempt}, {left:.0f}s left): {type(e).__name__}: {e}")
            if left <= 1:
                raise
            await ts_rebind()
            await asyncio.sleep(min(3.0, max(0.0, deadline - time.monotonic() - 1)))


async def pipe(src, dst):
    try:
        while True:
            chunk = await src.read(65536)
            if not chunk:
                break
            dst.write(chunk)
            await dst.drain()
    except (ConnectionError, asyncio.IncompleteReadError, ssl.SSLError, OSError):
        pass
    finally:
        try:
            if dst.can_write_eof():
                dst.write_eof()
        except Exception:
            pass


async def simple_response(w, status, body):
    w.write(b"HTTP/1.1 %s\r\nContent-Type: text/plain\r\nContent-Length: %d\r\nConnection: close\r\n\r\n%s"
            % (status, len(body), body))
    try:
        await w.drain()
    except Exception:
        pass


async def handle(cr, cw):
    uw = None
    t0 = time.monotonic()
    method = target = b"?"
    status = b"?"
    try:
        try:
            head = await asyncio.wait_for(read_head(cr), 60)
        except (asyncio.IncompleteReadError, asyncio.TimeoutError, asyncio.LimitOverrunError, ValueError):
            return
        new_head, method, target = rewrite_request_head(head)
        if target == b"/__fwd_health":
            await simple_response(cw, b"200 OK", b"ok\n")
            status = b"200(local)"
            return
        try:
            ur, uw = await connect_with_retry()
        except Exception as e:
            log(f"upstream connect gave up: {type(e).__name__}: {e}")
            await simple_response(cw, b"502 Bad Gateway", f"forwarder upstream error: {type(e).__name__}\n".encode())
            status = b"502(local)"
            return
        uw.write(new_head)
        await uw.drain()
        up_task = asyncio.create_task(pipe(cr, uw))  # request body (any framing) streamed as-is
        try:
            rhead = await ur.readuntil(b"\r\n\r\n")
        except (asyncio.IncompleteReadError, asyncio.LimitOverrunError) as e:
            up_task.cancel()
            await simple_response(cw, b"502 Bad Gateway", b"forwarder: bad upstream response\n")
            status = b"502(local)"
            return
        status = rhead.split(b"\r\n", 1)[0].split(b" ")[1] if b" " in rhead else b"?"
        cw.write(rewrite_response_head(rhead))
        await cw.drain()
        await pipe(ur, cw)  # streams until upstream closes (Connection: close)
        up_task.cancel()
    except Exception as e:
        log(f"handler error: {type(e).__name__}: {e}")
    finally:
        for w in (uw, cw):
            if w is not None:
                try:
                    w.close()
                except Exception:
                    pass
        # path only (no query string), never headers
        path = target.split(b"?", 1)[0].decode("latin-1", "replace")
        log(f"{method.decode('latin-1','replace')} {path} -> {status.decode('latin-1','replace')} {time.monotonic()-t0:.2f}s")


async def main():
    srv = await asyncio.start_server(handle, LISTEN_HOST, LISTEN_PORT, limit=MAX_HEAD, reuse_address=True)
    log(f"listening on {LISTEN_HOST}:{LISTEN_PORT} -> https://{UP_HOST}:{UP_PORT} via socks5 {SOCKS_HOST}:{SOCKS_PORT}")
    async with srv:
        await srv.serve_forever()


if __name__ == "__main__":
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        sys.exit(0)

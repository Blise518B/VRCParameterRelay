"""Hermetic test of the self-hosted exposure path (docs/REMOTE-ACCESS.md).

The bare address carries no secret: a password door, an HttpOnly cookie,
per-address blocking with proxy-aware identity, a wrong-password cap across
everyone that signed-in guests never feel, and one-button revoke —
plus the admin panel staying LAN-only through all of it.

Throwaway data dir, loopback only, no VRChat. Exits 0 on success.
"""
from __future__ import annotations

import asyncio
import json
import os
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent.parent))
os.environ["VRC_PARAMETER_RELAY_DATA_DIR"] = tempfile.mkdtemp(prefix="vrcpr-door-")

import aiohttp

from vrc_parameter_relay.core import AppCore
from vrc_parameter_relay.store import SHARE_WORDS, Store, new_share_password
from vrc_parameter_relay.webserver import (
    AUTH_FAILS, PASSWORD_FAILS_GLOBAL, PASSWORD_WINDOW_GLOBAL_S, GuestServer,
)

FAILS: list[str] = []


def check(desc: str, cond: bool) -> None:
    print(("OK   " if cond else "FAIL ") + desc)
    if not cond:
        FAILS.append(desc)


class StubLink:
    on_param = on_avatar = on_full_sync = on_status = None

    def __init__(self) -> None:
        self.sent: list = []

    def send_param(self, name, ptype, value) -> bool:
        self.sent.append((name, ptype, value))
        return True


def main() -> None:
    # -- the password itself ------------------------------------------------
    check("wordlist has no duplicates", len(set(SHARE_WORDS)) == len(SHARE_WORDS))
    check("wordlist is lowercase ascii",
          all(w.isalpha() and w.islower() for w in SHARE_WORDS))
    seen = {new_share_password() for _ in range(300)}
    check("passwords are random (300 draws, >290 distinct)", len(seen) > 290)
    for pw in seen:
        parts = pw.split("-")
        assert len(parts) == 4 and parts[3].isdigit() and len(parts[3]) == 2, pw
        assert len(set(parts[:3])) == 3, pw
    combos = len(SHARE_WORDS) ** 3 * 90
    check(f"three distinct words + two digits ({combos / 1e6:.0f}M combinations)", True)

    store = Store()
    check("a share password is generated on first run",
          bool(store.settings.get("share_password")))
    store.set("trusted_proxy", "127.0.0.1")   # pretend our test client is the proxy
    link = StubLink()
    core = AppCore(store, link)
    web = GuestServer(core, 0)
    web.start()
    deadline = time.time() + 10
    while not web.port and time.time() < deadline:
        time.sleep(0.05)
    check("server bound", bool(web.port))
    core._on_avatar_change("avtr_door_test")
    core._on_osc_param("Hoodie", "Bool", False)
    ctrl = core.add_control("Hoodie", "toggle")
    core.set_sharing(True)
    base = f"http://127.0.0.1:{web.port}"
    token = core.guest_token()
    admin = core.admin_token()
    password = core.share_password()

    class NoRedirect(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *a, **k):
            return None
    opener = urllib.request.build_opener(NoRedirect)

    def raw(path, headers=None, cookie=None):
        req = urllib.request.Request(base + path)
        for k, v in (headers or {}).items():
            req.add_header(k, v)
        if cookie:
            req.add_header("Cookie", cookie)
        try:
            with opener.open(req, timeout=5) as r:
                return r.status, r.headers.get("Set-Cookie", ""), r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.headers.get("Set-Cookie", ""), e.read()

    # -- the door -----------------------------------------------------------
    code, _, body = raw("/")
    check("bare GET / -> 401 password door", code == 401 and b"password" in body.lower())
    for _ in range(12):
        raw("/")
    check("12 bare visits never block (a first visit is not an attack)",
          not web._blocked("127.0.0.1"))
    check("wrong password -> 403",
          raw("/", {"X-Share-Password": "nope-nope-nope-11"})[0] == 403)
    check("...and it IS counted", bool(web._fails.get("127.0.0.1")))
    code, sc, _ = raw("/", {"X-Share-Password": f"  {password.upper()} "})
    check("right password (case/space forgiven) -> 204", code == 204)
    check("cookie is HttpOnly + SameSite=Lax",
          "vrcpr_share=" in sc and "HttpOnly" in sc and "SameSite=Lax" in sc)
    check("signing in forgives earlier fumbles", not web._fails.get("127.0.0.1"))
    cookie = sc.split(";")[0]
    check("cookie alone opens the board (200)", raw("/", cookie=cookie)[0] == 200)

    # -- one-click link -----------------------------------------------------
    code, sc2, _ = raw(f"/?k={token}", {"Accept": "text/html"})
    check("browser on a ?k= link -> 302 to the bare URL, cookie planted",
          code == 302 and "vrcpr_share=" in sc2)
    check("a script on a ?k= link (no html Accept) -> 200, not bounced",
          raw(f"/?k={token}")[0] == 200)
    check("a wrong ?k= is refused and counted",
          raw("/?k=wrong")[0] == 403 and bool(web._fails.get("127.0.0.1")))
    web._fails.clear()

    # -- per-address blocking, proxy-aware ------------------------------------
    scanner, innocent = "203.0.113.5", "198.51.100.7"
    codes = [raw("/?k=wrong", {"X-Forwarded-For": scanner})[0]
             for _ in range(AUTH_FAILS)]
    check(f"{AUTH_FAILS} wrong tokens from one forwarded address -> block engaged",
          codes == [403] * AUTH_FAILS and web._blocked(scanner))
    check("blocked scanner stays blocked EVEN with the right token",
          raw(f"/?k={token}", {"X-Forwarded-For": scanner})[0] == 429)
    check("innocent forwarded address unaffected (per address, not global)",
          raw(f"/?k={token}", {"X-Forwarded-For": innocent})[0] == 200)
    check("the host itself (no XFF) unaffected", raw(f"/?k={token}")[0] == 200)
    store.set("trusted_proxy", "")
    raw("/?k=wrong", {"X-Forwarded-For": "203.0.113.99"})
    check("a tunnel/proxy on this machine is believed with no configuration",
          bool(web._fails.get("203.0.113.99")))
    from types import SimpleNamespace
    spoof = SimpleNamespace(remote="93.184.216.34",   # a real public peer
                            headers={"X-Forwarded-For": "203.0.113.99"})
    check("XFF from a PUBLIC peer is ignored (a spoof can't shift blame)",
          web._client_ip(spoof) == "93.184.216.34")
    store.set("trusted_proxy", "127.0.0.1")
    web._fails.clear()
    web._blocks.clear()

    # -- websocket: cookie or token, never nothing ----------------------------
    async def ws_first(path, headers=None):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(base + path, headers=headers or {}) as ws:
                return json.loads((await asyncio.wait_for(ws.receive(), 5)).data)

    check("/ws with the cookie -> hello",
          asyncio.run(ws_first("/ws", {"Cookie": cookie}))["t"] == "hello")
    check("/ws with the token -> hello",
          asyncio.run(ws_first(f"/ws?k={token}"))["t"] == "hello")
    check("/ws with nothing -> denied", asyncio.run(ws_first("/ws"))["t"] == "denied")

    async def ws_send(path, headers, payload):
        async with aiohttp.ClientSession() as s:
            async with s.ws_connect(base + path, headers=headers) as ws:
                await asyncio.wait_for(ws.receive(), 5)
                await ws.send_str(payload)
                await asyncio.sleep(0.3)
    link.sent.clear()
    asyncio.run(ws_send("/ws", {"Cookie": cookie},
                        '{"t":"set","id":"%s","value":Infinity}' % ctrl["id"]))
    check("JSON Infinity/NaN is refused before it can reach VRChat", link.sent == [])

    # -- admin stays LAN-only and separate ------------------------------------
    check("the guest cookie does NOT open /admin",
          raw("/admin", cookie=cookie)[0] == 403)
    check("a proxied (forwarded) request is refused /admin even with the admin token",
          raw(f"/admin?k={admin}", {"X-Forwarded-For": innocent})[0] == 403)
    check("a connection FROM the proxy address is refused /admin (it is the public path)", raw(f"/admin?k={admin}")[0] == 403)
    store.set("trusted_proxy", "")   # 127.0.0.1 stood in for the proxy above; a real host is not it
    check("a direct local admin request still works",
          raw(f"/admin?k={admin}")[0] == 200)

    # -- global wrong-password cap: a fresh address per guess doesn't help ------
    web._fails.clear()
    web._blocks.clear()
    web._pw_fails.clear()
    codes = [raw("/", {"X-Share-Password": f"wrong-guess-nr-{i:02d}",
                       "X-Forwarded-For": f"203.0.113.{i + 1}"})[0]
             for i in range(PASSWORD_FAILS_GLOBAL)]
    check(f"{PASSWORD_FAILS_GLOBAL} wrong passwords, each from a new address -> all 403",
          codes == [403] * PASSWORD_FAILS_GLOBAL)
    check("...none of those addresses reached its own block",
          not any(web._blocked(f"203.0.113.{i + 1}") for i in range(PASSWORD_FAILS_GLOBAL)))
    req = urllib.request.Request(base + "/")
    req.add_header("X-Share-Password", password)
    req.add_header("X-Forwarded-For", "198.51.100.200")
    try:
        opener.open(req, timeout=5)
        status, retry, body, sc = 200, None, b"", ""
    except urllib.error.HTTPError as e:
        status, retry, body = e.code, e.headers.get("Retry-After"), e.read()
        sc = e.headers.get("Set-Cookie", "")
    check("then even the RIGHT password from a new address -> 429 'busy'",
          status == 429 and body == b"busy" and "vrcpr_share=" not in sc)
    check("...with a Retry-After of at most the window",
          retry is not None and 0 < int(retry) <= PASSWORD_WINDOW_GLOBAL_S)
    check("refused attempts aren't counted (the pause can't be stretched)",
          len(web._pw_fails) == PASSWORD_FAILS_GLOBAL)
    check("a signed-in browser (cookie) still opens the board", raw("/", cookie=cookie)[0] == 200)
    check("...and its websocket still connects",
          asyncio.run(ws_first("/ws", {"Cookie": cookie}))["t"] == "hello")
    check("a one-click token link still works", raw(f"/?k={token}")[0] == 200)
    # let the window pass: shift every recorded failure back in time
    aged = [t - PASSWORD_WINDOW_GLOBAL_S - 1 for t in web._pw_fails]
    web._pw_fails.clear()
    web._pw_fails.extend(aged)
    code, sc, _ = raw("/", {"X-Share-Password": password,
                            "X-Forwarded-For": "198.51.100.200"})
    check("once the window has passed, the right password signs in again",
          code == 204 and "vrcpr_share=" in sc)
    web._fails.clear()
    web._pw_fails.clear()

    # -- revoke: one button ends every session ---------------------------------
    old_pw = core.share_password()
    core.regenerate_share_password()
    check("New password: a new one is minted", core.share_password() != old_pw)
    check("...but a signed-in browser stays in (cookie carries the TOKEN)",
          raw("/", cookie=cookie)[0] == 200)
    core.regenerate_token()
    check("Reset link: the old cookie -> 401 door", raw("/", cookie=cookie)[0] == 401)
    check("a stale cookie is NOT counted as an attack", not web._blocked("127.0.0.1"))
    check("Reset link: the old one-click link is dead", raw(f"/?k={token}")[0] == 403)

    print("\n" + ("ALL SHARE-DOOR TESTS PASSED" if not FAILS
                  else f"{len(FAILS)} SHARE-DOOR TEST(S) FAILED"))
    sys.exit(1 if FAILS else 0)


if __name__ == "__main__":
    main()

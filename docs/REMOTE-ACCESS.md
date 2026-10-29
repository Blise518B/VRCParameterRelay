# Your own address — a permanent share link on your own domain

The Share button's quick tunnel is fine for a one-off: click, get a random
`https://something-random.trycloudflare.com` URL, hand it over. But the URL
changes every run and it dies when the app closes.

If you have a domain, you can share on **your own address instead —
`https://relay.example.com`, the same link forever.**

## In the app: one field

**Share → Your own address** → type `https://relay.example.com` → Enter.

That's all the app needs. From then on:

* no tunnel is started — the link you hand out **is** that address;
* guests see a **password door** the first time (the password is shown right
  under the link) and their browser stays signed in for a month;
* the app's web server opens to your LAN on port **3080**, so whatever routes
  your address to this PC can reach it. Windows may ask whether to let the
  app through the firewall — allow it on **private** networks.

The hint under the field shows exactly where to point your address
(`http://<this PC's LAN IP>:3080`). Empty the field to go back to the tunnel.

Typing just `relay.example.com` means `https://`. A plain `http://` address
works too, but the app warns you: the password then crosses the internet
unencrypted.

The rest of this page is about the part outside the app — **getting your
address to this PC.** Pick one.

---

## A. Cloudflare Tunnel (recommended)

No port forwarding, works behind carrier-grade NAT / DS-Lite, HTTPS for
free, and guests never see your home IP. Needs the domain's DNS on
Cloudflare (the free plan is enough).

1. In the Cloudflare dashboard, open **Zero Trust → Networks → Tunnels** and
   create a tunnel (type *cloudflared*). Menu names move around now and then
   — look for "Tunnels".
2. Install the connector on **this PC** with the command Cloudflare shows
   you. It runs as a Windows service, so it starts with the PC.
3. Add a **public hostname** (newer dashboards may call it a *published
   application route*): subdomain `relay`, your domain, service type
   **HTTP**, URL **`localhost:3080`**.
4. In the app: **Your own address** = `https://relay.example.com`.

The admin panel stays unreachable through the tunnel — the app refuses
anything that arrives through a proxy.

## B. A reverse proxy you already run

You have a box on the LAN (NAS, Pi, …) that already terminates HTTPS for
your other services, and your router forwards port 443 to it.

```
guest's phone
      │  https://avatar.example.de   (no secret in the address)
      ▼
   your router  ── port 443 (the only thing open) ──▶  reverse proxy
                                                            │  http, LAN only
                                                            ▼
                                              this app on the gaming PC :3080
```

**DNS:** one dynamic A record tracks the house (most routers update one),
every service is a CNAME onto it:

| Name | Type | Value | TTL |
|---|---|---|---|
| `home` | A | *(your router's DynDNS updates this)* | 60 |
| `avatar` | CNAME | `home.example.de` | 300 |

**Router:** 443 (and 80 if your proxy issues certificates over HTTP-01) →
the proxy machine. **Give the PC a fixed address** (a DHCP reservation) —
otherwise a lease change silently points the proxy at the wrong machine.
**Do not forward the app's port.** Needs a real public IPv4; behind
carrier-grade NAT / DS-Lite use option A.

**Caddy example:**

```caddyfile
avatar.example.de {
	# Only this app's own GUEST routes exist here. Everything else —
	# scanners probing /wp-login.php, /.env — and, deliberately, /admin —
	# is answered here and never reaches the PC.
	@app path / /ws /health /favicon.ico /static/* /fonts/*
	@notget not method GET

	handle @notget {
		respond 405        # the app has no POST/PUT/DELETE surface
	}

	handle @app {
		request_body {
			max_size 8KB   # no route reads a body; this is a backstop
		}
		reverse_proxy 192.168.1.50:3080 {
			# Overwrite, never append: a client can send its own
			# X-Forwarded-For. {remote_host} is who really connected.
			header_up X-Forwarded-For {remote_host}
		}
	}

	handle {
		respond 404
	}
}
```

Replace the hostname and `192.168.1.50:3080` with your own. Reload Caddy
**after** the DNS record is live, so the certificate can be issued. Caddy
will warn *"Unnecessary header_up X-Forwarded-For"*
— keep the line anyway; it replaces the header instead of appending to it.

Using nginx/Traefik instead? Pass WebSocket upgrades through, don't buffer
the response, and set `X-Forwarded-For` to the real client address.

**Optional — lock the app's port to the proxy** (elevated PowerShell):

```powershell
New-NetFirewallRule -DisplayName "VRC Parameter Relay (from proxy)" -Direction Inbound -Protocol TCP -LocalPort 3080 -RemoteAddress 192.168.1.10 -Action Allow
```

**Optional — a page for when the app is off.** With nothing running on the
PC the proxy has nowhere to forward and guests get its bare error page.
`docs/caddy-offline-block.txt` holds a ready `handle_errors` block — paste
it inside the site block. It says the relay isn't running, checks `/health`
every 5 seconds and loads the board by itself the moment the app comes up.
(Source: `vrc_parameter_relay/web/offline.html`; regenerate the snippet
with `tools/make_offline_block.py`.)

## C. Plain port forward (testing only)

Forward port 3080 on the router to this PC and enter
`http://your-ddns-name:3080`. It works, but there is no HTTPS: the password
travels in the clear and the app's own web server faces the open internet.
Fine to try things out; use A or B for real.

---

## How guests get in

**The address itself carries no secret.** It shows a password door; the
guest types the password once and their browser keeps a cookie for a month.

That is not decoration: **your hostname is public the moment it has a
certificate** — every certificate lands in public Certificate Transparency
logs, and bots probe new names from those logs within minutes. "Nobody knows
the address" was never protection; the password is. Keeping the secret out
of the URL also keeps it out of proxy logs, browser history, screenshares
and every chat the link is forwarded into.

| Button (Share window) | What it does |
|---|---|
| **Copy** (address) | the clean link — what you hand out |
| **Copy** (password) | the password; say it or paste it |
| **New password** | future sign-ins need the new one; people already signed in stay in |
| **Copy one-click link** | carries the token, skips the password. The app plants the cookie and strips it from the address bar on arrival — but it is a live secret while it sits in a chat |
| **Reset link** | new token: ends every session everywhere, one-click links included |

## What protects it

| Layer | What it stops |
|---|---|
| Password door on the bare address | anyone who found the hostname in a transparency log |
| Token / cookie gate | every route except `/health` and static assets |
| Cookie (`HttpOnly`, `SameSite=Lax`, `Secure` over HTTPS) | the secret never enters a URL, a log or a history |
| **8 wrong passwords or tokens → 15 min block, per address** | one visitor guessing (~150 million password combinations) |
| **30 wrong passwords a minute across everyone → new sign-ins pause** | guessing from many addresses at once — it caps the whole world at ~43,000 guesses a day, years for the full space. Signed-in guests never notice: their cookie doesn't ask for the password |
| Admin panel LAN-only | anything arriving through a tunnel, a proxy or a forwarded port is refused `/admin`, even with the admin link |
| 32 guests / 40 messages per second each | one client hogging the board |
| 16 KB WebSocket cap, no `Infinity`/`NaN` | malformed or oversized guest messages |
| **Reset link** | one click revokes every link ever handed out and drops everyone |

The per-address block needs the guest's real address. Behind a tunnel or
proxy every guest arrives from the proxy, so the app believes
`X-Forwarded-For` — but only from this machine or the LAN (where a tunnel or
proxy runs), never from a peer on the internet, and it takes the entry the
proxy added itself. A proxy outside your LAN can be listed in `settings.json`
as `trusted_proxy`. If a setup ever lets someone fake that address (some
routers make every forwarded connection look like it comes from the router
itself), the per-everyone cap above still holds.

**Guests can only do what the board offers** — the controls you placed,
never off-board parameters; locked categories are read-only.

**One trade-off with B and C:** your hostname resolves to your home IP, so
anyone you share the link with can see it. Option A hides it.

---

## When it doesn't work

| Symptom | Cause |
|---|---|
| Cloudflare error 1033 / "tunnel not connected" | the cloudflared service isn't running on this PC |
| Cloudflare 502 / bad gateway | the app isn't running, or the hostname's service isn't `HTTP` → `localhost:3080` |
| Certificate error / proxy won't start the site | DNS record not live yet, or 80/443 not reaching the proxy |
| 404 from the proxy | hostname isn't in the config, or the path isn't in the allowlist |
| 502 / connection refused from your proxy | app not running, wrong LAN IP, or Windows Firewall blocks port 3080 |
| Page loads, board stays empty | WebSocket blocked — the proxy isn't passing upgrades through |
| Works on mobile data, not at home | no NAT hairpinning on your router — use the "on this network" link at home |
| Everyone suddenly gets the door | the token was rotated (Reset link) |
| One guest gets "too many wrong links" | they're blocked for 15 minutes; an old link, or a typo |
| New guests get "too many sign-in attempts right now" | someone is guessing the password; it clears within a minute of the guessing stopping. Hand out the one-click link, or press **New password** |

# Mobile access

The intended setup: the PC runs everything, the phone is a client on the same Wi-Fi.

## LAN development

```bash
pnpm dev:lan
```

This binds the API to `0.0.0.0:8000` and Vite to `0.0.0.0:5173`. Find your PC's LAN
address:

```bash
ipconfig
```

Open `http://<pc-lan-ip>:5173` on the phone.

Add that origin to `CORS_ORIGINS` in `.env`, since it differs from `localhost`:

```dotenv
CORS_ORIGINS=http://localhost:5173,http://127.0.0.1:5173,http://192.168.1.50:5173
```

Windows Firewall will prompt on first run — allow **private networks only**.

## Why the phone only knows one port

Vite proxies `/api` and `/health` to the backend, so the browser makes same-origin
requests and the phone never needs to know the API port.

More importantly, **the application backend is the security boundary**. Ollama and
ComfyUI are reached only by the backend, over loopback. Neither is exposed to the phone.

## Security

Ollama and ComfyUI have **no authentication**. Anyone who can reach those ports can run
inference, read models, and — with ComfyUI — write files.

- **Never** port-forward `11434` or `8188` to the internet.
- Do not bind them to `0.0.0.0` "just to test from the phone". The proxy already solves
  that, correctly.
- `pnpm dev:lan` binds the API to all interfaces. That is fine on a home network and
  wrong on a café network. There is no authentication on the API either — it is a
  single-player local tool.

## PWA and HTTPS

Service workers and installability require a **secure context**: HTTPS, or `localhost`.

- On the PC at `http://localhost:5173` — the PWA installs and the service worker runs.
- On the phone at `http://192.168.x.x:5173` — the app works normally, but the service
  worker will not register and you cannot install it to the home screen.

The service worker precaches the app shell only. AI responses are never cached: serving
a stale turn would corrupt a playthrough. When the backend is unreachable, the header
shows an offline banner rather than pretending.

To get a real PWA on the phone, put HTTPS in front. Reasonable options, roughly in order
of effort:

1. **Tailscale** (or another WireGuard mesh) with its HTTPS certificates — a private
   network, no public exposure, and a real certificate. Best fit for this project.
2. **Caddy** locally with a self-signed CA installed on the phone.
3. A tunnel such as Cloudflare Tunnel — this makes the app **publicly reachable**, and
   the API has no authentication. Do not do this without adding auth first.

Production hosting, authentication, and native packaging are out of scope for this
iteration; see [roadmap.md](roadmap.md) Phase 7.

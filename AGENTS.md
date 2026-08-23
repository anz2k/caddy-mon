# AI Agent Instructions

## 1. Project Overview & Tech Stack
- **Role:** You are an expert Python backend developer working on a Caddy reverse-proxy visibility dashboard.
- **What it does:** Polls the Caddy admin API and shows each proxied site's alive/dead status, latency, and last-check time on a single web page. No Grafana, no Prometheus.
- **Tech Stack:** Python 3.12 (slim), FastAPI, uvicorn, httpx. Packaged as a single Docker container.
- **Runtime:** Runs on the same Docker network as `caddy-proxy` (`caddy_default`) so the DNS name `caddy-proxy` resolves and the admin API (port 2019) is reachable.
- **Package Manager:** Use `pip` / `Dockerfile` (no external package manager needed).

## 2. Core Commands
Use these to verify your work before declaring a task finished:
- Build: `docker compose up -d --build`
- Run locally: `uvicorn app:app --host 0.0.0.0 --port 8080` (outside Docker, needs Caddy reachable)
- Health check: `curl -s localhost:8080/api/state | python3 -m json.tool`
- Lint: `python3 -m py_compile app.py` (no formal linter configured)

## 3. Coding Style & Preferences
- **Language:** All code, comments, docstrings, UI strings, and docs MUST be English. Never write Estonian (or any other non-English) text in the repo.
- **Caddy data source:** Always read from the Caddy admin API (`http://caddy-proxy:2019`), never parse the Caddyfile directly. Use:
  - `/config/apps/http/servers/srv0/routes` for routes + upstreams
  - `/metrics` for `caddy_reverse_proxy_upstreams_healthy{upstream="IP:port"}` (0/1)
- **Health logic:** Caddy `healthy` metric is authoritative. A failed self-probe is a false negative (e.g. Immich does not answer plain HTTP). If Caddy reports no health (`None`), the probe becomes the only signal.
- **No hardcoded infra:** Never put real LAN IPs (e.g. `192.168.x.x`) or internal hostnames in the repo. Use placeholders like `<upstream-ip>` or `<deployment-dir>` in docs.
- **Example pattern (parsing healthy metric):**
```python
def _parse_healthy(metrics_text: str) -> dict:
    result = {}
    for line in (metrics_text or "").splitlines():
        if line.startswith("caddy_reverse_proxy_upstreams_healthy"):
            label_part = line.split("{", 1)[1].split("}", 1)[0]
            val = line.rsplit(" ", 1)[-1].strip()
            key = label_part.split("=", 1)[1].strip('"')
            result[key] = (val == "1")
    return result
```

## 4. Operational Boundaries & Rules
- **Be concise:** Do not explain standard Python/FastAPI patterns. Focus only on the unique changes.
- **No breaking changes:** Never change the `/api/state` response shape or the `CADDY_API` contract without asking first.
- **No deletions:** Never delete `LICENSE`, `.gitignore`, or `AGENTS.md`.
- **Security:** Never hardcode secrets. There are no secrets in this project; if one is needed, use `.env` + `python-dotenv` and document it in `.env.example`.
- **Repo hygiene:** Never commit Estonian text, private IPs, or hostnames. The repo is public.
- **Server update:** After pushing to `main`, the deployment host is updated automatically (no need to ask). The deployment dir is a clone of this repo; it is not part of the public source.
- **Caddyfile changes:** Never edit the Caddy Caddyfile or restart Caddy unless explicitly asked. The dashboard reads from the admin API; it does not modify Caddy config.

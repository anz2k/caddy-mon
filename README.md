# caddy-mon — minimal Caddy reverse-proxy visibility

Lihtne, Grafana-vaba vaade sinu Caddy proxy-le. Näitab ühel veebilehel iga
proxitud saidi seisu: elab/katki, latents, viimane kontroll.

## Mis see teeb
- Küsib Caddy admin-API-st (`caddy-proxy:2019`) iga 10s:
  - kõik ruudid + nende host-matcherid ja upstream-id
  - `caddy_reverse_proxy_upstreams_healthy` mõõdik (0/1 iga upstreami kohta)
- Teeb ise kiire HTTP GET proovi igale upstreamile (latents + HTTP staatus)
- Serveerib veebilehte `:8080` (einfach: roheline/punane tuli iga saidi kohta)

Ei kasuta Prometheusit, Grafanat ega TSDB-d. Ainult Caddy, mis sul juba on.

## Käivitus (docker02, sinu kasutajana)
```bash
cd ~/stacks/caddy-mon
docker compose up -d --build
# ava http://<server-ip>:8080
```

## Konfig
`docker-compose.yml`:
- liitub võrguga `caddy_default` (et `caddy-proxy` nimi laheneks DNS-is)
- mountib `/home/andres.kaaber/stacks/caddy/logs` kirjutuskaitstud (logiotsing, valikuline)
- pordi `8080` ei ole vaja WAN-i avada — vaid sisevõrgust

## Eemaldamine
```bash
docker compose down
```

## Tehniline märkus
Caddy `/metrics` ei sisalda `caddy_http_*` liiklusmõõdikuid (ainult
admin/reverse_proxy_healthy + Go runtime), seega:
- seis = `caddy_reverse_proxy_upstreams_healthy` (juba Caddy poolt arvutatud)
- latents = ise tehtud GET proov (ei sõltu Caddy logist)
- tulevikus: access.log parsimine (rate/4xx%) saab lisada ilma arhitektuuri muutmata

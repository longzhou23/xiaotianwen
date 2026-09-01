# Xiaotianwen domain gateway

This gateway exposes the three browser-facing services through Caddy:

| Hostname | Upstream |
| --- | --- |
| `astrbot.longzh.top` | `astrbot:6200` |
| `snowluma.longzh.top` | `snowluma:5099` |
| `novnc.longzh.top` | `snowluma:6081` |

The Caddy container joins the existing `xiaotianwen-astrbot_default` Docker
network, so it reaches the service names without publishing additional
container ports. Public TLS is terminated by Cloudflare; Caddy uses an
internal origin certificate, so set Cloudflare SSL/TLS mode to **Full** (use
Cloudflare Origin CA certificates if **Full (strict)** is required).

Before publishing the records, protect all three hostnames with Cloudflare
Access. The existing AstrBot Access application can be updated to keep
`astrbot.longzh.top`; create equivalent self-hosted applications for SnowLuma
and noVNC with the same owner policy.

## Required DNS records

Create proxied (`orange cloud`) A records:

```text
astrbot  -> 172.197.160.79
snowluma -> 172.197.160.79
novnc    -> 172.197.160.79
```

The Azure network security group and host firewall must allow TCP 80 and 443.
After DNS and firewall changes, start or reload the gateway with:

```bash
docker compose -f /home/developer/xiaotianwen/gateway/compose.yml up -d
docker compose -f /home/developer/xiaotianwen/gateway/compose.yml logs -f gateway
```

# ESPHost

**Host a website directly from your ESP32. No server. No cloud. Just your chip.**

```bash
pip install esphost
esphost
```

---

## What it does

1. **Detects** your ESP32 over USB or Wi-Fi automatically
2. **Scans** your site files against real ESP32 hardware limits
3. **Flashes** firmware + uploads files to SPIFFS (survives reboots)
4. **Tunnels** via Cloudflare — gives you a free public URL
5. **Queues** users — max 3 concurrent, overflow gets a live queue page

---

## Requirements

- Python 3.10+
- ESP32 board (4MB flash recommended)
- USB cable (for initial flash) or ESP32 already on Wi-Fi

---

## Install & run

```bash
pip install esphost
esphost
```

That's it. The UI handles everything else.

---

## Linux permission fix

If ESP32 is not detected on Linux:

```bash
sudo usermod -aG dialout $USER
# then log out and back in
```

---

## Custom domain

After deploying, you get a free `trycloudflare.com` URL.  
To use your own domain:

```
CNAME  yourdomain.com  →  yourname.trycloudflare.com
```

---

## Queue behavior

| Users | Behavior |
|-------|----------|
| 1–3   | Direct access to ESP32 |
| 4+    | Queue page with live position update |

Users are auto-admitted when a slot frees up.

---

## License

MIT

# Deploy InstaIQ backend on aaPanel (Python Manager)

Tested path: aaPanel → App Store → **Python Manager** → add project, with
**gunicorn + uvicorn worker** serving `main:app`. Your VPS's residential-ish
IP is actually an advantage here: the keyless Instagram fetch that fails on
Render/Vercel datacenter IPs often works directly.

## 1. Install Python Manager

aaPanel → **App Store** → search **Python Manager** → Install (also installs
gunicorn). Ensure Python **3.10+** is installed (Python version manager in
the same store).

## 2. Get the code

```bash
mkdir -p /www/python && cd /www/python
git clone https://github.com/SurbhiMishra28/instaiq-agent.git instaiq
cd instaiq/backend
```

## 3. Environment

```bash
cp env.aapanel.example .env
nano .env        # fill LLM_API_KEY, etc. (no Instagram token needed)
playwright install chromium   # one-time: browser for the keyless IG fetch
```

## 4. Dependencies

```bash
python3 -m venv /www/python/instaiq/venv
source /www/python/instaiq/venv/bin/activate
pip install -r requirements.txt gunicorn
```

## 5. Python Manager → Add project

| Field | Value |
|---|---|
| Project name | `instaiq-api` |
| Path | `/www/python/instaiq/backend` |
| Python version | 3.10+ (the venv one) |
| Framework | **FastAPI** |
| Startup mode | **gunicorn** |
| Start file | `main.py` (app entry `main:app`) |
| gunicorn config | `/www/python/instaiq/backend/gunicorn.aapanel.conf.py` |
| Port | `8000` |
| Whether to install dependencies | unchecked (done above) |

Start it, then check:

```bash
curl http://127.0.0.1:8000/health     # {"status":"healthy"}
```

## 6. Public site (reverse proxy)

aaPanel → **Website → Add site** (e.g. `api.yourdomain.com`, PHP: pure
static) → site **Settings → Reverse proxy** → target
`http://127.0.0.1:8000`.

Then **SSL** → Let's Encrypt → enable *Force HTTPS*.

## 7. Firewall / security

- aaPanel **Security** page: allow `80, 443` (and SSH port). Do **not**
  expose 8000 publicly — only the reverse proxy needs it.
- Cloud provider firewall/security group: same.

## 8. Verify from outside

```bash
curl https://api.yourdomain.com/health
curl -X POST https://api.yourdomain.com/api/chat \
     -H "Content-Type: application/json" \
     -d '{"message":"hi","handle":null}'
```

## 9. Point the frontend at it

Set `VITE_API_URL=https://api.yourdomain.com` (Vercel project env + commit
`frontend/.env.production`), redeploy the UI.

## Notes

- **Updates**: `cd /www/python/instaiq && git pull` → restart in Python
  Manager (or set up a deploy webhoook).
- **Logs**: Python Manager shows runtime logs; `/api/usage` shows whether
  the LLM key and gateways are configured.
- **CORS**: backend allows all origins, so the Vercel frontend works
  against this domain without changes.
- **Persistence**: SQLite caches live in the backend folder — they survive
  restarts, unlike Vercel/Render ephemeral filesystems. Backup the folder.

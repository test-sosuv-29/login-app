# Login App

A small Flask app with email/password auth and TOTP two-factor authentication
(compatible with Google Authenticator, Authy, 1Password, etc.).

## Requirements

- Python 3.11+

## Local development

```bash
python -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt

# Optional: copy env defaults
cp .env.example .env

python app.py
```

The app starts on http://localhost:5000 (it auto-selects a free port if 5000 is
busy). The SQLite database (`users.db`) is created automatically on first run.

## Running the tests

```bash
python -m unittest discover -s tests
```

## Configuration

All configuration is via environment variables:

| Variable      | Default                       | Description                                                        |
| ------------- | ----------------------------- | ------------------------------------------------------------------ |
| `SECRET_KEY`  | `dev-insecure-secret-change-me` | **Set this in production.** Signs session cookies.               |
| `DATABASE`    | `users.db`                    | Path to the SQLite database file.                                  |
| `PORT`        | `5000`                        | Port to bind. Most PaaS platforms inject this automatically.       |
| `FLASK_DEBUG` | `1`                           | Only affects `python app.py`. Set to `0` outside local dev.        |

Generate a strong secret key with:

```bash
python -c "import secrets; print(secrets.token_hex(32))"
```

## Deployment

The app exposes a WSGI callable at `app:app` and is served with
[gunicorn](https://gunicorn.org/) in production.

### Any platform (Render / Railway / Heroku / Fly.io)

A `Procfile` is included:

```
web: gunicorn app:app --bind 0.0.0.0:${PORT:-5000} --workers 2 --timeout 60
```

Set `SECRET_KEY` (and any other overrides) in the platform's environment
settings, then deploy.

### Docker

```bash
docker build -t login-app .
docker run -p 5000:5000 \
  -e SECRET_KEY="$(python -c 'import secrets; print(secrets.token_hex(32))')" \
  -v login-app-data:/data \
  login-app
```

The container stores the database at `/data/users.db` and exposes `/data` as a
volume so accounts persist across restarts and redeploys.

### Manual (systemd, bare VM, etc.)

```bash
pip install -r requirements.txt
export SECRET_KEY="...your-random-value..."
gunicorn app:app --bind 0.0.0.0:5000 --workers 2 --timeout 60
```

## Notes

- **Persistence:** the app uses SQLite. On platforms with ephemeral filesystems
  (e.g. Heroku dynos, some free tiers), the database is wiped on restart. Point
  `DATABASE` at a persistent volume, or migrate to a managed database, if you
  need durable accounts.
- Put the app behind HTTPS in production (via the platform's load balancer or a
  reverse proxy) so session cookies and TOTP codes are not sent in cleartext.

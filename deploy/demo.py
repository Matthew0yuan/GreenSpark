"""The demo on a public address: the app in Docker behind a Cloudflare quick tunnel (deploy/README.md).

    .venv/Scripts/python.exe deploy/demo.py up      build and start; the first time, copy var/live into the volume
    .venv/Scripts/python.exe deploy/demo.py url     the public address and the login
    .venv/Scripts/python.exe deploy/demo.py down    stop both containers (the volume, with the live data, stays)

The login (GS_AUTH) is made once, at random, in deploy/.env (git-ignored). The quick tunnel's address changes every time
the tunnel container starts. Standard library only: it runs before any container exists.
"""

import base64
import json
import re
import secrets
import subprocess
import sys
import time
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
ENV = ROOT / "deploy" / ".env"
VOLUME = "greenspark-var"  # = compose.yaml volumes.var.name
IMAGE = "greenspark-app:demo"
COMPOSE = ["docker", "compose", "-f", str(ROOT / "deploy" / "compose.yaml")]
ADDRESS = re.compile(r"https://[-a-z0-9]+\.trycloudflare\.com")


def sh(*cmd: str, check: bool = True, quiet: bool = False) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check, text=True, encoding="utf-8", errors="replace", capture_output=quiet)


def settings() -> dict:
    if not ENV.exists():
        ENV.write_text(f"GS_AUTH=demo:{secrets.token_urlsafe(12)}\nGS_PORT=8080\n", encoding="utf-8")
    pairs = (line.split("=", 1) for line in ENV.read_text(encoding="utf-8").splitlines() if "=" in line)
    return {k.strip(): v.strip() for k, v in pairs if not k.startswith("#")}


def meta(env: dict) -> dict:
    auth = base64.b64encode(env.get("GS_AUTH", "").encode()).decode()
    req = urllib.request.Request(f"http://127.0.0.1:{env.get('GS_PORT', '8080')}/api/v1/meta",
                                 headers={"Authorization": f"Basic {auth}"})
    with urllib.request.urlopen(req, timeout=10) as r:
        return json.loads(r.read())


def seed() -> None:
    """The first time: the volume gets this computer's live data (var/live) and operator decisions (var/app.db)."""
    if sh("docker", "volume", "inspect", VOLUME, check=False, quiet=True).returncode == 0:
        return
    var = ROOT / "var"
    if not (var / "live" / "state.json").exists():
        raise SystemExit("no live data on this computer: run .venv/Scripts/python.exe -m app.live.run --init first")
    print(f"copying var/live into the Docker volume {VOLUME} (once, about 1 GB) ...", flush=True)
    sh("docker", "volume", "create", VOLUME, quiet=True)
    copy = ("mkdir -p /app/var/live && cp -r --preserve=timestamps /seed/live/data /seed/live/state.json /app/var/live/"
            " && if [ -f /seed/app.db ]; then cp /seed/app.db /app/var/; fi")
    r = sh("docker", "run", "--rm", "--network", "none", "--user", "10001", "--mount", f"type=volume,src={VOLUME},dst=/app/var",
           "--mount", f"type=bind,src={var},dst=/seed,readonly", IMAGE, "sh", "-c", copy, check=False)
    if r.returncode != 0:
        sh("docker", "volume", "rm", VOLUME, check=False, quiet=True)
        raise SystemExit("copying the live data failed (above); nothing was started")


def address(wait: float = 0) -> str | None:
    t = time.time()
    while True:
        logs = sh(*COMPOSE, "logs", "--no-log-prefix", "tunnel", check=False, quiet=True)
        found = ADDRESS.findall(logs.stdout + logs.stderr)
        if found or time.time() - t >= wait:
            return found[-1] if found else None  # the newest: the tunnel container may have restarted
        time.sleep(2)


def show(env: dict, url: str | None) -> None:
    user, _, password = env.get("GS_AUTH", "").partition(":")
    print(f"\n  public address  {url or '(the tunnel has not reported one yet: run demo.py url in a minute)'}")
    print(f"  this computer   http://127.0.0.1:{env.get('GS_PORT', '8080')}")
    print(f"  login           user {user}   password {password}\n" if user else
          "  login           off: anyone with the address can open it (GS_AUTH is empty in deploy/.env)\n")


def up() -> None:
    env = settings()
    if sh("docker", "info", check=False, quiet=True).returncode != 0:
        raise SystemExit("Docker is not running: start Docker Desktop, then run this again")
    sh(*COMPOSE, "build", "app")
    seed()
    sh(*COMPOSE, "up", "-d")
    print("waiting for the engines (about a minute) ...", flush=True)
    t = time.time()
    while time.time() - t < 600:
        try:
            st = meta(env)["engines"]
            if all(v["state"] == "ready" for v in st.values()):
                break
            if any(v["state"] == "failed" for v in st.values()):
                raise SystemExit(f"an engine failed: {st}; see: docker compose -f deploy/compose.yaml logs app")
        except (urllib.error.URLError, ConnectionError, TimeoutError, json.JSONDecodeError):
            pass
        time.sleep(3)
    else:
        raise SystemExit("the app did not come up in 10 minutes; see: docker compose -f deploy/compose.yaml logs app")
    show(env, address(wait=120))


def main(argv: list[str]) -> None:
    cmd = argv[0] if argv else "up"
    if cmd == "up":
        up()
    elif cmd == "url":
        show(settings(), address())
    elif cmd == "down":
        sh(*COMPOSE, "down")
    else:
        raise SystemExit(__doc__)


if __name__ == "__main__":
    main(sys.argv[1:])

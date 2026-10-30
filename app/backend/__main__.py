"""Run the app:  .venv/Scripts/python.exe -m app.backend [--port 8000] [--host 127.0.0.1]

The default host is this machine only; the Docker image (deploy/) listens on 0.0.0.0 inside its container."""

import sys

import uvicorn


def _arg(name: str, default: str) -> str:
    return sys.argv[sys.argv.index(name) + 1] if name in sys.argv else default


if __name__ == "__main__":
    uvicorn.run("app.backend.main:app", host=_arg("--host", "127.0.0.1"), port=int(_arg("--port", "8000")),
                log_level="info")

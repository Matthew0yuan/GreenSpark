"""Run the app on this machine only:  .venv/Scripts/python.exe -m app.backend [--port 8000]"""

import sys

import uvicorn

if __name__ == "__main__":
    port = int(sys.argv[sys.argv.index("--port") + 1]) if "--port" in sys.argv else 8000
    uvicorn.run("app.backend.main:app", host="127.0.0.1", port=port, log_level="info")

"""Read an AEMO WA public directory listing (IIS HTML) into rows of (name, url, listed_at, size).

`listed_at` is the file's modified time as the listing shows it, in AWST (checked 2026-10-17: outage
snapshot GeneratorOutages_202309290930.csv, whose own ExtractDateTime is +08:00, is listed at 09:31).
It is evidence of when a file was public only where it has not been re-packaged later; each source
decides how far to trust it (see the download scripts).
"""

import datetime as dt
import html
import re
import urllib.request

BASE = "https://data.wa.aemo.com.au"
AGENT = {"User-Agent": "greenspark-hackathon/0.1"}
ROW = re.compile(r'(\d{1,2}/\d{1,2}/\d{4})\s+(\d{1,2}:\d{2}\s+[AP]M)\s+(&lt;dir&gt;|\d+)\s+<A HREF="([^"]+)">([^<]*)</A>', re.I)


def fetch(url: str, timeout: int = 300, first_bytes: int | None = None) -> bytes:
    """The file, or only its first `first_bytes` bytes (HTTP Range)."""
    headers = AGENT if first_bytes is None else {**AGENT, "Range": f"bytes=0-{first_bytes - 1}"}
    with urllib.request.urlopen(urllib.request.Request(url, headers=headers), timeout=timeout) as resp:
        return resp.read()


def listing(path: str) -> list[dict]:
    """Files (not sub-directories) in BASE/path, with their listed modified time."""
    text = fetch(f"{BASE}/{path.strip('/')}/").decode("utf-8", "replace")
    rows = []
    for day, clock, size, href, name in ROW.findall(text):
        if size.lower() == "&lt;dir&gt;":
            continue
        rows.append({
            "name": html.unescape(name), "url": BASE + html.unescape(href),
            "listed_at": dt.datetime.strptime(f"{day} {clock}", "%m/%d/%Y %I:%M %p"), "size": int(size),
        })
    return rows

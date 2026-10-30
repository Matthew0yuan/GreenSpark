"""Rough PDF-to-text for reading downloaded papers (standard library only; reads the PDF as data, never runs it).

Usage (from repo root):  .venv/bin/python analysis/2026-10-28/spike_literature/_pdftext.py <in.pdf> <out.txt>
Handles FlateDecode streams, object streams, ToUnicode CMaps (bfchar/bfrange) and Tj/TJ/'/" text operators.
Output is for a human to read; layout is approximate. Writes only <out.txt>.
"""
import re
import sys
import zlib

OBJ_RE = re.compile(rb"(\d+)\s+(\d+)\s+obj(.*?)endobj", re.S)


def inflate(raw):
    for wbits in (15, -15):
        try:
            return zlib.decompress(raw, wbits)
        except zlib.error:
            pass
    try:
        return zlib.decompressobj().decompress(raw)
    except zlib.error:
        return b""


def stream_of(body):
    m = re.search(rb"stream\r?\n", body)
    if not m:
        return None, body
    head = body[: m.start()]
    end = body.rfind(b"endstream")
    raw = body[m.end(): end if end > 0 else len(body)]
    if b"FlateDecode" in head:
        return inflate(raw), head
    return raw, head


def load_objects(data):
    objs = {}
    for m in OBJ_RE.finditer(data):
        num = int(m.group(1))
        body = m.group(3)
        content, head = stream_of(body)
        objs[num] = (head, content)
    # object streams
    for num, (head, content) in list(objs.items()):
        if content and b"/ObjStm" in head:
            n = re.search(rb"/N\s+(\d+)", head)
            first = re.search(rb"/First\s+(\d+)", head)
            if not (n and first):
                continue
            n, first = int(n.group(1)), int(first.group(1))
            nums = list(map(int, content[:first].split()))
            pairs = [(nums[i], nums[i + 1]) for i in range(0, min(len(nums), 2 * n), 2)]
            for i, (onum, off) in enumerate(pairs):
                end = pairs[i + 1][1] if i + 1 < len(pairs) else len(content) - first
                objs.setdefault(onum, (content[first + off: first + end], None))
    return objs


def hexbytes(s):
    s = re.sub(rb"\s", b"", s)
    if len(s) % 2:
        s += b"0"
    return bytes.fromhex(s.decode("ascii", "ignore"))


def parse_cmap(text):
    cmap = {}
    width = 1
    for block in re.findall(rb"beginbfchar(.*?)endbfchar", text, re.S):
        for src, dst in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>", block):
            width = max(width, len(src) // 2)
            try:
                cmap[int(src, 16)] = hexbytes(dst).decode("utf-16-be", "ignore")
            except ValueError:
                pass
    for block in re.findall(rb"beginbfrange(.*?)endbfrange", text, re.S):
        for lo, hi, rest in re.findall(rb"<([0-9A-Fa-f]+)>\s*<([0-9A-Fa-f]+)>\s*(\[[^\]]*\]|<[0-9A-Fa-f]+>)", block):
            width = max(width, len(lo) // 2)
            lo_i, hi_i = int(lo, 16), int(hi, 16)
            if rest.startswith(b"["):
                dsts = re.findall(rb"<([0-9A-Fa-f]+)>", rest)
                for k, d in enumerate(dsts):
                    cmap[lo_i + k] = hexbytes(d).decode("utf-16-be", "ignore")
            else:
                base = hexbytes(rest[1:-1])
                bi = int.from_bytes(base, "big")
                for k in range(0, min(hi_i - lo_i, 65535) + 1):
                    v = (bi + k).to_bytes(len(base), "big")
                    cmap[lo_i + k] = v.decode("utf-16-be", "ignore")
    return cmap, width


def font_maps(objs):
    """Map font resource name -> (cmap, width). Names are merged across pages (approximation)."""
    cmaps = {}
    for num, (head, content) in objs.items():
        src = head if content is None else (head or b"")
        if content is not None and b"beginbfchar" in content or (content is not None and b"beginbfrange" in content):
            cmaps[num] = parse_cmap(content)
    font_tu = {}
    for num, (head, content) in objs.items():
        text = head if content is None else head
        if text and b"/Font" in text and b"/ToUnicode" in text:
            m = re.search(rb"/ToUnicode\s+(\d+)\s+0\s+R", text)
            if m and int(m.group(1)) in cmaps:
                font_tu[num] = cmaps[int(m.group(1))]
    names = {}
    for num, (head, content) in objs.items():
        text = head if content is None else head
        if not text:
            continue
        for fd in re.findall(rb"/Font\s*<<(.*?)>>", text, re.S):
            for name, ref in re.findall(rb"/([A-Za-z0-9_+.-]+)\s+(\d+)\s+0\s+R", fd):
                if int(ref) in font_tu:
                    names.setdefault(name, font_tu[int(ref)])
    return names


def unescape(s):
    out = bytearray()
    i = 0
    while i < len(s):
        c = s[i]
        if c == 0x5C and i + 1 < len(s):
            n = s[i + 1]
            mp = {ord("n"): 10, ord("r"): 13, ord("t"): 9, ord("b"): 8, ord("f"): 12}
            if n in mp:
                out.append(mp[n]); i += 2; continue
            if 48 <= n <= 55:
                j = i + 1
                while j < len(s) and j < i + 4 and 48 <= s[j] <= 55:
                    j += 1
                out.append(int(s[i + 1:j], 8) & 255); i = j; continue
            if n in (10, 13):
                i += 2; continue
            out.append(n); i += 2; continue
        out.append(c); i += 1
    return bytes(out)


def decode(raw, fmap):
    if fmap is None:
        return raw.decode("latin-1")
    cmap, width = fmap
    out = []
    for k in range(0, len(raw) - width + 1, width):
        code = int.from_bytes(raw[k:k + width], "big")
        out.append(cmap.get(code, ""))
    return "".join(out)


TOKEN = re.compile(rb"\((?:\\.|[^\\)])*\)|<[0-9A-Fa-f\s]*>|\[|\]|/[^\s/\[\]()<>]+|[^\s/\[\]()<>]+", re.S)


def page_text(content, names):
    out = []
    font = None
    stack = []
    for tok in TOKEN.findall(content):
        if tok.startswith(b"("):
            stack.append(("s", unescape(tok[1:-1])))
        elif tok.startswith(b"<") and not tok.startswith(b"<<"):
            stack.append(("s", hexbytes(tok[1:-1])))
        elif tok == b"[":
            stack.append(("[", None))
        elif tok == b"]":
            arr = []
            while stack and stack[-1][0] != "[":
                arr.append(stack.pop())
            if stack:
                stack.pop()
            arr.reverse()
            stack.append(("a", arr))
        elif tok.startswith(b"/"):
            stack.append(("n", tok[1:]))
        elif tok in (b"Tj", b"'", b'"'):
            if stack and stack[-1][0] == "s":
                out.append(decode(stack[-1][1], names.get(font)))
            if tok != b"Tj":
                out.append("\n")
            stack = []
        elif tok == b"TJ":
            if stack and stack[-1][0] == "a":
                for kind, val in stack[-1][1]:
                    if kind == "s":
                        out.append(decode(val, names.get(font)))
                    elif kind == "x":
                        pass
            stack = []
        elif tok == b"Tf":
            for kind, val in reversed(stack):
                if kind == "n":
                    font = val
                    break
            stack = []
        elif tok in (b"Td", b"TD", b"T*", b"Tm", b"ET"):
            out.append("\n" if tok in (b"T*", b"ET", b"TD") else " ")
            stack = []
        elif re.fullmatch(rb"-?\d*\.?\d+", tok):
            if stack and stack[-1][0] == "[":
                pass
            stack.append(("x", tok))
        else:
            # other operator: inside an array keep numbers, otherwise reset
            if not any(k == "[" for k, _ in stack):
                stack = []
    return "".join(out)


def main(src, dst):
    data = open(src, "rb").read()
    objs = load_objects(data)
    names = font_maps(objs)
    pages = []
    for num, (head, content) in sorted(objs.items()):
        if content and (b"BT" in content and (b"Tj" in content or b"TJ" in content)) and b"beginbfchar" not in content:
            if head and (b"/Subtype /Image" in head or b"/Subtype/Image" in head):
                continue
            pages.append(page_text(content, names))
    with open(dst, "w", encoding="utf-8") as f:
        f.write("\n\n=== stream ===\n".join(pages))


if __name__ == "__main__":
    main(sys.argv[1], sys.argv[2])

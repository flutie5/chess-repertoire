"""One-shot extractor: webapp/static/index.html -> webapp/frontend/."""

from __future__ import annotations

import re
import shutil
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
static = ROOT / "webapp" / "static"
src_html = static / "index.html"
text = src_html.read_text(encoding="utf-8")

frontend = ROOT / "webapp" / "frontend"
(frontend / "src" / "board").mkdir(parents=True, exist_ok=True)
(frontend / "public" / "pieces").mkdir(parents=True, exist_ok=True)
(frontend / "public" / "vendor").mkdir(parents=True, exist_ok=True)

m = re.search(r"<style>(.*?)</style>", text, re.S)
css = m.group(1).strip() if m else ""
(frontend / "src" / "styles.css").write_text(css + "\n", encoding="utf-8")
print("css lines", css.count("\n") + 1)

m = re.search(r'<script type="module">(.*?)</script>\s*</body>', text, re.S)
if not m:
    m = re.search(r"<script type=\"module\">(.*?)</script>", text, re.S)
js = m.group(1).strip() if m else ""
print("js lines", js.count("\n") + 1)

html = re.sub(
    r"<style>.*?</style>",
    '  <link rel="stylesheet" href="/src/styles.css" />',
    text,
    count=1,
    flags=re.S,
)
html = re.sub(
    r'<script type="module">.*?</script>',
    '<script type="module" src="/src/main.ts"></script>',
    html,
    count=1,
    flags=re.S,
)
if 'id="board-live"' not in html:
    html = html.replace(
        '<div id="board"',
        '<div id="board-live" class="sr-only" aria-live="polite" aria-atomic="true"></div>\n'
        '      <div id="board" role="grid" aria-label="Chess board"',
        1,
    )
(frontend / "index.html").write_text(html, encoding="utf-8")

for name in ("openings.json", "_redirects"):
    p = static / name
    if p.exists():
        shutil.copy2(p, frontend / "public" / name)

pieces = static / "pieces"
if pieces.is_dir():
    for p in pieces.iterdir():
        if p.is_file():
            shutil.copy2(p, frontend / "public" / "pieces" / p.name)

vendor = static / "vendor" / "chess.js"
if vendor.exists():
    shutil.copy2(vendor, frontend / "public" / "vendor" / "chess.js")

for p in static.iterdir():
    if p.is_file() and p.suffix.lower() in {".png", ".svg", ".ico", ".webp", ".jpg"}:
        shutil.copy2(p, frontend / "public" / p.name)
        print("copied", p.name)

(frontend / "src" / "_extracted_app.js").write_text(js + "\n", encoding="utf-8")
print("ok", frontend)

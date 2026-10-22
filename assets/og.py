"""Renders assets/og.png, the 1200×630 link-preview image, in jaan.io's dark palette.

  uv run --with playwright python assets/og.py assets/og.png
"""
import sys

from playwright.sync_api import sync_playwright

# jaan.io dark palette (src/styles/tokens.css): --bg, --text, --text-muted, series colours
html = """<!doctype html><html><head>
<link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;700&display=swap" rel="stylesheet">
<style>
html,body{margin:0;width:1200px;height:630px;background:#000;color:#e2e2e2;font-family:Inter,sans-serif}
.wrap{display:flex;align-items:center;gap:72px;height:100%;padding:0 96px;box-sizing:border-box}
svg{width:300px;height:386px;flex:none}
h1{font-size:76px;line-height:1.02;letter-spacing:-.02em;margin:0;font-weight:700}
p{font-size:30px;line-height:1.3;color:#9a9a96;margin:28px 0 0}
p b{color:#24cb71;font-weight:700}
</style></head><body><div class="wrap">
<svg viewBox="15 5 70 90"><g fill="none" stroke-width="3" stroke-linejoin="miter" stroke-linecap="square">
<path stroke="#24cb71" d="M18 8H60L82 30V92H18Z"/><path stroke="#24cb71" d="M60 8V30H82"/>
<path stroke="#e870ed" d="M28 45H58V75H28Z"/>
<path stroke="#fbda00" stroke-linecap="butt" d="M57.5 52.25L72 45V75L57.5 67.75"/></g></svg>
<div><h1>List of Longform Adaptations</h1>
<p>Films and TV series adapted from <b>newspaper and magazine journalism</b></p></div>
</div></body></html>"""
with sync_playwright() as p:
    b = p.chromium.launch()
    pg = b.new_page(viewport={"width": 1200, "height": 630})
    pg.set_content(html, wait_until="networkidle")
    pg.evaluate("document.fonts.ready")
    pg.screenshot(path=sys.argv[1])

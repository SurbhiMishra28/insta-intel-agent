"""Convert PROJECT_DOCUMENTATION.md to a styled PDF via markdown + headless Chrome."""
import pathlib
import subprocess
import sys

import markdown

ROOT = pathlib.Path(__file__).resolve().parent.parent  # insta-intel-agent/
MD = ROOT / "PROJECT_DOCUMENTATION.md"
HTML = ROOT / "_docs_render.html"
OUT = ROOT / "PROJECT_DOCUMENTATION.pdf"

text = MD.read_text(encoding="utf-8")
body = markdown.markdown(
    text,
    extensions=["tables", "fenced_code", "sane_lists", "smarty"],
    output_format="html5",
)

CSS = """
@page { size: A4; margin: 0; }
* { box-sizing: border-box; }
body {
  font-family: 'Segoe UI', Calibri, Arial, sans-serif;
  color: #1a1f24; font-size: 10.5pt; line-height: 1.55;
  margin: 0; padding: 0;
}
.banner {
  background: linear-gradient(135deg, #0f1320 0%, #1c2340 60%, #3b2f63 100%);
  color: #eef1f7; padding: 34px 46px 30px;
  border-bottom: 4px solid #7c5cff;
}
.banner h1 { color: #ffffff; font-size: 24pt; margin: 0 0 6px; border: none; padding: 0; }
.banner .tagline { color: #b9c0d4; font-size: 11pt; margin: 0 0 10px; }
.banner .meta { color: #8f97b3; font-size: 9pt; margin: 0; }
h1 {
  font-size: 16pt; color: #141a2e; border-bottom: 2px solid #7c5cff;
  padding-bottom: 5px; margin: 30px 0 12px;
  break-after: avoid; page-break-after: avoid;
}
h2 { font-size: 13pt; color: #1c2340; margin: 22px 0 8px; break-after: avoid; page-break-after: avoid; }
h3 { font-size: 11.5pt; color: #2a2f45; margin: 16px 0 6px; break-after: avoid; page-break-after: avoid; }
p { margin: 7px 0; }
ul, ol { margin: 7px 0 7px 4px; padding-left: 20px; }
li { margin: 3px 0; }
table {
  border-collapse: collapse; width: 100%; margin: 12px 0;
  font-size: 9.5pt; break-inside: avoid; page-break-inside: avoid;
}
th {
  background: #1c2340; color: #fff; text-align: left;
  padding: 7px 10px; font-weight: 600;
}
td { border: 1px solid #d8dce6; padding: 6px 10px; vertical-align: top; }
tr:nth-child(even) td { background: #f4f5fa; }
code {
  font-family: Consolas, 'Courier New', monospace; font-size: 9pt;
  background: #eef0f6; color: #3b2f63; padding: 1.5px 5px; border-radius: 3px;
}
pre {
  background: #12172a; color: #dce2f2; padding: 13px 15px;
  border-radius: 6px; overflow-x: hidden; white-space: pre-wrap; word-wrap: break-word;
  font-size: 8.5pt; line-height: 1.45; break-inside: avoid; page-break-inside: avoid;
  margin: 10px 0;
}
pre code { background: none; color: inherit; padding: 0; }
blockquote {
  border-left: 4px solid #7c5cff; margin: 10px 0; padding: 6px 14px;
  background: #f4f2fd; color: #3c3c50;
}
strong { color: #141a2e; }
hr { border: none; border-top: 1px solid #d8dce6; margin: 22px 0; }
"""

html = f"""<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<title>InstaIQ — Project Documentation</title>
<style>{CSS}</style>
</head>
<body>
<div class="banner">
  <h1>🤖 InstaIQ — Project Documentation</h1>
  <p class="tagline">AI Instagram Profile &amp; Competitor Intelligence Agent</p>
  <p class="meta">Version 2.0 · LangChain AI engine · Real Instagram data via Apify · Generated September 9, 2026</p>
</div>
<div class="content">{body}</div>
</body>
</html>"""

HTML.write_text(html, encoding="utf-8")

chrome = r"C:\Users\DELL\AppData\Local\Google\Chrome\Application\chrome.exe"
cmd = [
    chrome,
    "--headless=new",
    "--disable-gpu",
    "--no-pdf-header-footer",
    "--print-to-pdf=" + str(OUT),
    "--print-to-pdf-no-header",
    HTML.as_uri(),
]
r = subprocess.run(cmd, capture_output=True, text=True, timeout=120)
if OUT.exists() and OUT.stat().st_size > 1000:
    print(f"PDF written: {OUT} ({OUT.stat().st_size / 1024:.0f} KB)")
    HTML.unlink()
else:
    print("FAILED:", r.returncode, r.stdout[-400:], r.stderr[-400:])
    sys.exit(1)

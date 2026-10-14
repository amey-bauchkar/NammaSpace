"""Render a Markdown document to HTML and PDF (headless Chrome print) and report the page count.

    python scripts/docs/render_pdf.py docs/round1/TECHNICAL_ABSTRACT.md [--max-pages 5]

Uses the `markdown` package (tables, fenced code) and Chrome's --print-to-pdf with a temporary profile.
"""
import argparse
import re
import subprocess
import sys
import tempfile
from pathlib import Path

import markdown

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "browser"))
from cdp import find_chrome  # noqa: E402

CSS = """
@page { size: A4; margin: 15mm 14mm 15mm 14mm; }
body { font: 9.6pt/1.38 "Segoe UI", system-ui, sans-serif; color: #111; }
h1 { font-size: 16pt; margin: 0 0 4pt; } h2 { font-size: 11.5pt; margin: 9pt 0 3pt; border-bottom: 1px solid #ccd; }
h3 { font-size: 10pt; margin: 7pt 0 2pt; } p { margin: 3pt 0; } ul, ol { margin: 2pt 0 3pt 16pt; padding: 0; }
table { border-collapse: collapse; width: 100%; margin: 4pt 0; font-size: 8.4pt; page-break-inside: avoid; }
th, td { border: 1px solid #c8ccd6; padding: 2pt 4pt; vertical-align: top; text-align: left; } th { background: #eef1f6; }
code { font: 8.4pt Consolas, monospace; background: #f3f4f7; padding: 0 2pt; }
pre { font: 8pt Consolas, monospace; background: #f3f4f7; padding: 4pt; white-space: pre-wrap; }
img { max-width: 100%; } .small { font-size: 8pt; color: #444; }
"""


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("src")
    ap.add_argument("--max-pages", type=int, default=0)
    a = ap.parse_args()
    src = Path(a.src).resolve()
    html_body = markdown.markdown(src.read_text(encoding="utf-8"), extensions=["tables", "fenced_code"])
    html = f"<!doctype html><html><head><meta charset='utf-8'><title>{src.stem}</title><style>{CSS}</style></head><body>{html_body}</body></html>"
    out_html = src.with_suffix(".html")
    out_html.write_text(html, encoding="utf-8")
    out_pdf = src.with_suffix(".pdf")
    with tempfile.TemporaryDirectory(prefix="namma-pdf-") as prof:
        subprocess.run([find_chrome(), "--headless=new", f"--user-data-dir={prof}", "--no-first-run", "--disable-extensions",
                        "--no-pdf-header-footer", f"--print-to-pdf={out_pdf}", out_html.as_uri()],
                       check=True, capture_output=True, timeout=120)
    pages = len(re.findall(rb"/Type\s*/Page[^s]", out_pdf.read_bytes()))
    print(f"{out_pdf} pages={pages}")
    if a.max_pages and pages > a.max_pages:
        print(f"OVER THE LIMIT: {pages} > {a.max_pages} pages")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

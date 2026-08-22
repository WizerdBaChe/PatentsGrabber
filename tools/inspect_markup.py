"""Throwaway markup inspector: print the shape of chosen containers in a saved page.

Usage: python tools/inspect_markup.py [number]
"""

from __future__ import annotations

import sys
from pathlib import Path

from bs4 import BeautifulSoup

ROOT = Path(__file__).resolve().parents[1]
number = sys.argv[1] if len(sys.argv) > 1 else "US6285999B1"
html = (ROOT / "var" / "raw" / f"{number}.html").read_text(encoding="utf-8")
soup = BeautifulSoup(html, "lxml")


def show(label: str, node, limit: int = 900) -> None:
    print(f"\n{'=' * 70}\n{label}\n{'=' * 70}")
    if node is None:
        print("  (not found)")
        return
    text = str(node)
    print(text[:limit].replace("><", ">\n<"))
    print(f"  ... [{len(text)} chars total]")


show("section[itemprop=family] (first 900 chars)", soup.find("section", attrs={"itemprop": "family"}))
show("itemprop=inventor", soup.find(attrs={"itemprop": "inventor"}), 400)
show("itemprop=similarDocuments (first row)", soup.find("tr", attrs={"itemprop": "similarDocuments"}), 600)
show("itemprop=events (first)", soup.find(attrs={"itemprop": "events"}), 500)
show("itemprop=assigneeCurrent / assigneeOriginal (first)",
     soup.find(attrs={"itemprop": "assigneeCurrent"}) or soup.find(attrs={"itemprop": "assigneeOriginal"}), 300)
show("section[itemprop=description] head", soup.find("section", attrs={"itemprop": "description"}), 400)

desc = soup.find("section", attrs={"itemprop": "description"})
if desc:
    print(f"\nDESCRIPTION TEXT LENGTH: {len(desc.get_text(' ', strip=True))} chars")

print("\n\n=== dt/dd metadata labels in application section ===")
app = soup.find("section", attrs={"itemprop": "application"}) or soup
for dt in app.find_all("dt")[:20]:
    dd = dt.find_next_sibling("dd")
    print(f"  {dt.get_text(strip=True)[:30]:32} -> {(dd.get_text(' ', strip=True)[:60] if dd else '')}")

"""Google Patents single-document source.

Contract (BR-7, docs/01-concept-note.md): this module fetches ONLY
`/patent/{number}/{lang}` pages, which `patents.google.com/robots.txt` explicitly
allows (`Disallow: /*` + `Allow: /patent/`). It must never be used to drive the
search endpoint.

Parsing strategy: Google Patents marks its page up with schema.org microdata
(`itemprop=...`) plus Highwire `citation_*` meta tags. Both are far more stable
than CSS classes. Every field records WHICH selector produced it, so a coverage
report can distinguish "the page does not have this" from "our parser missed it"
— the two failures look identical in a boolean matrix and must not be conflated.
"""

from __future__ import annotations

import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from bs4 import BeautifulSoup

BASE = "https://patents.google.com/patent/{number}/{lang}"

# Identify honestly as an automated reader rather than spoofing a browser.
HEADERS = {
    "User-Agent": (
        "PatentsGrabber/0.1 (personal research reader; "
        "single-document fetch only, honours robots.txt)"
    ),
    "Accept-Language": "en",
}

MIN_DESCRIPTION_CHARS = 500  # below this, treat description as "stub, not real text"


class FetchError(RuntimeError):
    """The document could not be retrieved (network, HTTP status, or not found)."""


@dataclass
class Field:
    """One extracted value plus the selector that produced it."""

    value: object = None
    selector: str | None = None

    @property
    def present(self) -> bool:
        if self.value is None:
            return False
        if isinstance(self.value, (str, list, dict)):
            return len(self.value) > 0
        return True


@dataclass
class PatentDoc:
    number: str
    url: str
    http_status: int
    raw_path: str | None = None
    fields: dict[str, Field] = field(default_factory=dict)

    def present_map(self) -> dict[str, bool]:
        return {k: v.present for k, v in self.fields.items()}

    def to_dict(self) -> dict:
        return {
            "number": self.number,
            "url": self.url,
            "http_status": self.http_status,
            "raw_path": self.raw_path,
            "fields": {k: asdict(v) for k, v in self.fields.items()},
        }


# --------------------------------------------------------------------------- #
# fetch
# --------------------------------------------------------------------------- #

def fetch_html(
    number: str,
    lang: str = "en",
    *,
    raw_dir: Path | None = None,
    timeout: float = 30.0,
    client: httpx.Client | None = None,
) -> tuple[str, str, int]:
    """Fetch one patent page. Returns (url, html, status).

    The raw HTML is written to disk BEFORE any parsing runs, so a parser bug is
    recoverable without re-hitting the source.
    """
    url = BASE.format(number=number, lang=lang)
    owns_client = client is None
    client = client or httpx.Client(headers=HEADERS, timeout=timeout, follow_redirects=True)
    try:
        resp = client.get(url)
    except httpx.HTTPError as exc:
        raise FetchError(f"{number}: transport failure: {exc}") from exc
    finally:
        if owns_client:
            client.close()

    html = resp.text
    if raw_dir is not None:
        raw_dir.mkdir(parents=True, exist_ok=True)
        (raw_dir / f"{number}.html").write_text(html, encoding="utf-8")

    if resp.status_code != 200:
        raise FetchError(f"{number}: HTTP {resp.status_code}")
    return url, html, resp.status_code


def looks_like_missing(html: str, soup: BeautifulSoup) -> bool:
    """True when the page loaded but describes no document.

    Google Patents answers unknown numbers with a 200 page rather than a 404, so
    an empty card would otherwise sail through as a successful parse.
    """
    if soup.find("meta", attrs={"name": "citation_patent_publication_number"}):
        return False
    if soup.find(attrs={"itemprop": "publicationNumber"}):
        return False
    return True


# --------------------------------------------------------------------------- #
# parse helpers — each returns (value, selector_that_matched)
# --------------------------------------------------------------------------- #

def _meta_name(soup: BeautifulSoup, name: str) -> tuple[str | None, str | None]:
    el = soup.find("meta", attrs={"name": name})
    if el and el.get("content"):
        return el["content"].strip(), f'meta[name="{name}"]'
    return None, None


def _meta_itemprop(soup: BeautifulSoup, prop: str) -> tuple[str | None, str | None]:
    el = soup.find("meta", attrs={"itemprop": prop})
    if el and el.get("content"):
        return el["content"].strip(), f'meta[itemprop="{prop}"]'
    return None, None


def _section_text(
    soup: BeautifulSoup, prop: str, tag: str | None = None
) -> tuple[str | None, str | None]:
    """Text of the element carrying `itemprop=prop`.

    `tag` matters more than it looks: Google reuses itemprop names at different
    depths (e.g. `<span itemprop="description">` inside every classification row
    versus the one `<section itemprop="description">` holding the actual
    specification). An unscoped find silently returns the wrong, much shorter one.
    """
    el = soup.find(tag, attrs={"itemprop": prop}) if tag else soup.find(attrs={"itemprop": prop})
    if el:
        text = el.get_text(" ", strip=True)
        if text:
            return text, f'{tag or "*"}[itemprop="{prop}"]'
    return None, None


def _collect_meta_names(soup: BeautifulSoup, name: str) -> tuple[list[str], str | None]:
    els = soup.find_all("meta", attrs={"name": name})
    vals = [e["content"].strip() for e in els if e.get("content")]
    return vals, (f'meta[name="{name}"] xN' if vals else None)


def _images(soup: BeautifulSoup) -> tuple[list[str], str | None]:
    """Full-resolution drawing URLs."""
    urls = [
        e["content"].strip()
        for e in soup.find_all("meta", attrs={"itemprop": "full"})
        if e.get("content")
    ]
    if urls:
        return urls, '[itemprop="images"] meta[itemprop="full"]'
    # Fallback: any patentimages figure URL anywhere in the document.
    urls = sorted(
        set(
            re.findall(
                r"https://patentimages\.storage\.googleapis\.com/[^\"'\s]+?-D\d{5}\.png",
                str(soup),
            )
        )
    )
    return urls, ("regex:patentimages -D#####.png" if urls else None)


def _classifications(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    out = []
    for li in soup.find_all(attrs={"itemprop": "classifications"}):
        code = li.find("span", attrs={"itemprop": "Code"})
        desc = li.find("span", attrs={"itemprop": "Description"})
        if code and code.get_text(strip=True):
            out.append(
                {
                    "code": code.get_text(strip=True),
                    "description": desc.get_text(" ", strip=True) if desc else None,
                }
            )
    if out:
        # Google repeats parent nodes of the hierarchy; keep the deepest unique codes.
        seen, uniq = set(), []
        for c in out:
            if c["code"] not in seen:
                seen.add(c["code"])
                uniq.append(c)
        return uniq, '[itemprop="classifications"] span[itemprop="Code"]'
    return [], None


def _rows(soup: BeautifulSoup, prop: str) -> tuple[list[str], str | None]:
    """Publication numbers listed under a table itemprop (citations)."""
    nums = []
    for tr in soup.find_all("tr", attrs={"itemprop": prop}):
        cell = tr.find(attrs={"itemprop": "publicationNumber"})
        if cell and cell.get_text(strip=True):
            nums.append(cell.get_text(strip=True))
    return nums, (f'tr[itemprop="{prop}"] [itemprop="publicationNumber"]' if nums else None)


def _cell(row, prop: str) -> str | None:
    el = row.find(attrs={"itemprop": prop})
    return el.get_text(" ", strip=True) if el else None


def _family(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    """Family applications, scoped to the family section.

    Not `alsoPublishedAs` (which this page markup does not use): the family lives
    in `section[itemprop=family]` as `tr[itemprop=applications]` rows.
    """
    section = soup.find("section", attrs={"itemprop": "family"})
    if not section:
        return [], None
    out = []
    for tr in section.find_all("tr", attrs={"itemprop": "applications"}):
        out.append(
            {
                "application_number": _cell(tr, "applicationNumber"),
                "publication": _cell(tr, "representativePublication"),
                "status": _cell(tr, "ifiStatus"),
                "title": _cell(tr, "title"),
                "priority_date": _cell(tr, "priorityDate"),
                "filing_date": _cell(tr, "filingDate"),
            }
        )
    sel = 'section[itemprop="family"] tr[itemprop="applications"]' if out else None
    return out, sel


def _similar(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    """Google's own 'Similar Documents' — directly useful for finding related art."""
    out = []
    for tr in soup.find_all("tr", attrs={"itemprop": "similarDocuments"}):
        out.append(
            {
                "publication": _cell(tr, "publicationNumber"),
                "date": _cell(tr, "publicationDate"),
                "title": _cell(tr, "title"),
            }
        )
    return out, ('tr[itemprop="similarDocuments"]' if out else None)


def _events(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    out = []
    for dd in soup.find_all("dd", attrs={"itemprop": "events"}):
        out.append(
            {
                "date": _cell(dd, "date"),
                "title": _cell(dd, "title"),
                "type": _cell(dd, "type"),
            }
        )
    return out, ('dd[itemprop="events"]' if out else None)


# A dependent claim is one that refers back to another claim. Testing the text is
# more robust than trusting the `claim-dependent` CSS class, which is absent on
# some vintages of the page; we use the class only as corroboration.
_DEPENDS_RE = re.compile(r"\b(?:of|in|to|according to)\s+claims?\s+\d+", re.IGNORECASE)


def _claim_list(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    """Individual claims with independent/dependent marking.

    Independent claims carry the actual scope of the patent, so the reader needs
    them separated out rather than buried in one wall of text.
    """
    section = soup.find("section", attrs={"itemprop": "claims"})
    if not section:
        return [], None
    out = []
    for div in section.find_all("div", class_="claim"):
        num = div.get("num")
        if not num:
            continue  # outer wrapper, not a claim
        text = div.get_text(" ", strip=True)
        body = re.sub(r"^\s*\d+\s*\.\s*", "", text)
        dependent = bool(_DEPENDS_RE.search(body)) or bool(
            div.find(class_="claim-dependent") or "claim-dependent" in (div.parent.get("class") or [])
        )
        out.append({"num": num.lstrip("0") or num, "text": text, "dependent": dependent})
    return out, ('section[itemprop="claims"] div.claim[num]' if out else None)


def _people(soup: BeautifulSoup, prop: str) -> tuple[list[str], str | None]:
    els = soup.find_all("dd", attrs={"itemprop": prop})
    vals = [e.get_text(" ", strip=True) for e in els if e.get_text(strip=True)]
    return vals, (f'dd[itemprop="{prop}"]' if vals else None)


# --------------------------------------------------------------------------- #
# parse
# --------------------------------------------------------------------------- #

def parse(html: str, number: str, url: str, status: int, raw_path: Path | None) -> PatentDoc:
    soup = BeautifulSoup(html, "lxml")

    if looks_like_missing(html, soup):
        raise FetchError(
            f"{number}: page loaded (HTTP {status}) but carries no patent record "
            "— treat as NOT FOUND, not as an empty document"
        )

    doc = PatentDoc(number=number, url=url, http_status=status, raw_path=str(raw_path) if raw_path else None)

    def put(key, pair):
        value, selector = pair
        doc.fields[key] = Field(value=value, selector=selector)

    def first(*strategies):
        """Try each strategy in order; return the first that yields a value."""
        for value, selector in strategies:
            if value:
                return value, selector
        return None, None

    put("title", first(_meta_name(soup, "DC.title"), _meta_name(soup, "citation_title")))
    put("abstract", first(_section_text(soup, "abstract", "section"), _meta_name(soup, "DC.description")))
    put("description", _section_text(soup, "description", "section"))
    put("claims", _section_text(soup, "claims", "section"))
    put("claim_list", _claim_list(soup))
    put("pdf_link", first(_meta_name(soup, "citation_pdf_url"), _meta_itemprop(soup, "pdfLink")))
    put("images", _images(soup))
    put("classifications", _classifications(soup))
    put("family", _family(soup))
    put("similar_documents", _similar(soup))
    put("backward_citations", _rows(soup, "backwardReferences"))
    put("forward_citations", _rows(soup, "forwardReferences"))
    put("legal_status", _section_text(soup, "status"))
    put("legal_events", _events(soup))
    put("publication_date", first(_meta_name(soup, "citation_publication_date"), _section_text(soup, "publicationDate", "time")))
    put("filing_date", _section_text(soup, "filingDate", "time"))
    put("priority_date", _section_text(soup, "priorityDate", "time"))
    put("assignee", first(_people(soup, "assigneeCurrent"), _people(soup, "assigneeOriginal")))
    put("inventors", _people(soup, "inventor"))

    # Quality gate, not just presence: a 40-character "description" is a stub.
    desc = doc.fields["description"]
    if desc.present and len(str(desc.value)) < MIN_DESCRIPTION_CHARS:
        desc.selector = f"{desc.selector} (SHORT: {len(str(desc.value))} chars)"

    return doc


def get(
    number: str,
    lang: str = "en",
    *,
    raw_dir: Path | None = None,
    client: httpx.Client | None = None,
    polite_delay: float = 1.0,
) -> PatentDoc:
    """Fetch + parse one document. Raises FetchError when there is no record."""
    url, html, status = fetch_html(number, lang, raw_dir=raw_dir, client=client)
    if polite_delay:
        time.sleep(polite_delay)
    raw_path = (raw_dir / f"{number}.html") if raw_dir else None
    return parse(html, number, url, status, raw_path)

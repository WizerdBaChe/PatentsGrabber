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

import html as html_mod
import re
import time
from dataclasses import asdict, dataclass, field
from pathlib import Path

import httpx
from bs4 import BeautifulSoup, NavigableString, Tag

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


def _claim_parts(el, depth: int = 1) -> list[dict]:
    """The limitations of one claim, in order, carrying their nesting depth.

    A US claim is one sentence that can run 300 words; the markup already breaks
    it at every limitation (`div.claim-text` nested inside the preamble's own
    `div.claim-text`). Reading it as a tree is the difference between parsing a
    claim and merely looking at it.
    """
    out: list[dict] = []
    for child in el.find_all("div", class_="claim-text", recursive=False):
        text, rich = _inline(child)
        if text:
            out.append({"depth": depth, "text": text, "rich": rich})
        out.extend(_claim_parts(child, depth + 1))
    return out


def _claim_list(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    """Individual claims with independent/dependent marking and their limitations.

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
        flat = div.get_text(" ", strip=True)
        body = re.sub(r"^\s*\d+\s*\.\s*", "", flat)
        refs = sorted({(r.get("idref") or "").replace("CLM-", "").lstrip("0")
                       for r in div.find_all("claim-ref")} - {""}, key=lambda s: int(s) if s.isdigit() else 0)
        dependent = bool(refs) or bool(_DEPENDS_RE.search(body)) or bool(
            div.find(class_="claim-dependent") or "claim-dependent" in (div.parent.get("class") or [])
        )
        top = div.find("div", class_="claim-text", recursive=False)
        if top is not None:
            lead, lead_rich = _inline(top)
            parts = _claim_parts(top, 1)
        else:                                   # older markup: no claim-text at all
            lead, lead_rich = _inline(div)
            parts = []
        # `text` is what the user copies. Rebuilding it from the structured pieces
        # avoids the artefacts a flat get_text leaves behind ("2 . The tool of
        # claim 1 , wherein"), which come from the markup's own <b>2</b> spans.
        out.append({
            "num": num.lstrip("0") or num,
            "text": "\n".join([lead] + [p["text"] for p in parts]) if parts else (lead or flat),
            "dependent": dependent,
            "lead": lead,
            "lead_rich": lead_rich,
            "parts": parts,
            "refs": refs,
        })
    return out, ('section[itemprop="claims"] div.claim[num]' if out else None)


def _people(soup: BeautifulSoup, prop: str) -> tuple[list[str], str | None]:
    els = soup.find_all("dd", attrs={"itemprop": prop})
    vals = [e.get_text(" ", strip=True) for e in els if e.get_text(strip=True)]
    return vals, (f'dd[itemprop="{prop}"]' if vals else None)


# --------------------------------------------------------------------------- #
# structure — recovering what a flat get_text() throws away
#
# Google Patents publishes the specification as real structure: headings, one
# element per paragraph (with its [0042] number), semantic `figref` for every
# "FIG. 3" mention, `figure-callout` for reference numerals, and claims already
# broken into nested limitations. Rendering that as one flattened run of text is
# the single biggest readability loss in Stage 0, and it is self-inflicted:
# `get_text(" ")` discards a structure the source went to the trouble of marking.
#
# Two markup vintages exist and both must be handled (measured over the 12 pages
# in var/raw/, 2026-08-26):
#   grants / older docs   div.description  > div.description-paragraph   (no numbers)
#   newer publications    ul.description   > li > para-num + div.description-line
# --------------------------------------------------------------------------- #

# Inline tags worth keeping: chemistry and math depend on sub/sup, and losing
# them changes meaning (H2O vs H₂O). Everything else is unwrapped to plain text.
_INLINE_KEEP = {"sub", "sup", "i", "b", "em", "strong"}
_PARA_CLASSES = ("description-paragraph", "description-line")
_FIG_NUM_RE = re.compile(r"(\d+)\s*([A-Za-z])?")


def _is_claim_part(node) -> bool:
    return isinstance(node, Tag) and node.name == "div" and "claim-text" in (node.get("class") or [])


def _fig_number(text: str) -> str | None:
    """'FIG. 3A' -> '3A'. Returns None when no number is present."""
    m = _FIG_NUM_RE.search(text)
    if not m:
        return None
    return m.group(1) + (m.group(2).upper() if m.group(2) else "")


def _wrap(rich: str, open_tag: str, close_tag: str) -> str:
    """Wrap inline content in a tag while leaving its edge whitespace outside it.

    `<figref>FIG. <b>1</b> </figref>` carries the separating space INSIDE the
    element; wrapping it as-is underlines a trailing blank, and stripping it
    outright welds the next word on. Moving the whitespace out keeps both right.
    """
    inner = rich.strip()
    if not inner:
        return rich
    lead = rich[: len(rich) - len(rich.lstrip())]
    trail = rich[len(rich.rstrip()):]
    return f"{lead}{open_tag}{inner}{close_tag}{trail}"


def _inline_raw(node) -> tuple[str, str]:
    """(plain, rich) for one element's inline content, whitespace untouched.

    Whitespace is normalized only by the caller: collapsing at every recursion
    level eats the single space that separates a word from the tag next to it.
    """
    plain: list[str] = []
    rich: list[str] = []
    for child in node.children:
        if isinstance(child, NavigableString):
            text = str(child)
            plain.append(text)
            rich.append(html_mod.escape(text))
            continue
        if not isinstance(child, Tag):
            continue
        if _is_claim_part(child):
            continue  # a nested limitation is its own block, not part of this one
        name = child.name.lower()
        p, r = _inline_raw(child)
        if name == "figref":
            num = _fig_number(p)
            plain.append(p)
            rich.append(_wrap(r, f'<a class="figref" data-fig="{html_mod.escape(num)}">', "</a>")
                        if num else r)
        elif name == "claim-ref":
            ref = (child.get("idref") or "").replace("CLM-", "").lstrip("0")
            plain.append(p)
            rich.append(_wrap(r, f'<a class="claimref" data-claim="{html_mod.escape(ref)}">', "</a>")
                        if ref else r)
        elif name == "figure-callout":
            plain.append(p)
            rich.append(_wrap(r, '<span class="numeral">', "</span>"))
        elif name in _INLINE_KEEP:
            plain.append(p)
            rich.append(_wrap(r, f"<{name}>", f"</{name}>"))
        elif name == "br":
            plain.append(" ")
            rich.append(" ")
        else:
            plain.append(p)
            rich.append(r)
    return "".join(plain), "".join(rich)


def _inline(node) -> tuple[str, str]:
    """Normalized (plain, rich) for one block element."""
    p, r = _inline_raw(node)
    return re.sub(r"\s+", " ", p).strip(), re.sub(r"\s+", " ", r).strip()


def _figs_in(node) -> list[str]:
    out = []
    for fr in node.find_all("figref"):
        num = _fig_number(fr.get_text(" ", strip=True))
        if num and num not in out:
            out.append(num)
    return out


def _para_number(el) -> str | None:
    """The [0042] paragraph number, from the div itself or its para-num sibling."""
    num = (el.get("num") or "").strip()
    if not num:
        sib = el.find_previous_sibling("para-num")
        num = ((sib.get("num") if sib else "") or "").strip() if sib else ""
    num = num.strip("[]").strip()
    return num.lstrip("0") or num if num else None


def _block_kind(el) -> str | None:
    """Which kind of block this element carries, if any."""
    if not isinstance(el, Tag):
        return None
    name = el.name.lower()
    if name == "heading":
        return "heading"
    if name == "div" and any(c in (el.get("class") or []) for c in _PARA_CLASSES):
        return "para"
    if name in ("pre", "table"):
        return "pre"
    if name == "li":
        # Enumerated limitations and feature lists appear as bare <li> in some
        # documents and as <li><para-num/><div class="description-line"/></li> in
        # others; only the former is a block in its own right.
        return "li"
    return None


def _ancestor_kinds(el, root) -> set[str]:
    kinds, node = set(), el.parent
    while node is not None and node is not root:
        kind = _block_kind(node)
        if kind:
            kinds.add(kind)
        node = node.parent
    return kinds


def _description_blocks(soup: BeautifulSoup) -> tuple[list[dict], str | None]:
    """The specification as ordered blocks: headings, numbered paragraphs, lists, tables."""
    section = soup.find("section", attrs={"itemprop": "description"})
    if not section:
        return [], None
    root = section.find(class_="description") or section

    blocks: list[dict] = []
    for el in root.find_all(["heading", "div", "pre", "table", "li"]):
        kind = _block_kind(el)
        if not kind:
            continue
        ancestors = _ancestor_kinds(el, root)
        # Content already carried by an enclosing block must not be emitted twice.
        if kind == "para" and "para" in ancestors:
            continue
        if kind in ("pre", "heading") and ({"para", "li"} & ancestors):
            continue
        if kind == "li":
            if "para" in ancestors:
                continue          # the enclosing paragraph already carries this text
            if el.find("li") or el.find("div", class_=list(_PARA_CLASSES)):
                continue          # a wrapper around other blocks, not a list item itself

        if kind == "pre":
            # Preformatted data and tables carry their meaning in their layout;
            # collapsing them into prose destroys it. Kept verbatim, rendered mono.
            text = el.get_text("\n", strip=True) if el.name == "table" else el.get_text()
            if text.strip():
                blocks.append({"type": "pre", "text": text.rstrip()})
            continue

        text, rich = _inline(el)
        if not text:
            continue
        if kind == "heading":
            blocks.append({"type": "heading", "text": text, "rich": rich, "id": el.get("id")})
        elif kind == "para":
            blocks.append({"type": "para", "num": _para_number(el), "text": text,
                           "rich": rich, "figs": _figs_in(el)})
        else:
            depth = 0
            node = el.parent
            while node is not None and node is not root:
                if isinstance(node, Tag) and node.name in ("ul", "ol"):
                    depth += 1
                node = node.parent
            blocks.append({"type": "li", "level": max(1, depth), "text": text,
                           "rich": rich, "figs": _figs_in(el)})

    if not blocks:
        return [], None
    return blocks, 'section[itemprop="description"] heading|.description-paragraph|.description-line|li'


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
    put("description_blocks", _description_blocks(soup))
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

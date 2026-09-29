"""HTML -> readable text, shared by `web.open` / `web.extract` and email bodies.

Standard library only (`html.parser`): tolerant of broken markup, no extra dependency. It is a
readability approximation, not a browser: scripts, styles and embedded objects are dropped, block
elements become lines, links and headings are collected. Output is untrusted data.
"""

from __future__ import annotations

from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import urljoin, urlsplit

_SKIP = frozenset({"script", "style", "noscript", "svg", "template", "iframe", "object", "canvas"})
_BLOCK = frozenset(
    {
        "address", "article", "aside", "blockquote", "br", "dd", "div", "dl", "dt", "fieldset",
        "figcaption", "figure", "footer", "form", "h1", "h2", "h3", "h4", "h5", "h6", "header",
        "hr", "li", "main", "nav", "ol", "p", "pre", "section", "table", "tbody", "td", "tfoot",
        "th", "thead", "title", "tr", "ul",
    }
)  # fmt: skip
_HEADINGS = {"h1": 1, "h2": 2, "h3": 3}


@dataclass(frozen=True, slots=True)
class HtmlPage:
    title: str
    description: str
    lines: tuple[str, ...]
    headings: tuple[tuple[int, str], ...]
    links: tuple[tuple[str, str], ...]  # (text, absolute url)

    @property
    def text(self) -> str:
        return "\n".join(self.lines)


class _Extractor(HTMLParser):
    def __init__(self, base_url: str) -> None:
        super().__init__(convert_charrefs=True)
        self._base = base_url
        self._skip_depth = 0
        self._line: list[str] = []
        self.lines: list[str] = []
        self.title_parts: list[str] = []
        self.description = ""
        self.headings: list[tuple[int, str]] = []
        self.links: list[tuple[str, str]] = []
        self._in_title = False
        self._heading: tuple[int, list[str]] | None = None
        self._link: tuple[str, list[str]] | None = None

    # -- helpers ----------------------------------------------------------------------------
    def _flush(self) -> None:
        text = " ".join("".join(self._line).split())
        if text:
            self.lines.append(text)
        self._line = []

    # -- HTMLParser -------------------------------------------------------------------------
    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        attributes = dict(attrs)
        if tag == "meta":
            name = (attributes.get("name") or attributes.get("property") or "").lower()
            if name in ("description", "og:description") and not self.description:
                self.description = " ".join((attributes.get("content") or "").split())
            return
        if tag in _BLOCK:
            self._flush()
        if tag == "title":
            self._in_title = True
        elif tag == "li":
            self._line.append("- ")
        elif tag in _HEADINGS:
            self._heading = (_HEADINGS[tag], [])
        elif tag == "a":
            href = (attributes.get("href") or "").strip()
            target = urljoin(self._base, href) if href else ""
            if urlsplit(target).scheme in ("http", "https"):
                self._link = (target, [])

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip_depth = max(0, self._skip_depth - 1)
            return
        if self._skip_depth:
            return
        if tag in _BLOCK:
            self._flush()
        if tag == "title":
            self._in_title = False
        elif tag in _HEADINGS and self._heading is not None:
            level, parts = self._heading
            text = " ".join("".join(parts).split())
            if text:
                self.headings.append((level, text))
            self._heading = None
        elif tag == "a" and self._link is not None:
            target, parts = self._link
            text = " ".join("".join(parts).split())
            if text:
                self.links.append((text, target))
            self._link = None

    def handle_data(self, data: str) -> None:
        if self._skip_depth:
            return
        if self._in_title:
            self.title_parts.append(data)
            return
        self._line.append(data)
        if self._heading is not None:
            self._heading[1].append(data)
        if self._link is not None:
            self._link[1].append(data)


def parse_html(html: str, base_url: str = "") -> HtmlPage:
    extractor = _Extractor(base_url)
    extractor.feed(html)
    extractor.close()
    extractor._flush()
    return HtmlPage(
        title=" ".join("".join(extractor.title_parts).split()),
        description=extractor.description,
        lines=tuple(extractor.lines),
        headings=tuple(extractor.headings),
        links=tuple(extractor.links),
    )


def html_to_text(html: str) -> str:
    return parse_html(html).text

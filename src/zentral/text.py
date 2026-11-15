"""Text normalisation helpers. Zentral stores and renders plain text only."""

from __future__ import annotations

import re
import unicodedata
from html.parser import HTMLParser

_BLOCK_TAGS = {"p", "div", "br", "li", "tr", "h1", "h2", "h3", "h4", "h5", "h6", "blockquote"}
_SKIP_TAGS = {"script", "style", "head", "title", "template"}


class _TextExtractor(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._skip = 0

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag in _SKIP_TAGS:
            self._skip += 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP_TAGS and self._skip:
            self._skip -= 1
        elif tag in _BLOCK_TAGS:
            self.parts.append("\n")

    def handle_data(self, data: str) -> None:
        if not self._skip:
            self.parts.append(data)


def html_to_text(html: str) -> str:
    """Reduce HTML to plain text. Markup, scripts and styles are discarded."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return clean_text("".join(parser.parts))


def clean_text(text: str, max_chars: int | None = None) -> str:
    """Normalise untrusted text: strip control characters and collapse blank runs."""
    text = unicodedata.normalize("NFC", text).replace("\r\n", "\n").replace("\r", "\n")
    text = "".join(
        ch for ch in text if ch in "\n\t" or unicodedata.category(ch) not in ("Cc", "Cf", "Cs", "Co")
    )
    text = re.sub(r"[ \t]+\n", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text).strip()
    if max_chars is not None and len(text) > max_chars:
        text = text[:max_chars].rstrip() + "\n[… truncated]"
    return text


def single_line(text: str, max_chars: int = 200) -> str:
    line = re.sub(r"\s+", " ", clean_text(text)).strip()
    return line if len(line) <= max_chars else line[: max_chars - 1].rstrip() + "…"

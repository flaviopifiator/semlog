"""Shared Markdown helpers for the README checks: both language editions of
the README (`README.md` in English, `README.es.md` in Spanish), with the
section titles each check needs in each language, plus stdlib-only
parsing of headings, fenced code blocks, sections and badges. Parsing is
fence-aware: a `# comment` line inside a code block is never a heading.
Not itself a `test*.py` file, so the traceability checker skips it.
"""

from __future__ import annotations

import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
BENCHMARKS_PATH = REPO_ROOT / "BENCHMARKS.md"

# One entry per README edition: its path, the language switch it must open
# with, and the localized titles of the sections other checks look into.
READMES = {
    "en": {
        "path": REPO_ROOT / "README.md",
        "switch": "**English** | [Español](README.es.md)",
        "configuration": "Configuration",
        "documentation": "Documentation",
        "performance": "Performance",
        "modes": "Modes",
        "configuration_sources": "Configuration sources",
        "limitations": "Limitations",
    },
    "es": {
        "path": REPO_ROOT / "README.es.md",
        "switch": "[English](README.md) | **Español**",
        "configuration": "Configuración",
        "documentation": "Documentación",
        "performance": "Rendimiento",
        "modes": "Modos",
        "configuration_sources": "Fuentes de configuración",
        "limitations": "Limitaciones",
    },
}

_FENCE_RE = re.compile(r"^```")
_HEADING_RE = re.compile(r"^(#{1,6}) (.+)$")
_BADGE_RE = re.compile(r"\[!\[([^\]]*)\]\(([^)\s]+)\)\]\(([^)\s]+)\)")
_HTML_COMMENT_RE = re.compile(r"<!--.*?-->", re.DOTALL)


def readme_text(language):
    return READMES[language]["path"].read_text(encoding="utf-8")


def _body_lines(text):
    """Yield `(index, line)` for lines outside fenced code blocks."""
    in_fence = False
    for index, line in enumerate(text.splitlines()):
        if _FENCE_RE.match(line):
            in_fence = not in_fence
            continue
        if not in_fence:
            yield index, line


def headings(text):
    """`[(level, title), ...]` for every ATX heading outside code blocks."""
    result = []
    for _index, line in _body_lines(text):
        match = _HEADING_RE.match(line)
        if match:
            result.append((len(match.group(1)), match.group(2).strip()))
    return result


def fenced_blocks(text):
    """`[(info_string, content), ...]` for every fenced code block, in order."""
    blocks = []
    info = None
    buffer = []
    for line in text.splitlines():
        if _FENCE_RE.match(line):
            if info is None:
                info = line[3:].strip()
                buffer = []
            else:
                blocks.append((info, "\n".join(buffer)))
                info = None
            continue
        if info is not None:
            buffer.append(line)
    return blocks


def section(text, level, title):
    """The text of the section whose heading is `title` at `level`, up to
    the next heading at the same or a shallower level (code-block aware).
    Raises `ValueError` when the heading is absent."""
    lines = text.splitlines()
    start = end = None
    for index, line in _body_lines(text):
        match = _HEADING_RE.match(line)
        if not match:
            continue
        heading_level, heading_title = len(match.group(1)), match.group(2).strip()
        if start is None:
            if heading_level == level and heading_title == title:
                start = index
        elif heading_level <= level:
            end = index
            break
    if start is None:
        raise ValueError(f"no level-{level} heading {title!r}")
    return "\n".join(lines[start:end])


def badges(text):
    """`[(alt_text, image_url, link_target), ...]` for every linked badge
    image outside HTML comments, in order."""
    return _BADGE_RE.findall(_HTML_COMMENT_RE.sub("", text))


def html_comments(text):
    return _HTML_COMMENT_RE.findall(text)


def commented_badges(text):
    """`[(alt_text, image_url, link_target), ...]` for every linked badge
    image inside an HTML comment (a badge not yet enabled)."""
    return [
        badge for comment in html_comments(text) for badge in _BADGE_RE.findall(comment)
    ]

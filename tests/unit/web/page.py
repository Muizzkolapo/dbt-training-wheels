"""Reading a rendered walk screen back as text, so a test can tell what the
web layer *authored* from what it only *rendered*.

Spec section 6.1 says every explanatory string on screen comes from the
engine. The only way to hold a template to that mechanically is to separate
the two, and the separation has to be one a template cannot fake:

* text inside an element carrying `data-engine` is *claimed* to be an engine
  string, and `test_no_screen_authors_a_sentence` checks each such claim by
  **exact match** against the strings this conversion actually produced. A
  template that wrapped its own sentence in the marker fails that check, so
  the marker buys nothing;
* every other text run is the template's own, and is what "authors no
  explanatory text" is asserted against.

Why elements rather than substring subtraction. The obvious stripper --
remove each engine string from the page text and look at what is left -- is
the way this test goes vacuous: engine prose on these screens is long and
ordinary, so subtracting it also subtracts an authored sentence built from
the same words, and the remainder is empty whatever the page says. Removing a
marked *element* removes exactly what that element holds, so a sentence the
templates wrote survives the strip and can be measured.

Runs are accumulated per block element, not per text node. `<p>Every run
<em>adds</em> what it finds.</p>` is one run of six words, not three runs of
two -- otherwise a sentence with any inline markup in it measures as several
short ones and no threshold ever fires.

`placeholder`, `title`, `alt` and `aria-label` are read as authored runs
alongside the text. They are prose the reader sees, and a template that put a
sentence in one of them would otherwise never be looked at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from html.parser import HTMLParser

# Tags whose text is its own run. A sentence lives in one of these; inline
# tags (em, code, span, b, small, a ...) deliberately are not here, so markup
# inside a sentence does not break it into pieces too short to measure. That
# is also why a list of links is written `<li><a>...</a></li>`: the `li` ends
# the run, the `a` does not, and no sentence can hide behind a link.
_BLOCK = frozenset(
    {
        "p", "li", "h1", "h2", "h3", "h4", "h5", "h6", "td", "th", "caption",
        "div", "section", "article", "nav", "main", "header", "footer", "aside",
        "form", "fieldset", "legend", "label", "button", "pre", "blockquote",
        "summary", "details", "option", "figcaption", "dt", "dd", "table",
        "thead", "tbody", "tr", "ul", "ol", "dl",
    }
)  # fmt: skip

# Elements that close themselves, so nothing waits for an end tag.
_VOID = frozenset(
    {"area", "base", "br", "col", "embed", "hr", "img", "input", "link", "meta", "source", "wbr"}
)

# Nothing inside these is prose a reader meets.
_SKIP = frozenset({"head", "script", "style", "title"})

# Attributes whose value is prose the reader sees.
_PROSE_ATTRS = ("placeholder", "title", "alt", "aria-label")

_HEADINGS = ("h1", "h2", "h3", "h4", "h5", "h6")

# A word, for the purpose of "is this a sentence". Letters only: a rendered
# count is digits, and a count is never prose -- it is the one number spec
# section 11.4(c) requires every screen to derive rather than author.
_WORD = re.compile(r"[A-Za-z][A-Za-z'’‐-]*")


def words(text: str) -> list[str]:
    """The words in `text`, by the definition above."""
    return _WORD.findall(text)


def normalised(text: str) -> str:
    """`text` with every run of whitespace collapsed to one space.

    Both sides of the engine-string comparison go through this: a template
    indents what it renders, and a `<pre>` keeps newlines the engine wrote
    that a one-line comparison would trip over.
    """
    return " ".join(text.split())


@dataclass(frozen=True, slots=True)
class Run:
    """One block's worth of text, and where on the page it stood.

    `aside` is set for a run inside an element carrying `data-aside` -- the
    glossary block, which is the one region of a screen whose content is
    derived from the rest of it. A check asking "which dbt words does this
    screen use" has to read the screen without it, or the block defining
    fourteen words is a screen that uses fourteen words and the check passes
    for any block at all.
    """

    tag: str
    text: str
    engine: bool
    aside: bool = False


@dataclass(frozen=True, slots=True)
class Page:
    """One rendered screen, read back.

    `authored` is every run the templates wrote; `engine` is every run they
    marked as the engine's. `runs` is both, in the order their elements
    closed, which is the order the heading check walks.
    """

    runs: tuple[Run, ...]
    counts: tuple[tuple[str, str], ...]  # (the data-count name, the text shown)
    items: tuple[str, ...]  # one entry per data-item element
    inputs: tuple[dict[str, str], ...]  # every <input>'s attributes
    forms: tuple[dict[str, str], ...]  # every <form>'s attributes
    links: tuple[str, ...]  # every <a>'s href
    text: str  # every text node, engine and authored alike

    def outside_asides(self) -> str:
        """Everything on the page except what a `data-aside` region holds."""
        return " ".join(run.text for run in self.runs if not run.aside)

    @property
    def authored(self) -> tuple[str, ...]:
        return tuple(run.text for run in self.runs if not run.engine and run.text.strip())

    @property
    def engine(self) -> tuple[str, ...]:
        return tuple(run.text for run in self.runs if run.engine)

    def sentences(self, minimum: int) -> tuple[str, ...]:
        """Every authored run holding `minimum` words or more."""
        return tuple(run for run in self.authored if len(words(run)) >= minimum)

    def empty_headings(self) -> tuple[str, ...]:
        """Every heading whose section holds nothing.

        A heading is empty when the next run that is not blank is a heading of
        the same or a higher level, or when nothing follows it at all. A
        heading followed by a deeper one opens a section with a subsection,
        which is ordinary.
        """
        empty: list[str] = []
        filled = [run for run in self.runs if run.text.strip()]
        for position, run in enumerate(filled):
            if run.tag not in _HEADINGS:
                continue
            following = filled[position + 1 :]
            if not following:
                empty.append(run.text)
                continue
            after = following[0]
            if after.tag in _HEADINGS and after.tag <= run.tag:
                empty.append(run.text)
        return tuple(empty)


@dataclass
class _Frame:
    tag: str
    engine: bool
    aside: bool
    buffer: list[str] = field(default_factory=list)


class _Reader(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.runs: list[Run] = []
        self.counts: list[tuple[str, str]] = []
        self.items: list[str] = []
        self.inputs: list[dict[str, str]] = []
        self.forms: list[dict[str, str]] = []
        self.links: list[str] = []
        self.text: list[str] = []
        self.frames: list[_Frame] = [_Frame(tag="", engine=False, aside=False)]
        self._skip = 0
        # (name, depth) per open element carrying data-count, so the number it
        # shows is read off that element's own text rather than guessed at.
        self._counting: list[tuple[str, int]] = []

    def _engine_depth(self) -> int:
        return sum(1 for frame in self.frames if frame.engine)

    def _aside(self) -> bool:
        return any(frame.aside for frame in self.frames)

    def _add(self, text: str) -> None:
        self.frames[-1].buffer.append(text)

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        attributes = {name: (value or "") for name, value in attrs}
        if tag == "input":
            self.inputs.append(attributes)
        if tag == "form":
            self.forms.append(attributes)
        if tag == "a" and "href" in attributes:
            self.links.append(attributes["href"])
        if "data-item" in attributes:
            self.items.append(attributes["data-item"])
        for attribute in _PROSE_ATTRS:
            if attributes.get(attribute, "").strip():
                self.runs.append(Run(tag=f"@{attribute}", text=attributes[attribute], engine=False))
        if tag in _SKIP:
            self._skip += 1
            return
        if tag in _VOID:
            return
        self.frames.append(
            _Frame(
                tag=tag,
                engine="data-engine" in attributes,
                aside=self._aside() or "data-aside" in attributes,
            )
        )
        if "data-count" in attributes:
            self._counting.append((attributes["data-count"], len(self.frames)))

    def handle_startendtag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.handle_starttag(tag, attrs)
        if tag not in _VOID and tag not in _SKIP:
            self.handle_endtag(tag)

    def handle_endtag(self, tag: str) -> None:
        if tag in _SKIP:
            self._skip = max(0, self._skip - 1)
            return
        if tag in _VOID:
            return
        if len(self.frames) == 1:
            raise AssertionError(f"</{tag}> closes an element that was never opened")
        frame = self.frames.pop()
        if frame.tag != tag:
            raise AssertionError(f"</{tag}> closes <{frame.tag}>; the page is not well formed")
        text = "".join(frame.buffer)
        while self._counting and self._counting[-1][1] > len(self.frames):
            name, _ = self._counting.pop()
            self.counts.append((name, text.strip()))
        if self._engine_depth():
            # Inside an engine element: everything belongs to the one run that
            # element records when it closes.
            self._add(text)
            return
        if frame.engine or frame.tag in _BLOCK:
            self.runs.append(Run(tag=frame.tag, text=text, engine=frame.engine, aside=frame.aside))
            return
        self._add(text)

    def handle_data(self, data: str) -> None:
        if self._skip:
            return
        self.text.append(data)
        self._add(data)


def read(html: str) -> Page:
    """One rendered screen, parsed."""
    reader = _Reader()
    reader.feed(html)
    reader.close()
    if len(reader.frames) != 1:
        unclosed = ", ".join(frame.tag for frame in reader.frames[1:])
        raise AssertionError(f"the page leaves <{unclosed}> open")
    return Page(
        runs=tuple(reader.runs),
        counts=tuple(reader.counts),
        items=tuple(reader.items),
        inputs=tuple(reader.inputs),
        forms=tuple(reader.forms),
        links=tuple(reader.links),
        text="".join(reader.text),
    )

"""What this tool does, in four lines, owned by the engine.

The entry screen is the one screen with no conversion behind it, and until
now that meant it could say nothing at all: every other screen explains a
Decision, and spec section 6.1 holds a template to rendering the engine's
sentences rather than writing its own. A screen that greets a reader with
nothing but a file input explains nothing, and the design this walk is built
from opens with four of these.

They live here rather than in a template for exactly the reason a
`Decision.reason` does: `test_no_screen_authors_a_sentence` checks every
marked string on every screen by exact match against what `dbtw.core`
produced, so a sentence written in `entry.html` is either unmarked -- and
fails the prose threshold -- or marked and fails the match. Owning them here
is what makes them sayable, and it puts the claim the tool makes about itself
in the same place as every other claim it makes.

Each line is a promise this code keeps. `Ships` is the one to watch: the
design it comes from says "one branch, one PR per domain", and `deliver`
makes a branch and deliberately does not open a pull request -- it needs a
remote, credentials and a forge, each a decision about someone's account
rather than their files. So the line says what the branch is and stops where
the code stops. A pillar that promised the pull request would be this module
advertising a feature the reader would then go looking for.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Pillar:
    """One thing this tool does, numbered as the reader meets it."""

    number: str
    name: str
    plain: str


# The headline and the line under it. Engine-owned for the same reason the
# pillars are: they are sentences on a screen, and a screen may not write
# one. The design's own lede ends "and it opens the pull requests under your
# name" -- `deliver` makes a branch and stops there, so this one stops there
# too. A headline is the last place to promise something the code does not do.
HEADLINE = "Turn the SQL you already run into dbt."

LEDE = (
    "Bring in a query. It proposes the layers, you supply the judgement, "
    "and it puts the result on a branch of your own repository."
)

PILLARS: tuple[Pillar, ...] = (
    Pillar(
        number="01",
        name="Reads",
        plain="Separates the tables your SQL reads from the tables it builds.",
    ),
    Pillar(
        number="02",
        name="Layers",
        plain="Places every table your script writes into the layer your project already uses.",
    ),
    Pillar(
        number="03",
        name="Asks",
        plain="You answer what only you can answer, and describe what each model is for.",
    ),
    Pillar(
        number="04",
        name="Ships",
        plain="One branch in your own repository, committed under your own name.",
    ),
)

# The line under the four, about where a reader's SQL goes. It is the answer
# to the question three of five personas asked before pasting anything, and
# it is true of this tool in a way it is not true of a hosted one: the
# conversion runs in this process, on this machine, and `emit` refuses to
# write inside the project at all until the write action is pressed.
PRIVACY = (
    "Your SQL is read on this machine and goes nowhere else. "
    "Nothing is written until you press the button that writes it."
)

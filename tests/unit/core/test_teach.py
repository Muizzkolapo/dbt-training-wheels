"""The glossary: the dbt words this tool's own output uses, defined once.

Measured, both persona rounds. "compile / run / test / build never related to
each other" was 5/5 in round 2 and the top change request; `materialized`
undefined 4/5 then 3/3; `staging` read as a deploy environment; `{{ }}`
unexplained 5/5 in round 1; 3/5 could not tell whether a failing check holds a
write back or reports it afterwards.

Several tests here are written against the *checking* logic as well as the
data, because a loop over a list is only a test while the list has entries in
it: `test_the_jargon_check_catches_a_definition_that_leans_on_a_banned_word`
and its sibling below exist so that the jargon sweep is known to be able to
fail, rather than assumed to.
"""

import re

import pytest
from tests.unit.passes.test_plain_register import JARGON

from dbtw.core.teach import GLOSSARY, Term, terms_in


def _by_name() -> dict[str, Term]:
    return {term.name: term for term in GLOSSARY}


def _names(text: str) -> list[str]:
    return [term.name for term in terms_in(text)]


def _mentions(text: str, word: str) -> bool:
    """`word` as a whole word, so a definition that only contains "rebuild"
    does not read as one that relates itself to `build`."""
    return re.search(rf"\b{re.escape(word)}\b", text) is not None


# --- the four commands, told apart from each other


def test_the_four_commands_are_defined_and_related_to_each_other():
    """5/5 personas asked for this and it is the top unmet request: each of
    the four is defined, and each says how it relates to the others, because
    knowing what `run` does without knowing how it differs from `build` was
    the actual failure.

    Each assertion names a word that does not occur in the checked term's own
    name, so none of them can pass on the term's self-reference alone.
    """
    by_name = _by_name()
    for command in ("dbt compile", "dbt run", "dbt test", "dbt build"):
        assert command in by_name, command

    assert _mentions(by_name["dbt compile"].plain, "run")
    assert _mentions(by_name["dbt run"].plain, "compile")
    assert _mentions(by_name["dbt run"].plain, "build")
    assert _mentions(by_name["dbt test"].plain, "build")
    assert _mentions(by_name["dbt build"].plain, "run")
    assert _mentions(by_name["dbt build"].plain, "test")


# The ordering claim, pinned by what the definition must *say* rather than by
# what it may not. A blacklist cannot carry this fact: a blind review rewrote
# `dbt test` to "it reads tables that have not been written yet: it tells you
# a table would hold bad data after the check runs" -- the exact reversal 3/5
# personas got wrong -- and every test passed, because the word "after" was
# still in the string and no banned word had been used.
#
# `have|has|... already ... written` and not a bare search for "already": the
# verb has to sit directly against it, so "have *not* already been written"
# does not match either.
_ALREADY_WRITTEN = re.compile(
    r"\b(?:have|has|had|is|are|was|were)\s+already\s+(?:been\s+)?written\b", re.IGNORECASE
)
_CANNOT_UNDO = re.compile(
    r"\b(?:cannot|can ?not|can't|could not|does not|do not|will not|never)\s+"
    r"(?:\w+\s+){0,2}undo\b",
    re.IGNORECASE,
)


def test_a_test_is_described_as_reporting_not_blocking():
    """3/5 could not tell whether a failing check stops the write. It does
    not: it evaluates when separately invoked, after the write.

    This is the one sentence on the branch carrying that fact, so it is
    pinned by what it states, not only by what it avoids. What the positive
    assertions buy: the mechanism cannot be removed or reversed in place
    while incidental words survive. What they do not buy, and no regex over
    prose can: a definition that states the mechanism correctly and then
    contradicts it in a further sentence. `_HOLDS_BACK` below is the cheap
    half of that, and the `before` check is one phrasing of it, not the
    class.
    """
    (test_term,) = [term for term in GLOSSARY if term.name == "dbt test"]
    plain = test_term.plain
    assert _ALREADY_WRITTEN.search(plain), plain
    assert _CANNOT_UNDO.search(plain), plain
    assert _mentions(plain, "after")
    # Nothing in a true description of this command needs to put the check
    # ahead of the write, so the word is refused here rather than reasoned
    # about. One phrasing closed, not the class -- see the docstring.
    assert "before" not in plain.lower(), plain


def test_the_ordering_check_rejects_the_reversal_it_exists_to_catch():
    """The reversal a blind review found surviving, verbatim, beside the
    affirmative form it replaced. A check matching both would be no check,
    and this is where that is proven rather than assumed."""
    reversed_claim = (
        "it reads tables that have not been written yet: it tells you a table "
        "would hold bad data after the check runs, and it cannot undo the write."
    )
    truthful = (
        "it reads tables that have already been written: it tells you a table "
        "contains bad data after that data is in it, and it cannot undo the write."
    )
    assert _ALREADY_WRITTEN.search(truthful)
    assert not _ALREADY_WRITTEN.search(reversed_claim)
    # The negation cannot be smuggled in beside the word the check looks for.
    assert not _ALREADY_WRITTEN.search("tables that have not already been written")
    assert _CANNOT_UNDO.search(truthful)
    assert not _CANNOT_UNDO.search("and a failing check can undo the write")


# Stems, not the four plural forms the brief listed: "it does not stop the
# write" would pass a check for "stops" while saying the thing the check
# exists to forbid. Swept over the whole glossary rather than `dbt test`
# alone, because the constraint is that no wording *anywhere* may imply a
# check holds a write back.
#
# `hold` and `keep` are here because a blind review got both past the first
# list -- "a failure holds the write back", "it keeps the bad rows out" --
# and `hold` is the stem this sweep's own test is named after. They are
# deliberately over-broad: they also refuse "a table holds bad data" and
# "dbt keeps no data of its own", which are true sentences. That is the
# trade taken on purpose. An over-broad stem costs a rewording, caught at
# the moment the definition is written; a missing one costs a wrong fact
# shipped to the reader who cannot check it. Five definitions were reworded
# to take it.
_HOLDS_BACK = re.compile(
    r"\b(stop|prevent|block|reject|refuse|halt|abort|hold|keep|forbid|deny|veto|guard)\w*",
    re.IGNORECASE,
)


def test_no_definition_says_a_check_holds_a_write_back():
    # A loop rather than a parametrize: an empty glossary would make a
    # parametrized sweep *skip*, which is one line in a summary of hundreds.
    assert GLOSSARY, "an empty glossary passes this loop without checking anything"
    for term in GLOSSARY:
        found = _HOLDS_BACK.findall(term.plain)
        assert not found, f"{term.name} says {found}"


def test_the_holds_back_check_is_able_to_fail():
    """The sweep above is a regex over prose: if it matched nothing it would
    pass for every glossary, including one that said the wrong thing."""
    assert _HOLDS_BACK.findall("a failing check prevents the write")
    assert _HOLDS_BACK.findall("it will stop the model being written")
    # The two paraphrases a blind review found surviving the first stem list.
    assert _HOLDS_BACK.findall("a failure holds the write back until you fix it")
    assert _HOLDS_BACK.findall("it keeps the bad rows out")
    # The brief's four exact words, proven subsumed here rather than
    # re-asserted against the glossary, where the sweep already covers them
    # and a second loop could never fail on its own.
    for forbidden in ("stops", "prevents", "blocks", "rejects"):
        assert _HOLDS_BACK.findall(f"a failing check {forbidden} the write"), forbidden
    assert not _HOLDS_BACK.findall("it reports what it found afterwards")


# --- the words the walkthroughs recorded as unreadable


@pytest.mark.parametrize("name", ["materialized", "staging", "{{ }}", "incremental"])
def test_the_words_the_personas_could_not_read_are_defined(name: str):
    assert name in {term.name for term in GLOSSARY}


# The denial has to attach an indefinite article to the thing being denied:
# "not *a* deployment environment" is a denial of the category, while "not
# *the* production environment" is a denial of one environment and leaves
# staging as another -- which is the misreading. A blind review got the looser
# form ("The staging environment, not the production environment.") past the
# first version of this.
#
# The residual limit, which no regex closes: "not a production environment,
# but the staging one" would still pass. What is pinned is that the sentence
# denies the category, not that nothing elsewhere in the definition re-asserts
# it.
_DENIES_THE_CATEGORY = re.compile(r"\b(?:not|never)\s+an?\s+[^.]{0,30}\benvironment\b")


def test_staging_is_not_defined_as_an_environment():
    """A backend engineer read it as staging-vs-production and finished the
    walk still wrong about it. Asserting the word `environment` appears is
    not enough on its own -- "the staging environment" contains it too -- so
    the denial has to be the thing that is pinned."""
    (staging,) = [term for term in GLOSSARY if term.name == "staging"]
    assert _mentions(staging.plain, "layer")
    assert _DENIES_THE_CATEGORY.search(staging.plain), staging.plain


def test_the_environment_denial_check_is_able_to_fail():
    assert _DENIES_THE_CATEGORY.search("A layer of models, not a deployment environment.")
    # Verbatim, the looser form a blind review got past the first version.
    assert not _DENIES_THE_CATEGORY.search(
        "The staging environment, not the production environment."
    )
    assert not _DENIES_THE_CATEGORY.search("The environment models are deployed into.")


def test_every_jargon_word_is_defined_by_exactly_one_term():
    """The two lists are one decision seen from both sides: JARGON is the
    words the plain register may not use, and the glossary is where those
    same words get said properly. A word banned from one register and
    defined in neither is a word the reader has no way to look up.

    `terms_in` rather than a name comparison, because that is how a surface
    will actually find the definition for a word it printed -- and it also
    catches two terms claiming one spelling, which would render the same
    word twice.
    """
    # Content, not just emptiness: a loop over an emptied JARGON would pass
    # having checked nothing, and `assert JARGON` is a statement pyright can
    # already see is always true. This fails either way, and it also fails if
    # the evidence list is quietly narrowed.
    assert "materialized" in JARGON, "the evidence list no longer holds the words this covers"
    for word in JARGON:
        found = terms_in(word)
        assert len(found) == 1, f"{word!r} resolves to {[t.name for t in found]}"


def test_every_term_carries_a_definition_that_is_not_just_its_own_name():
    assert GLOSSARY
    for term in GLOSSARY:
        assert term.plain.strip(), f"{term.name} has no definition"
        assert term.plain.strip() != term.name, f"{term.name} defines itself as itself"


# A definition is rendered on its own, in a list whose membership is different
# on every surface: the report defines eight of these and names none of the
# four commands, and a screen may define one. So no definition may point at a
# neighbouring entry or at a place on the page -- the entry it points at is
# very often not there. Caught in review on the real report, where Jinja read
# "the templating language *those* double curly brackets belong to" beside a
# glossary that did not list the brackets, because that report contains none.
_POINTS_AT_A_NEIGHBOUR = re.compile(
    r"\b(above|below|those|the previous|the next|listed here|shown here)\b", re.IGNORECASE
)


def test_no_definition_points_at_another_entry_or_at_the_page():
    assert GLOSSARY, "an empty glossary passes this loop without checking anything"
    for term in GLOSSARY:
        found = _POINTS_AT_A_NEIGHBOUR.findall(term.plain)
        assert not found, f"{term.name} points at {found}, which its surface may not be showing"


def test_the_pointing_check_is_able_to_fail():
    assert _POINTS_AT_A_NEIGHBOUR.findall("the language those double brackets belong to")
    assert _POINTS_AT_A_NEIGHBOUR.findall("as defined above")
    assert not _POINTS_AT_A_NEIGHBOUR.findall("the double curly brackets in a model file")


def test_no_two_terms_share_a_name():
    names = [term.name for term in GLOSSARY]
    assert len(set(names)) == len(names), names


# --- the jargon sweep, and proof that the sweep can fail


def _jargon_in(term: Term) -> list[str]:
    """The banned words `term`'s definition leans on.

    Lowercased on both sides, matching `test_plain_register._jargon_in`: a
    definition opening with "Materialized ..." leans on the word just as much
    as one that spells it in lower case, and the self-reference a term is
    allowed is the same self-reference either way.
    """
    plain = term.plain.lower()
    return [word for word in JARGON if word != term.name.lower() and word in plain]


def test_every_definition_avoids_the_jargon_list():
    """The plain register is measured against the same word list the
    Decision plain register is. A definition that needs a second definition
    has not defined anything."""
    assert GLOSSARY, "an empty glossary passes this loop without checking anything"
    for term in GLOSSARY:
        found = _jargon_in(term)
        assert not found, f"{term.name} leans on {found}"


def test_the_jargon_check_catches_a_definition_that_leans_on_a_banned_word():
    """The sweep above only ever runs over definitions written to pass it, so
    it has never been seen to fail. This is the case where it must."""
    planted = Term(name="a word", plain="the rows are stored in the warehouse as a table")
    assert _jargon_in(planted) == ["warehouse"]


def test_the_jargon_check_exempts_a_term_naming_itself_and_nothing_else():
    """Defining "warehouse" necessarily says "warehouse". Defining it does
    not license saying "staging"."""
    assert _jargon_in(Term(name="warehouse", plain="the warehouse the SQL runs in")) == []
    assert _jargon_in(Term(name="warehouse", plain="the staging warehouse")) == ["staging"]


# --- terms_in


def test_terms_in_finds_only_the_terms_a_string_uses():
    """Exact, not a membership check: a `terms_in` that returned the whole
    glossary would satisfy "materialized is in there" and is precisely the
    round-1 behaviour the walkthroughs found does not work."""
    # One assertion, not two: the membership check this replaced -- `"dbt
    # build" not in _names(...)` -- is a logical consequence of the exact list
    # above it, and no implementation can fail one while passing the other.
    assert _names("this model is materialized as a table") == ["materialized"]


def test_terms_in_finds_a_term_by_the_spelling_it_actually_appears_as():
    """`{{ }}` is never written that way in a model file, and `ref()` is
    written `ref(`. A term whose name is not its spelling is found by the
    spelling or it is never found at all."""
    assert _names("the body reads {{ source('raw', 'orders') }}") == ["{{ }}", "source()"]


def test_terms_in_is_case_insensitive():
    """The report writes "Incremental models" at the start of a sentence and
    "dbt Jinja" mid-sentence. Same word, same definition."""
    assert _names("Incremental models remain deferred") == ["incremental"]
    assert _names("so the body runs as dbt Jinja") == ["Jinja"]


def test_terms_in_returns_each_term_once():
    assert _names("staging models live in the staging folder") == ["staging"]


def test_terms_in_orders_terms_by_where_they_first_appear():
    """A surface defines a word where the reader meets it, so the order is
    the text's, not the glossary's and not the alphabet's. Both directions of
    one pair are asserted: glossary order would put compile first in both,
    and alphabetical order would put build first in both."""
    assert _names("run dbt build after dbt compile") == ["dbt build", "dbt compile"]
    assert _names("dbt compile first, then dbt build") == ["dbt compile", "dbt build"]


# The brief's `test_terms_in_is_stable_in_order` -- `_names(text) ==
# _names(text)` -- is not written here. Every pure function passes it, so no
# mutation of `terms_in` can make it fail; the content it was reaching for is
# what the test above pins, against the two orderings it could have been.


def test_terms_in_finds_nothing_in_text_that_uses_none_of_the_words():
    assert terms_in("") == ()
    assert terms_in("the rows already in the table stay where they are") == ()


def test_no_term_is_named_a_word_that_occurs_inside_other_words():
    """`terms_in` matches on plain substrings and has no word boundaries, on
    purpose -- `materialized` has to be found inside `materialized='table'`,
    and `ref(` inside a rewritten body. So this is a property of the
    *glossary*, not of the matcher: it is safe only because every term is
    named with a prefix (`dbt run`) or punctuation (`ref(`, `{{`) that does
    not recur inside ordinary words. A term named `run` or `test` or `build`
    would be found in every string below.

    The limit, stated so nobody reads more into this than it says: substring
    matching does return `incremental` for "incrementally", `Jinja` for
    "Jinja2" and `staging` for "restaging". Those are the same word or a
    near-enough one, so they are wanted; a bare `run` inside "rerun" is a
    different word, and that is what the naming rule refuses.
    """
    assert _names("rerun the staging models") == ["staging"]
    assert _names("a nightly job that is still running") == []
    assert _names("the team builds and tests every night") == []


def test_no_term_s_spelling_is_hidden_inside_another_s():
    """What makes matching each term independently correct. Two terms whose
    spellings nest -- a `test` beside `dbt test` -- would both match one
    occurrence, and the reader would be handed two definitions for one word
    with nothing saying which is meant. `terms_in` does not resolve that, so
    the glossary may not create it.
    """
    spellings = [(term, spelling.lower()) for term in GLOSSARY for spelling in term.spellings]
    for term_a, a in spellings:
        for term_b, b in spellings:
            if term_a.name == term_b.name:
                continue
            assert a not in b, f"{term_a.name}'s {a!r} hides inside {term_b.name}'s {b!r}"

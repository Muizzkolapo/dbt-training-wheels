"""The one definition of "is this table reference qualified", shared everywhere.

Before slice-6a's final review, `_qualified` (the dotted `catalog.db.name`
join) was triplicated byte-for-byte across `passes.tier1`, `assemble.assembler`,
and `assemble.resolve` (there as `_qualified_key`) — and the qualification
predicate that decides whether a reference is schema/catalog-qualified at all
had quietly drifted between copies: `passes.tier1`/`assemble.assembler` tested
`if ref.db:` while `assemble.resolve` correctly tested `db or catalog`. A
catalog-only reference (e.g. Snowflake's `mydb..orders`) then fell through
`assembler`'s qualified-dependency and source-entry logic as if it were a
plain unqualified bare name, while `resolve` correctly refused to bare-name
match it — so the emitted report could claim a dependency edge (or a source)
that the Decisions and the rewritten body both say never resolved.

This module is the one place both `passes` and `assemble` import the rule
from, so it can never drift between them again. It sits directly under
`dbtw.core` — a sibling of both packages, depending on neither — precisely so
that importing it never risks a cycle: `passes.tier1` needs the same rule
`assemble.assembler`/`assemble.resolve` do, and neither of those packages may
depend on the other just to share it.

Works on anything with `.catalog`, `.db`, `.name` string attributes/properties
— both `assemble.types.TableRef` and sqlglot's `exp.Table` satisfy this
structurally, without either being imported here.
"""

from __future__ import annotations

from typing import Protocol


class Qualifiable(Protocol):
    """Structural shape shared by `TableRef` and sqlglot's `exp.Table`.

    Declared as read-only properties, not plain attributes: `exp.Table`
    exposes `catalog`/`db`/`name` as computed `@property` getters and
    `TableRef` is a frozen dataclass, so neither is writable — a plain
    `catalog: str` annotation here would demand write access neither type
    offers and reject both.
    """

    @property
    def catalog(self) -> str: ...
    @property
    def db(self) -> str: ...
    @property
    def name(self) -> str: ...


def qualified_name(ref: Qualifiable) -> str:
    """Dotted catalog.db.name, dropping empty parts; bare name if unqualified."""
    return ".".join(part for part in (ref.catalog, ref.db, ref.name) if part)


def is_qualified(ref: Qualifiable) -> bool:
    """True when the reference carries a schema and/or catalog the author wrote.

    Two fields, not one, carry qualification. `ref.catalog` alone (Snowflake's
    `mydb..orders`, parsed as catalog="mydb", db="") is still qualified — it
    must never bare-name-match a draft or an existing model. Testing `ref.db`
    alone reopens exactly the identity-bug class this module exists to close.
    """
    return bool(ref.db) or bool(ref.catalog)

"""Shapes shared by every collection endpoint.

Pagination is `limit`/`offset` rather than a cursor. That is a deliberate trade:
offset pagination skips rows the database still has to walk, and a row inserted
mid-scroll shifts the window. Neither matters at the scale of one institution's
students, and a cursor scheme would be complexity this project cannot verify
until Phase 2 brings up a database.

What *is* enforced is a hard ceiling, because `?limit=100000` is the cheapest
denial-of-service in any CRUD API.
"""

from __future__ import annotations

from typing import Annotated

from fastapi import Query
from pydantic import BaseModel, ConfigDict, Field

__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "Limit",
    "Offset",
    "Page",
    "SearchQuery",
]

MAX_PAGE_SIZE = 100
DEFAULT_PAGE_SIZE = 25


class Page[T](BaseModel):
    """A slice of a collection, plus the total so a UI can render a pager.

    `total` is the count matching the filter, not the length of `items` — the
    difference is what tells a caller there is a next page.
    """

    model_config = ConfigDict(from_attributes=True)

    items: list[T]
    total: int = Field(ge=0)
    limit: int
    offset: int


Limit = Annotated[
    int,
    Query(ge=1, le=MAX_PAGE_SIZE, description=f"Rows to return (max {MAX_PAGE_SIZE})."),
]
Offset = Annotated[int, Query(ge=0, description="Rows to skip.")]
SearchQuery = Annotated[
    str | None,
    Query(
        max_length=128,
        description="Case-insensitive match against name or identifier.",
    ),
]

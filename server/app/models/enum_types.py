from enum import Enum as PyEnum
from typing import TypeVar

from sqlalchemy import Enum as SAEnum

E = TypeVar("E", bound=PyEnum)


def pg_enum(enum_cls: type[E], name: str) -> SAEnum:
    """A Postgres ENUM column bound to `enum_cls`, storing each member's
    `.value` rather than SQLAlchemy's default of its `.name`. Our enums are
    `str` mixins with lowercase values (e.g. UserRole.ADMIN == "admin"), and
    the DB-side type is created with those lowercase values in the initial
    migration — without `values_callable` SQLAlchemy would instead send the
    uppercase Python member name and every insert would fail.
    """
    return SAEnum(enum_cls, name=name, values_callable=lambda cls: [e.value for e in cls])

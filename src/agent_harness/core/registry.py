"""Register trusted implementations by explicit configuration name."""

from __future__ import annotations

import re
from collections.abc import Iterator

from agent_harness.core.errors import DuplicateRegistrationError, UnknownRegistrationError

_NAME_PATTERN = re.compile(r"^[a-z][a-z0-9_-]*$")


class Registry[T]:
    """Resolve code-owned implementations without dynamic imports."""

    def __init__(self, kind: str) -> None:
        self._kind = kind
        self._items: dict[str, T] = {}

    def register(self, name: str, item: T) -> None:
        """Register one unique, safely named implementation."""

        if not _NAME_PATTERN.fullmatch(name):
            raise ValueError(
                f"{self._kind.capitalize()} registration name '{name}' must match "
                "'^[a-z][a-z0-9_-]*$'"
            )
        if name in self._items:
            raise DuplicateRegistrationError(
                f"{self._kind.capitalize()} '{name}' is already registered"
            )
        self._items[name] = item

    def resolve(self, name: str) -> T:
        """Return a registered implementation or raise a typed error."""

        try:
            return self._items[name]
        except KeyError as error:
            raise UnknownRegistrationError(
                f"Unknown {self._kind} '{name}'; registered names: "
                f"{', '.join(sorted(self._items)) or '<none>'}"
            ) from error

    def names(self) -> tuple[str, ...]:
        """Return registered names in stable order."""

        return tuple(sorted(self._items))

    def __iter__(self) -> Iterator[tuple[str, T]]:
        return iter(sorted(self._items.items()))

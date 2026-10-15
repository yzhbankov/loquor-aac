"""Reasoner interface."""

from __future__ import annotations


class Reasoner:
    name = "base"

    async def complete(
        self,
        system: str,
        prompt: str,
        *,
        temperature: float | None = None,
        schema: dict | None = None,
    ) -> str:
        """Return a short completion for the prompt.

        ``schema`` asks for JSON matching a JSON schema. Backends that cannot
        enforce it return plain text; callers must validate either way.
        """
        raise NotImplementedError

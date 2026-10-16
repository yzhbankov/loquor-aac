"""The capability document carried in HELLO (AMP §6).

A subset of the A2A Agent Card, CBOR-encoded, without the web-only fields
(endpoint URL, OAuth schemes)::

    {
      "name": str, "description": str, "version": str,
      "skills": [{"id": str, "name": str, "description": str, "tags": [str],
                  "input_schema": {...}, "output_schema": {...}, "min_trust": 0,
                  "triggers": {name: {"description": str, "input_schema": {...}}}}],  # actions a peer may fire
      "llm": {"model": str, "context_window": int, "modalities": [str]},
      "extensions": [int],          # envelope types this agent understands beyond 1-4
      "relay": bool                 # stores and forwards envelopes for others
    }

Capabilities are the skill ids; the beacon's Bloom filter is derived from
them. Optional keys are omitted when empty.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass
class AgentCard:
    name: str
    description: str = ""
    version: str = "1"
    skills: list[dict[str, Any]] = field(default_factory=list)
    llm: dict[str, Any] | None = None
    extensions: list[int] = field(default_factory=list)
    relay: bool = False  # stores and forwards envelopes for others (§8)

    @property
    def capabilities(self) -> list[str]:
        return [str(s["id"]) for s in self.skills]

    def to_dict(self) -> dict[str, Any]:
        doc: dict[str, Any] = {"name": self.name, "description": self.description, "version": self.version,
                               "skills": [_clean(s) for s in self.skills]}
        if self.llm:
            doc["llm"] = _clean(self.llm)
        if self.extensions:
            doc["extensions"] = sorted(set(self.extensions))
        if self.relay:
            doc["relay"] = True
        return doc

    @classmethod
    def from_dict(cls, doc: Any) -> AgentCard:
        if not isinstance(doc, dict) or not isinstance(doc.get("name"), str):
            raise ValueError("capability document needs a name")
        skills = doc.get("skills", [])
        if not isinstance(skills, list) or not all(isinstance(s, dict) and isinstance(s.get("id"), str) for s in skills):
            raise ValueError("skills must be a list of {id: str, ...}")
        extensions = doc.get("extensions", [])
        if not isinstance(extensions, list) or not all(isinstance(e, int) for e in extensions):
            raise ValueError("extensions must be a list of ints")
        return cls(
            name=doc["name"],
            description=str(doc.get("description", "")),
            version=str(doc.get("version", "1")),
            skills=skills,
            llm=doc.get("llm") or None,
            extensions=extensions,
            relay=bool(doc.get("relay", False)),
        )


def skill_entry(
    id: str,
    *,
    name: str = "",
    description: str = "",
    tags: list[str] | None = None,
    input_schema: dict | None = None,
    output_schema: dict | None = None,
    min_trust: int = 0,
    extra: dict[str, Any] | None = None,  # skill-specific keys, e.g. "triggers" (offer_triggers)
) -> dict[str, Any]:
    return _clean({
        "id": id, "name": name or id, "description": description, "tags": tags or [],
        "input_schema": input_schema, "output_schema": output_schema,
        "min_trust": int(min_trust) or None,  # 0 is the default and is omitted
        **(extra or {}),
    })


def _clean(d: dict[str, Any]) -> dict[str, Any]:
    """Drop None / empty values so the CBOR stays small."""
    return {k: v for k, v in d.items() if v not in (None, "", [], {})}

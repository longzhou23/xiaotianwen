"""Perspective-owned read-time projection of canonical entities."""

from __future__ import annotations

from .contracts import EntityReference, IdentityConfig, Perspective


class PerspectiveResolver:
    """Determines only SELF relation and projects confirmed SELF memories.

    Projection is intentionally structured framing only.  Raw memory content is
    never rewritten inside the sentence, because Chinese compound-word boundaries
    cannot be determined reliably with string replacement.
    """

    owner = "Perspective Resolver"

    def __init__(self, identity: IdentityConfig | None = None) -> None:
        self.identity = identity or IdentityConfig()

    def resolve(self, subject: EntityReference | None) -> Perspective:
        if subject is None:
            return Perspective.UNRESOLVED
        if subject.entity_id == self.identity.self_entity:
            return Perspective.AUTOBIOGRAPHICAL
        if subject.entity_id.startswith("group:"):
            return Perspective.SHARED_GROUP
        return Perspective.INTERPERSONAL

    def project(self, content: str, perspective: Perspective) -> str:
        """Return raw content without fragile alias rewriting.

        ``RuntimeMemoryView.perspective`` and the L2 formatter's ``[你的经历]``
        prefix carry the structured SELF attribution.
        """
        return content

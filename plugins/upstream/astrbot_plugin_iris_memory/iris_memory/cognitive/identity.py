"""Identity-owned Entity Registry and deterministic resolution.

No LLM inference is used here.  A name resolves only from a confirmed claim;
platform IDs are the sole automatic canonical-entity creation path.  The
registry is intentionally in-memory until durable registry ownership and
persistence are frozen by the architecture.
"""

from __future__ import annotations

from dataclasses import replace
from typing import Iterable

from .contracts import (
    CanonicalEntity,
    CognitiveContractError,
    EntityReference,
    IdentityClaim,
    IdentityClaimStatus,
    IdentityConfig,
    IdentityStore,
)


def _mention_key(mention: str) -> str:
    return mention.strip().casefold()


class EntityRegistry(IdentityStore):
    """Identity-owned, reversible registry for the current runtime process."""

    owner = "Identity"

    def __init__(self, config: IdentityConfig | None = None) -> None:
        self.config = config or IdentityConfig()
        self._entities: dict[str, CanonicalEntity] = {}
        self._claims: list[IdentityClaim] = []
        self.register_entity(
            CanonicalEntity(
                id=self.config.self_entity,
                aliases=self.config.self_aliases,
            ),
            source="self_binding",
        )

    @property
    def self_entity(self) -> str:
        return self.config.self_entity

    def register_entity(self, entity: CanonicalEntity, *, source: str) -> None:
        """Register an entity and its explicitly supplied aliases/bindings."""
        if not source:
            raise CognitiveContractError("entity registration source is required")
        existing = self._entities.get(entity.id)
        if existing and existing != entity:
            raise CognitiveContractError(f"entity already exists with different data: {entity.id}")
        self._entities[entity.id] = entity
        for alias in entity.aliases:
            self.add_claim(
                IdentityClaim(
                    mention=alias,
                    candidate_entity=entity.id,
                    evidence=(f"entity registration: {entity.id}",),
                    confidence=1.0,
                    source=source,
                    status=IdentityClaimStatus.CONFIRMED,
                )
            )

    def add_claim(self, claim: IdentityClaim) -> None:
        """Store evidence.  Only CONFIRMED claims participate in resolution."""
        if claim.candidate_entity not in self._entities:
            raise CognitiveContractError(
                f"claim candidate entity is not registered: {claim.candidate_entity}"
            )
        self._claims.append(claim)

    def claims_for(self, mention: str) -> tuple[IdentityClaim, ...]:
        key = _mention_key(mention)
        return tuple(claim for claim in self._claims if _mention_key(claim.mention) == key)

    def resolve_alias(self, mention: str) -> EntityReference | None:
        """Resolve only an unambiguous confirmed alias; otherwise fail closed."""
        confirmed = {
            claim.candidate_entity
            for claim in self.claims_for(mention)
            if claim.status is IdentityClaimStatus.CONFIRMED
        }
        if len(confirmed) != 1:
            return None
        entity_id = next(iter(confirmed))
        return EntityReference(
            entity_id=entity_id,
            source="confirmed_alias",
            confidence=1.0,
            evidence=(f"confirmed alias: {mention.strip()}",),
        )

    def resolve_platform_id(self, platform: str, platform_id: str) -> EntityReference | None:
        """Resolve a stable platform ID, creating only its deterministic entity."""
        normalized_platform = platform.strip().casefold()
        normalized_id = platform_id.strip()
        if not normalized_platform or not normalized_id:
            return None
        for entity in self._entities.values():
            if entity.platform_ids.get(normalized_platform) == normalized_id:
                return EntityReference(
                    entity.id,
                    "platform_uid",
                    1.0,
                    (f"{normalized_platform}:{normalized_id}",),
                )

        entity_id = f"person:{normalized_platform}:{normalized_id}"
        self.register_entity(
            CanonicalEntity(
                id=entity_id,
                platform_ids={normalized_platform: normalized_id},
            ),
            source="platform_uid",
        )
        return EntityReference(
            entity_id,
            "platform_uid",
            1.0,
            (f"{normalized_platform}:{normalized_id}",),
        )

    def resolve_mention(
        self,
        mention: str,
        *,
        platform: str = "",
        platform_id: str = "",
    ) -> EntityReference | None:
        """Apply the frozen priority order without any LLM fallback."""
        if platform and platform_id:
            platform_result = self.resolve_platform_id(platform, platform_id)
            if platform_result is not None:
                return platform_result
        return self.resolve_alias(mention)

    def resolve_coreference(
        self,
        mention: str,
        *,
        actor: EntityReference | None,
    ) -> EntityReference | None:
        """Conservative P0 coreference: only speaker ``我`` and confirmed aliases."""
        cleaned = mention.strip()
        if cleaned == "我":
            if actor is None:
                return None
            return EntityReference(
                actor.entity_id,
                "speaker_coreference",
                actor.confidence,
                ("first-person reference resolved to event actor",),
            )
        return self.resolve_alias(cleaned)

    def revoke_claim(self, claim: IdentityClaim) -> IdentityClaim:
        """Return the revoked record and remove its resolution authority."""
        for index, current in enumerate(self._claims):
            if current == claim:
                revoked = replace(current, status=IdentityClaimStatus.REVOKED)
                self._claims[index] = revoked
                return revoked
        raise CognitiveContractError("identity claim is not registered")

    def entities(self) -> tuple[CanonicalEntity, ...]:
        return tuple(self._entities.values())

    def all_claims(self) -> tuple[IdentityClaim, ...]:
        return tuple(self._claims)


class IdentityResolver:
    """Thin resolver façade; Identity is the only writer of the registry."""

    owner = "Identity"

    def __init__(self, registry: EntityRegistry) -> None:
        self.registry = registry

    @property
    def self_entity(self) -> str:
        return self.registry.self_entity

    def resolve_actor(self, platform: str, platform_id: str, display_name: str = "") -> EntityReference | None:
        return self.registry.resolve_mention(
            display_name,
            platform=platform,
            platform_id=platform_id,
        )

    def resolve_event_self(self, platform: str, platform_id: str) -> EntityReference | None:
        """Bind SELF only from this event's explicit bot UID, never from text."""
        if not platform.strip() or not platform_id.strip():
            return None
        return EntityReference(
            self.self_entity,
            "event_self_uid",
            1.0,
            (f"{platform.strip().casefold()}:{platform_id.strip()}",),
        )

    def resolve_mentions(self, mentions: Iterable[tuple[str, str]]) -> tuple[EntityReference, ...]:
        resolved: list[EntityReference] = []
        for platform_id, display_name in mentions:
            entity = self.registry.resolve_mention(
                display_name,
                platform="qq",
                platform_id=platform_id,
            )
            if entity is not None:
                resolved.append(entity)
        return tuple(resolved)

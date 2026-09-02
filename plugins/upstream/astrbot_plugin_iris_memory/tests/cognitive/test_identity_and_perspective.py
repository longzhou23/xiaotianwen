from iris_memory.cognitive.contracts import (
    CanonicalEntity,
    IdentityClaim,
    IdentityClaimStatus,
    IdentityConfig,
    Perspective,
)
from iris_memory.cognitive.identity import EntityRegistry, IdentityResolver
from iris_memory.cognitive.perspective import PerspectiveResolver


def test_uid_is_stable_when_display_name_changes():
    resolver = IdentityResolver(EntityRegistry())

    first = resolver.resolve_actor("qq", "10001", "龙洲")
    renamed = resolver.resolve_actor("qq", "10001", "longz")

    assert first is not None
    assert first.entity_id == "person:qq:10001"
    assert renamed == first


def test_confirmed_alias_resolves_and_revocation_rolls_it_back():
    registry = EntityRegistry()
    registry.register_entity(CanonicalEntity("person:longz"), source="test")
    claim = IdentityClaim(
        mention="龙洲",
        candidate_entity="person:longz",
        evidence=("manual confirmed test fixture",),
        confidence=1.0,
        source="test",
        status=IdentityClaimStatus.CONFIRMED,
    )
    registry.add_claim(claim)

    assert registry.resolve_alias("龙洲").entity_id == "person:longz"
    assert registry.revoke_claim(claim).status is IdentityClaimStatus.REVOKED
    assert registry.resolve_alias("龙洲") is None


def test_possible_alias_never_merges_and_coreference_stays_conservative():
    registry = EntityRegistry()
    registry.register_entity(CanonicalEntity("person:longz"), source="test")
    registry.add_claim(
        IdentityClaim(
            mention="龙妹",
            candidate_entity="person:longz",
            evidence=("unverified chat guess",),
            confidence=0.4,
            source="test",
            status=IdentityClaimStatus.POSSIBLE,
        )
    )

    actor = registry.resolve_platform_id("qq", "10001")
    assert registry.resolve_alias("龙妹") is None
    assert registry.resolve_coreference("我", actor=actor).entity_id == actor.entity_id
    assert registry.resolve_coreference("你", actor=actor) is None


def test_self_binding_projects_only_confirmed_self_memories():
    config = IdentityConfig()
    registry = EntityRegistry(config)
    perspective = PerspectiveResolver(config)
    self_ref = registry.resolve_alias("小天文")

    assert self_ref is not None
    assert perspective.resolve(self_ref) is Perspective.AUTOBIOGRAPHICAL
    # Runtime projection is structured framing; raw memory content is never
    # rewritten inside the sentence.
    assert perspective.project("小天文曾经和 NICEICK 玩梗", Perspective.AUTOBIOGRAPHICAL) == "小天文曾经和 NICEICK 玩梗"
    assert perspective.project("小天文学会曾经组织观测", Perspective.AUTOBIOGRAPHICAL) == "小天文学会曾经组织观测"
    assert perspective.project("小天文爱好者曾经参与观测", Perspective.AUTOBIOGRAPHICAL) == "小天文爱好者曾经参与观测"
    assert perspective.project("小天文台昨晚开放", Perspective.AUTOBIOGRAPHICAL) == "小天文台昨晚开放"
    assert perspective.project("小天文望远镜该选哪种", Perspective.AUTOBIOGRAPHICAL) == "小天文望远镜该选哪种"
    assert perspective.project("小天文知识竞赛", Perspective.AUTOBIOGRAPHICAL) == "小天文知识竞赛"
    assert perspective.project("助手曾经和 NICEICK 玩梗", Perspective.UNRESOLVED) == "助手曾经和 NICEICK 玩梗"

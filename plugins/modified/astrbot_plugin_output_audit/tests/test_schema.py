import pytest

from astrbot_plugin_output_audit.policy import VerdictValidationError, parse_verdict


def test_valid_allow_contract():
    verdict = parse_verdict('{"decision":"allow","risk_level":"none","categories":[],"reason_code":"OK","rewrite_instruction":"","confidence":0.98}')
    assert verdict.decision == "allow"


@pytest.mark.parametrize(
    "payload",
    [
        '{"decision":"allow","risk_level":"high","categories":[],"reason_code":"NO","rewrite_instruction":"","confidence":0.5}',
        '{"decision":"revise","risk_level":"medium","categories":["PRIVACY"],"reason_code":"X","rewrite_instruction":"","confidence":0.5}',
        '{"decision":"block","risk_level":"critical","categories":["NOT_A_CODE"],"reason_code":"X","rewrite_instruction":"","confidence":0.5}',
    ],
)
def test_invalid_contracts_are_rejected(payload):
    with pytest.raises(VerdictValidationError):
        parse_verdict(payload)

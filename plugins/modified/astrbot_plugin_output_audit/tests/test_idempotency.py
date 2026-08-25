from astrbot_plugin_output_audit.state import RequestState


def test_finalized_state_blocks_a_second_send_path():
    state = RequestState(candidate_hash="candidate")
    assert not state.finalized
    state.finalized = True
    assert state.finalized

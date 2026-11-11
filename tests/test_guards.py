import pytest

from coldopen.guards import (GuardError, check_duration, check_llm_budget, check_render_gate, check_source,
                             cut_digest, make_approval, verify_proposals)
from coldopen.models import MomentProposal
from tests.conftest import P


def props(*items):
    return [MomentProposal.model_validate(i) for i in items]


def test_input_guard_allows_youtube_only(settings):
    assert check_source("https://www.youtube.com/watch?v=abc", settings)["kind"] == "url"
    assert check_source("https://youtu.be/abc", settings)["kind"] == "url"
    for bad in ["https://evil.example.com/v.mp4", "http://169.254.169.254/latest", "", "/no/such/file.mp4"]:
        with pytest.raises(GuardError) as e:
            check_source(bad, settings)
        assert e.value.stop_reason == "REJECTED_INPUT"


def test_input_guard_rejects_wrong_extension(settings, tmp_path):
    f = tmp_path / "notes.txt"
    f.write_text("hi")
    with pytest.raises(GuardError):
        check_source(str(f), settings)


def test_budget_guard(settings):
    check_duration(600, settings.budgets)
    with pytest.raises(GuardError) as e:
        check_duration(settings.budgets.max_source_minutes * 60 + 60, settings.budgets)
    assert e.value.stop_reason == "BUDGET_EXCEEDED"
    with pytest.raises(GuardError):
        check_llm_budget(settings.budgets.max_llm_calls, 0, 0, settings.budgets)
    with pytest.raises(GuardError):
        check_llm_budget(0, 0, settings.budgets.max_cost_usd, settings.budgets)


def test_verifier_uses_transcript_times_not_model(transcript):
    ok, rejected = verify_proposals(props(P(2, 4)), transcript.sentences)
    assert len(ok) == 1 and not rejected
    m = ok[0]
    s = {x.id: x for x in transcript.sentences}
    assert s[1].end <= m.start <= s[2].start          # snapped to the sentence, with a little lead-in
    assert s[4].end <= m.end <= s[5].start             # and a little tail, never into the next sentence
    assert m.text.startswith("Here is the thing")


def test_verifier_rejects_invented_overlong_and_overlapping(transcript):
    ok, rejected = verify_proposals(
        props(P(99, 120), P(4, 2), P(0, 9), P(2, 3, hook=9), P(3, 4)), transcript.sentences, max_len=12
    )
    whys = sorted(r["why"] for r in rejected)
    assert any("does not exist" in w for w in whys)
    assert any("end before start" in w for w in whys)
    assert any("duration" in w for w in whys)
    assert any("overlaps" in w for w in whys)
    assert [m.id for m in ok] == ["m0002-0003"]


def test_render_gate_denies_by_default(transcript, settings):
    ok, _ = verify_proposals(props(P(2, 3), P(6, 7)), transcript.sentences)
    with pytest.raises(GuardError):
        check_render_gate(ok, None)
    appr = make_approval(ok, "me", "human", 1)
    check_render_gate(ok, appr)
    edited = list(reversed(ok))                     # any edit after approval changes the digest
    assert cut_digest(edited) != appr.digest
    with pytest.raises(GuardError):
        check_render_gate(edited, appr)
    expired = make_approval(ok, "me", "human", -1)
    with pytest.raises(GuardError):
        check_render_gate(ok, expired)

from coldopen.guards import verify_proposals
from coldopen.models import MomentProposal
from coldopen.sequencer import build_cut, cut_length
from tests.conftest import P


def test_cut_fits_budget_and_orders_for_tension(transcript):
    items = [P(2, 3, role="opener", hook=9, clarity=9), P(4, 4, emotion=9, surprise=8),
             P(6, 7, emotion=5, surprise=5), P(8, 9, role="closer", emotion=8, surprise=9)]
    ok, _ = verify_proposals([MomentProposal.model_validate(i) for i in items], transcript.sentences)
    cut = build_cut(ok, target=30, min_clips=3, max_clips=6)
    assert 3 <= len(cut) <= 6
    assert cut_length(cut) <= 30 * 1.1
    assert cut[0].id == "m0002-0003"          # strongest hook opens
    assert cut[-1].id == "m0008-0009"          # cliffhanger closes
    assert [m.order for m in cut] == list(range(1, len(cut) + 1))


def test_sentence_split_on_punctuation(transcript):
    assert len(transcript.sentences) == 10
    assert transcript.sentences[3].text == "Most agents fail because nobody set a budget."

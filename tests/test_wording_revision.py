from types import SimpleNamespace

import pytest

from backend.latex_workflow import (Tailoring, Edit, Score, Judgment, RevisedJudgment,
                                    SelectionAdvice, Revision, candidates_for, choose,
                                    revise_from_feedback)


def fixture():
    context = {'bullets': [{'id': f'b{i}', 'text': 'Built a Python API.', 'entry_id':'e1'}
                           for i in range(1, 6)]}
    proposal = Tailoring(selected_bullet_ids=[f'b{i}' for i in range(1, 6)], edits=[
        Edit(bullet_id='b1', alternatives=['Developed a Python API.', 'Implemented a Python API.', 'Created a Python API.'])])
    return context, proposal, candidates_for(proposal, context)


def judgment(candidates, value=70, original=60):
    return RevisedJudgment(evaluations=[Score(candidate_id=c['candidate_id'], support='SUPPORTED',
        relevance=original if c['candidate_id'].endswith('-0') else value,
        clarity=original if c['candidate_id'].endswith('-0') else value,
        specificity=original if c['candidate_id'].endswith('-0') else value,
        concision=original if c['candidate_id'].endswith('-0') else value, reason='MOCKED weak specificity')
        for c in candidates], selection_advice=SelectionAdvice(drop_bullet_ids=[],add_bullet_ids=[],reason='MOCKED'))


def test_writer_applies_feedback_once_and_harsh_reviewer_sees_all(tmp_path):
    context, proposal, candidates = fixture()
    calls = []
    def call(role, instruction, data, schema):
        calls.append(role)
        if role == 'revise':
            assert data['target_bullet_ids'] == ['b1']
            assert data['reviewer_feedback'][0]['reason'] == 'MOCKED weak specificity'
            return Revision(edits=[Edit(bullet_id='b1', alternatives=[
                'Developed the Python API.', 'Created the Python API.', 'Built an API using Python.'])])
        assert role == 'judge' and len(data['candidates']) == 7
        assert data['candidates'][0]['text'] == 'Built a Python API.'
        result = judgment(data['candidates'])
        # New candidates sound better but lack support: keep prior valid winner.
        for score in result.evaluations:
            if score.candidate_id.endswith('-r1'):
                score.support = 'UNSUPPORTED'
                score.relevance = score.clarity = score.specificity = score.concision = 100
        return result
    combined, reviewed, record = revise_from_feedback(SimpleNamespace(call=call, mocked=True), tmp_path,
        context, 'Synthetic JD', proposal, candidates, judgment(candidates))
    replacements, decisions = choose(combined, reviewed)
    assert replacements['b1'] == 'Developed a Python API.'
    assert calls == ['revise', 'judge'] and record['rounds'] == 1
    assert (tmp_path/'wording-revision.json').exists()


def test_no_improvement_retains_original_after_one_round(tmp_path):
    context, proposal, candidates = fixture()
    calls = []
    def call(role, instruction, data, schema):
        calls.append(role)
        if role == 'revise':
            return Revision(edits=proposal.edits)
        return judgment(data['candidates'], value=62)
    combined, review, _ = revise_from_feedback(SimpleNamespace(call=call, mocked=True), tmp_path,
        context, 'Synthetic', proposal, candidates, judgment(candidates, value=62))
    assert choose(combined, review)[0] == {}
    assert calls == ['revise', 'judge']


def test_strong_supported_edit_needs_no_regeneration(tmp_path):
    context, proposal, candidates = fixture()
    runner = SimpleNamespace(call=lambda *_: pytest.fail('No unnecessary iteration'))
    _, _, record = revise_from_feedback(runner, tmp_path, context, 'Synthetic', proposal, candidates,
                                       judgment(candidates, value=90))
    assert record is None


def test_revision_cannot_change_targets(tmp_path):
    context, proposal, candidates = fixture()
    wrong = Revision(edits=[Edit(bullet_id='b2', alternatives=proposal.edits[0].alternatives)])
    runner = SimpleNamespace(call=lambda *_: wrong)
    with pytest.raises(ValueError, match='exactly the requested'):
        revise_from_feedback(runner, tmp_path, context, 'Synthetic', proposal, candidates, judgment(candidates))


def test_revision_reviewer_outage_cannot_approve(tmp_path):
    context, proposal, candidates = fixture()
    def call(role, *args):
        if role == 'revise':
            return Revision(edits=proposal.edits)
        raise ValueError('Reviewer unavailable')
    with pytest.raises(ValueError, match='Reviewer unavailable'):
        revise_from_feedback(SimpleNamespace(call=call), tmp_path, context, 'Synthetic', proposal,
                             candidates, judgment(candidates))
    assert not (tmp_path/'wording-revision.json').exists()

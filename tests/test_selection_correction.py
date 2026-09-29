import json
from types import SimpleNamespace

import pytest

from backend.latex_workflow import SelectionAdvice, reviewed_selection


def selection_context():
    entries = [
        {'id': 'education', 'section': 'Education', 'bullets': ['b0']},
        {'id': 'work', 'section': 'Experience', 'bullets': [f'b{i}' for i in range(1, 18)]},
        {'id': 'project', 'section': 'Personal Projects', 'bullets': ['b18', 'b19']},
    ]
    return {'entries': entries, 'bullets': [
        {'id': b, 'entry_id': e['id']} for e in entries for b in e['bullets']]}


def advice(drops=(), adds=()):
    return SelectionAdvice(drop_bullet_ids=list(drops), add_bullet_ids=list(adds), reason='MOCKED')


def test_one_selection_correction_preserves_scored_wording(tmp_path):
    selected = [f'b{i}' for i in range(17)] + ['b18']
    replacements = {'b1': 'Previously evaluated wording'}
    calls = []
    def call(role, instruction, data, schema):
        calls.append(role)
        assert '5–18' in instruction and 'ONE or TWO' in instruction
        assert data['validation_error'] == 'Reviewer selection must retain 5–18 source bullets'
        assert schema is SelectionAdvice
        return advice(['b16'], ['b19'])
    result, words = reviewed_selection(SimpleNamespace(call=call), tmp_path, selection_context(),
                                      selected, replacements, advice(adds=['b19']), 'Synthetic JD')
    assert len(result) == 18 and 'b19' in result and 'b16' not in result
    assert words == replacements and calls == ['content_repair']
    assert json.loads((tmp_path/'selection-correction-request.json').read_text())['original_advice']['add_bullet_ids'] == ['b19']


def test_invalid_correction_stops_after_one_call(tmp_path):
    selected = [f'b{i}' for i in range(17)] + ['b18']
    calls = []
    def call(*args):
        calls.append(args)
        return advice(adds=['b19'])
    with pytest.raises(ValueError, match='retain 5–18'):
        reviewed_selection(SimpleNamespace(call=call), tmp_path, selection_context(), selected,
                           {}, advice(adds=['b19']), 'Synthetic JD')
    assert len(calls) == 1


def test_correction_cannot_drop_education(tmp_path):
    correction = advice(['b0'])
    selected = ['b0', 'b1', 'b2', 'b3', 'b4', 'b18']
    runner = SimpleNamespace(call=lambda *args: correction)
    with pytest.raises(ValueError, match='Education'):
        reviewed_selection(runner, tmp_path, selection_context(), selected, {}, correction, 'Synthetic JD')


def test_valid_selection_does_not_call_reviewer(tmp_path):
    selected = ['b0', 'b1', 'b2', 'b3', 'b18']
    def forbidden(*args):
        pytest.fail('Valid advice needs no correction call')
    result, _ = reviewed_selection(SimpleNamespace(call=forbidden), tmp_path, selection_context(),
                                  selected, {}, advice(), 'Synthetic JD')
    assert result == selected

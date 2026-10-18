"""标准评测的答案边界和来源绑定，不依赖第三方评分器或模型。"""

import json

import pytest

from repopilot.evaluation import swebench as bench


@pytest.fixture
def record():
    return {'instance_id': 'example__repo-1', 'repo': 'example/repo', 'base_commit': 'a' * 40,
            'version': '1.0', 'problem_statement': 'Fix the reported behavior.',
            'patch': 'reference answer\n', 'test_patch': 'hidden checks',
            'FAIL_TO_PASS': '["hidden_test"]', 'PASS_TO_PASS': '["regression"]',
            'hints_text': 'later discussion', 'future_unknown_field': 'another answer'}


def prepare(tmp_path, record):
    dataset = tmp_path / 'dataset.jsonl'
    dataset.write_text(json.dumps(record) + '\n', encoding='utf-8')
    output = tmp_path / 'case'
    bench.prepare(dataset, record['instance_id'], output, dataset_name='test-fixture',
                  dataset_revision='b' * 40, harness_revision='c' * 40)
    return output


def test_solver_input_has_only_pre_issue_information(tmp_path, record):
    directory = prepare(tmp_path, record)
    solver = json.loads((directory / 'solver-input.json').read_text(encoding='utf-8'))
    assert set(solver) == set(bench.SOLVER_FIELDS)
    assert all(value not in solver.values() for value in ('hidden checks', 'later discussion', 'another answer'))
    assert bench.load_case(directory)[1] == record


@pytest.mark.parametrize('artifact', ['solver-input.json', 'scoring-record.jsonl'])
def test_changed_artifacts_cannot_be_exported(tmp_path, record, artifact):
    directory = prepare(tmp_path, record)
    path = directory / artifact
    changed = json.loads(path.read_text(encoding='utf-8'))
    changed['problem_statement'] = 'Changed after freezing'
    path.write_text(json.dumps(changed), encoding='utf-8')
    with pytest.raises(ValueError, match='artifact_changed'):
        bench.export_prediction(directory, tmp_path / 'out', kind='reference', label='gold')
    assert not (tmp_path / 'out').exists()


@pytest.mark.parametrize('kind', ['agent', 'reference', 'empty'])
def test_prediction_preserves_exact_patch_and_distinguishes_controls(tmp_path, record, kind):
    directory = prepare(tmp_path, record)
    patch = tmp_path / 'candidate.patch'
    patch.write_bytes(b'diff --git a/f b/f\n+candidate\n')
    result = bench.export_prediction(directory, tmp_path / 'out', kind=kind, label='experiment',
                                    patch=patch if kind == 'agent' else None)
    data = (tmp_path / 'out/predictions.jsonl').read_bytes()
    prediction = json.loads(data)
    expected = {'agent': patch.read_text(), 'reference': record['patch'], 'empty': ''}[kind]
    assert prediction['model_patch'] == expected
    assert prediction['instance_id'] == record['instance_id']
    assert result['kind'] == kind and result['status'] == 'not_evaluated'
    assert result['predictions_sha256'] == bench.digest(data)
    assert set(prediction) == {'model_patch', 'instance_id', 'model_name_or_path'}
    with pytest.raises(FileExistsError):
        bench.export_prediction(directory, tmp_path / 'out', kind='empty', label='overwrite')


def test_duplicate_instance_cannot_silently_select_first(tmp_path, record):
    dataset = tmp_path / 'duplicate.jsonl'
    dataset.write_text((json.dumps(record) + '\n') * 2)
    with pytest.raises(ValueError, match='exactly_once'):
        bench.prepare(dataset, record['instance_id'], tmp_path / 'case', dataset_name='fixture',
                      dataset_revision='b' * 40, harness_revision='c' * 40)
    assert not (tmp_path / 'case').exists()


@pytest.mark.parametrize('key,value', [('base_commit', 'main'), ('repo', '../bad'),
                                      ('instance_id', '../escape'), ('problem_statement', '')])
def test_invalid_identity_fails_before_writing(tmp_path, record, key, value):
    record[key] = value
    with pytest.raises(ValueError):
        prepare(tmp_path, record)
    assert not (tmp_path / 'case').exists()


def test_controls_cannot_accidentally_use_candidate_patch(tmp_path, record):
    directory = prepare(tmp_path, record)
    with pytest.raises(ValueError, match='patch_only_required'):
        bench.export_prediction(directory, tmp_path / 'out', kind='reference', label='gold', patch='candidate')


def test_cli_records_absence_of_evaluation(tmp_path, record, capsys):
    directory = prepare(tmp_path, record)
    assert bench.main(['export', str(directory), '--output', str(tmp_path / 'out'),
                       '--kind', 'empty', '--label', 'empty-control']) == 0
    assert json.loads(capsys.readouterr().out)['status'] == 'not_evaluated'

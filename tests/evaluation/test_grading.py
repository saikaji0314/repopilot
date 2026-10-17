"""用真实本地Git与解释器检查评分边界，不调用模型。"""

from difflib import unified_diff
import json
from pathlib import Path
import shutil

import pytest

from repopilot.evaluation import cases, grading
from repopilot.evaluation.__main__ import main


@pytest.mark.parametrize('case_id', ['python-limit', 'javascript-slug'])
@pytest.mark.parametrize('patch_name, expected', [(None, 'unsolved'), ('correct', 'solved'), ('bad', 'unsolved')])
def test_fixed_baseline_good_and_bad_patches(tmp_path, case_id, patch_name, expected):
    if case_id == 'javascript-slug' and not shutil.which('node'):
        pytest.skip('JavaScript开发题需要Node.js')
    directory = cases.CASE_ROOT / case_id
    patch = None if patch_name is None else directory / 'patches' / f'{patch_name}.patch'
    before = cases.tree_digest(directory / 'baseline')
    report = grading.evaluate(case_id, patch, tmp_path / 'work')
    assert report['status'] == expected, report
    assert [r['verdict'] for r in report['baseline_checks']] == ['passed', 'failed']
    assert report['candidate_checks'][0]['verdict'] == 'passed'
    assert cases.tree_digest(directory / 'baseline') == before
    assert not list((tmp_path / 'work').iterdir())


def test_modifying_public_test_cannot_pass(tmp_path):
    patch = tmp_path / 'tamper.patch'
    original = (cases.CASE_ROOT / 'python-limit/baseline/public_check.py').read_text(encoding='utf-8')
    patch.write_text(''.join(unified_diff(original.splitlines(True), ['# 绕过检查\n'],
        fromfile='a/public_check.py', tofile='b/public_check.py')), encoding='utf-8')
    report = grading.evaluate('python-limit', patch, tmp_path / 'work')
    assert report['status'] == 'invalid_patch'
    assert not report['candidate_checks']


@pytest.mark.parametrize('payload', ['invalid diff', '', 'x' * (grading.MAX_PATCH_BYTES + 1)],
                         ids=['malformed', 'empty', 'oversized'])
def test_invalid_or_oversized_patch_is_rejected(tmp_path, payload):
    patch = tmp_path / 'bad.patch'
    patch.write_text(payload, encoding='utf-8')
    result = grading.evaluate('python-limit', patch, tmp_path / 'work')
    assert result['status'] == 'invalid_patch', result


def test_changed_fixture_is_not_scored(tmp_path, monkeypatch):
    copied = tmp_path / 'cases'
    shutil.copytree(cases.CASE_ROOT / 'python-limit', copied / 'python-limit')
    (copied / 'python-limit/baseline/batches.py').write_text('changed', encoding='utf-8')
    monkeypatch.setattr(cases, 'CASE_ROOT', copied)
    result = grading.evaluate('python-limit', None, tmp_path / 'work')
    assert result['status'] == 'environment_error'
    assert result['reason'] == 'baseline_hash_mismatch'
    assert not result['baseline_checks']


def test_unavailable_check_executable_is_environment_error(tmp_path, monkeypatch):
    def unavailable(*args):
        raise FileNotFoundError('synthetic missing interpreter')
    monkeypatch.setattr(grading, 'run_check', unavailable)
    report = grading.evaluate('python-limit', None, tmp_path / 'work')
    assert report['status'] == 'environment_error'
    assert report['reason'] == 'FileNotFoundError'


def test_broken_candidate_is_not_blamed_on_environment(tmp_path):
    original = (cases.CASE_ROOT / 'python-limit/baseline/batches.py').read_text(encoding='utf-8')
    patch = tmp_path / 'syntax.patch'
    patch.write_text(''.join(unified_diff(original.splitlines(True), ['def newest(:\n'],
        fromfile='a/batches.py', tofile='b/batches.py')), encoding='utf-8', newline='\n')
    result = grading.evaluate('python-limit', patch, tmp_path / 'work')
    assert result['status'] == 'evaluation_error'
    assert any('SyntaxError' in item['output'] for item in result['candidate_checks'])


@pytest.mark.parametrize('code, output, verdict', [
    (1, 'SyntaxError: broken oracle', 'error'), (0, '', 'error'),
    (1, '{"repair_check":"failed"}', 'failed'),
    (0, '{"repair_check":"failed"}', 'error'),
])
def test_process_failure_is_not_automatically_a_failing_assertion(code, output, verdict):
    assert grading.check_verdict({'exit_code': code, 'output': output, 'timed_out': False}) == verdict


def test_timeout_stays_distinct_from_unsolved(tmp_path, monkeypatch):
    original = grading.execute_checks
    calls = []
    def timeout_on_candidate(task):
        calls.append(task)
        if len(calls) == 1:
            return original(task)
        return [{'name': 'acceptance:requirements', 'exit_code': -1, 'verdict': 'timeout', 'timed_out': True}]
    monkeypatch.setattr(grading, 'execute_checks', timeout_on_candidate)
    assert grading.evaluate('python-limit', None, tmp_path / 'work')['status'] == 'timeout'


def test_cli_persists_complete_report(tmp_path, capsys):
    patch = cases.CASE_ROOT / 'python-limit/patches/correct.patch'
    assert main(['python-limit', '--patch', str(patch), '--output', str(tmp_path)]) == 0
    result = json.loads(capsys.readouterr().out)
    saved = json.loads(Path(result['report_path']).read_text(encoding='utf-8'))
    assert saved['status'] == 'solved'
    assert saved['patch_sha256'] == result['patch_sha256']
    assert saved['candidate_checks']

"""SWE-bench 输入隔离与标准补丁导出；不加载模型，不替代官方评分器。"""

import argparse
from hashlib import sha256
import json
from pathlib import Path
import re


SOLVER_FIELDS = ('instance_id', 'repo', 'base_commit', 'problem_statement', 'version')


def digest(data):
    return sha256(data).hexdigest()


def encoded(value):
    return (json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2) + '\n').encode('utf-8')


def write_json(path, value):
    path.write_bytes(encoded(value))


def revision(value):
    if not isinstance(value, str) or not re.fullmatch(r'[0-9a-f]{40}', value):
        raise ValueError('revision_requires_full_commit_sha')
    return value


def validate_record(record):
    if not isinstance(record, dict):
        raise ValueError('invalid_dataset_record')
    if any(not isinstance(record.get(key), str) or not record[key].strip() for key in SOLVER_FIELDS):
        raise ValueError('missing_solver_field')
    if not re.fullmatch(r'[A-Za-z0-9_.-]+__[A-Za-z0-9_.-]+-\d+', record['instance_id']):
        raise ValueError('invalid_instance_id')
    if not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_.-]*/[A-Za-z0-9][A-Za-z0-9_.-]*', record['repo']):
        raise ValueError('invalid_repository')
    revision(record['base_commit'])
    if not isinstance(record.get('patch'), str) or not record['patch'].strip():
        raise ValueError('missing_reference_patch')


def prepare(dataset, instance_id, output, *, dataset_name, dataset_revision, harness_revision):
    """显式选题并冻结来源；控制记录不能复制进解题工作区。"""
    revision(dataset_revision)
    revision(harness_revision)
    if not dataset_name.strip():
        raise ValueError('dataset_name_required')
    data = Path(dataset).read_bytes()
    records = [json.loads(line) for line in data.decode('utf-8').splitlines() if line.strip()]
    if any(not isinstance(record, dict) for record in records):
        raise ValueError('invalid_dataset_record')
    selected = [row for row in records if row.get('instance_id') == instance_id]
    if len(selected) != 1:
        raise ValueError('instance_must_match_exactly_once')
    record = selected[0]
    validate_record(record)
    solver = {key: record[key] for key in SOLVER_FIELDS}
    manifest = {'schema_version': '1.0', 'instance_id': instance_id,
                'dataset_name': dataset_name, 'dataset_revision': dataset_revision,
                'dataset_sha256': digest(data), 'harness_revision': harness_revision,
                'record_sha256': digest(encoded(record)), 'solver_sha256': digest(encoded(solver))}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'scoring-record.jsonl').write_bytes(
        (json.dumps(record, ensure_ascii=False) + '\n').encode('utf-8'))
    write_json(output / 'solver-input.json', solver)
    write_json(output / 'manifest.json', manifest)
    return manifest


def load_case(directory):
    directory = Path(directory)
    manifest = json.loads((directory / 'manifest.json').read_text(encoding='utf-8'))
    if not isinstance(manifest, dict) or manifest.get('schema_version') != '1.0':
        raise ValueError('unsupported_benchmark_manifest')
    revision(manifest['dataset_revision'])
    revision(manifest['harness_revision'])
    record = json.loads((directory / 'scoring-record.jsonl').read_text(encoding='utf-8'))
    validate_record(record)
    solver = {key: record[key] for key in SOLVER_FIELDS}
    if (digest(encoded(record)) != manifest['record_sha256'] or
            (directory / 'solver-input.json').read_bytes() != encoded(solver) or
            digest(encoded(solver)) != manifest['solver_sha256'] or
            record['instance_id'] != manifest['instance_id']):
        raise ValueError('benchmark_artifact_changed')
    return manifest, record


def export_prediction(directory, output, *, kind, label, patch=None):
    """导出官方 JSONL 与本地来源清单；参考补丁绝不能算模型成绩。"""
    manifest, record = load_case(directory)
    if not label.strip() or '\n' in label or '\r' in label:
        raise ValueError('prediction_label_required')
    if kind == 'agent' and patch is not None:
        patch_data = Path(patch).read_bytes()
    elif kind == 'reference' and patch is None:
        patch_data = record['patch'].encode('utf-8')
    elif kind == 'empty' and patch is None:
        patch_data = b''
    else:
        raise ValueError('patch_only_required_for_agent_prediction')
    prediction = {'instance_id': record['instance_id'], 'model_patch': patch_data.decode('utf-8'),
                  'model_name_or_path': label}
    data = (json.dumps(prediction, ensure_ascii=False) + '\n').encode('utf-8')
    provenance = {'schema_version': '1.0', 'kind': kind, 'label': label,
                  'case': manifest, 'patch_sha256': digest(patch_data),
                  'predictions_sha256': digest(data), 'status': 'not_evaluated'}
    output = Path(output)
    output.mkdir(parents=True, exist_ok=False)
    (output / 'predictions.jsonl').write_bytes(data)
    write_json(output / 'provenance.json', provenance)
    return provenance


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    commands = parser.add_subparsers(dest='command', required=True)
    prepare_parser = commands.add_parser('prepare', help='冻结单个开发案例，不执行仓库代码')
    prepare_parser.add_argument('dataset', type=Path, help='官方数据导出的JSONL文件')
    prepare_parser.add_argument('--instance', required=True)
    prepare_parser.add_argument('--output', required=True, type=Path)
    prepare_parser.add_argument('--dataset-name', required=True)
    prepare_parser.add_argument('--dataset-revision', required=True)
    prepare_parser.add_argument('--harness-revision', required=True)
    export_parser = commands.add_parser('export', help='导出补丁；评分另由官方工具执行')
    export_parser.add_argument('case', type=Path)
    export_parser.add_argument('--output', required=True, type=Path)
    export_parser.add_argument('--kind', required=True, choices=('agent', 'reference', 'empty'))
    export_parser.add_argument('--label', required=True)
    export_parser.add_argument('--patch', type=Path)
    args = parser.parse_args(argv)
    try:
        if args.command == 'prepare':
            result = prepare(args.dataset, args.instance, args.output, dataset_name=args.dataset_name,
                             dataset_revision=args.dataset_revision, harness_revision=args.harness_revision)
        else:
            result = export_prediction(args.case, args.output, kind=args.kind, label=args.label, patch=args.patch)
    except (ValueError, KeyError, OSError) as exc:
        # 不把题目、补丁或路径错误中的原始内容输出为结构化运行证据。
        parser.exit(2, f'benchmark_artifact_error: {type(exc).__name__}\n')
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

"""从源码checkout运行公开开发题，不调用模型或读取运行时凭证。"""

import argparse
import json
from pathlib import Path
from uuid import uuid4

from repopilot.runtime.config import PROJECT_ROOT
from .grading import evaluate


def main(argv=None):
    parser = argparse.ArgumentParser(description='独立验证候选补丁，不采信Agent报告')
    parser.add_argument('case_id')
    parser.add_argument('--patch', type=Path, help='省略时评价未修复基线')
    parser.add_argument('--output', type=Path, default=PROJECT_ROOT / '.local' / 'repair-evals')
    args = parser.parse_args(argv)
    try:
        report = evaluate(args.case_id, args.patch, args.output / '_work')
    except (ValueError, OSError) as exc:
        report = {'status': 'environment_error', 'error_type': type(exc).__name__}
    directory = args.output / f'{uuid4().hex}'
    directory.mkdir(parents=True)
    (directory / 'report.json').write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding='utf-8')
    print(json.dumps(report | {'report_path': str(directory / 'report.json')}, ensure_ascii=True, indent=2))
    return 0 if report['status'] == 'solved' else 1


if __name__ == '__main__':
    raise SystemExit(main())

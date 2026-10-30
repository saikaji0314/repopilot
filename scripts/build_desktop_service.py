"""Build a self-contained Windows local service and collect dependency notices."""
from importlib import metadata
import json
from pathlib import Path
import shutil
import sys


def main():
    if sys.platform != 'win32':
        raise SystemExit('Build the Windows desktop service on Windows.')
    root = Path(__file__).resolve().parents[1]
    output = root / 'desktop/release'
    licenses = output / 'third-party-licenses'
    licenses.mkdir(parents=True, exist_ok=True)
    records = []
    for source in Path(sys.base_prefix).glob('LICENSE*'):
        if source.is_file():
            target = licenses / 'python-runtime' / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    # Include original license/notice text; never traverse local runtime or config.
    for distribution in metadata.distributions():
        name = distribution.metadata.get('Name', 'unknown')
        for file in distribution.files or []:
            if Path(file).name.lower().startswith(('license', 'copying', 'notice')):
                source = Path(distribution.locate_file(file))
                if source.is_file() and source.stat().st_size <= 2_000_000:
                    target = licenses / ('python-' + name) / Path(file).name
                    target.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copyfile(source, target)
        records.append({'ecosystem': 'python', 'name': name, 'version': distribution.version})
    modules = root / 'desktop/node_modules/.pnpm'
    for manifest in modules.glob('*/node_modules/*/package.json'):
        copy_node_license(manifest, licenses, records)
    for manifest in modules.glob('*/node_modules/@*/*/package.json'):
        copy_node_license(manifest, licenses, records)
    (licenses / 'build-dependencies.json').write_text(json.dumps(records, indent=2, ensure_ascii=False), encoding='utf-8')
    from PyInstaller.__main__ import run
    args = ['--noconfirm', '--onedir', '--console', '--name', 'repopilot-service',
            '--distpath', str(output / 'backend'), '--workpath', str(root / '.local/desktop-build/work'),
            '--specpath', str(root / '.local/desktop-build'), '--collect-all', 'repopilot',
            '--collect-all', 'uvicorn', '--collect-all', 'langchain_deepseek', '--collect-all', 'langchain_openai',
            '--collect-all', 'tiktoken_ext', '--recursive-copy-metadata', 'repopilot',
            '--exclude-module', 'tkinter', '--exclude-module', 'matplotlib',
            str(root / 'desktop/electron/service.py')]
    run(args)
    # Editable-install provenance is build-machine metadata, not a runtime need.
    bundle = (output / 'backend/repopilot-service').resolve()
    for file in bundle.rglob('direct_url.json'):
        if not file.resolve().is_relative_to(bundle):
            raise RuntimeError('unexpected_build_path')
        file.unlink()


def copy_node_license(manifest, licenses, records):
    value = json.loads(manifest.read_text(encoding='utf-8'))
    name = value.get('name', 'unknown')
    safe = name.replace('/', '--').replace('@', '')
    for source in manifest.parent.iterdir():
        if source.is_file() and source.name.lower().startswith(('license', 'copying', 'notice')):
            target = licenses / ('node-' + safe + '-' + value.get('version', 'unknown')) / source.name
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target)
    records.append({'ecosystem': 'node', 'name': name, 'version': value.get('version'), 'license': value.get('license')})


if __name__ == '__main__':
    main()

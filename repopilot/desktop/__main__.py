"""Spawned by Electron. The session token travels through the environment, never stdout."""
import argparse
import json
import os
from pathlib import Path
import socket

import uvicorn

from repopilot.core.paths import default_runtime_root
from repopilot.desktop.api import create_app


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument('--output', type=Path, default=default_runtime_root())
    parser.add_argument('--ui', type=Path)
    args = parser.parse_args()
    token = os.environ.pop('REPOPILOT_DESKTOP_TOKEN', '')
    app = create_app(args.output, token, args.ui)
    sock = socket.socket()
    sock.bind(('127.0.0.1', 0))
    sock.listen(128)
    print(json.dumps({'desktop_ready': True, 'port': sock.getsockname()[1]}), flush=True)
    uvicorn.Server(uvicorn.Config(app, log_level='warning')).run(sockets=[sock])


if __name__ == '__main__':
    main()

"""此文件作为受信Python源码发送到容器；只提供固定文件操作，不装模型依赖。"""

# Repository由控制面拼接的file_access源码提供；输入只作为JSON数据解析。
import itertools
import json
import sys

def serve():
    request = json.load(sys.stdin)
    repository = Repository(Path('/work'))  # noqa: F821
    action = request['action']
    arguments = request.get('arguments', [])
    try:
        if action == 'files':
            result = list(itertools.islice(repository.files(), 4001))
        elif action == 'cwd':
            target = repository.resolve(arguments[0])
            if not target.is_dir():
                raise ValueError('check_cwd_not_directory')
            result = str(target)
        elif action in {'read', 'text', 'search', 'write', 'replace'}:
            result = getattr(repository, action)(*arguments)
        else:
            raise ValueError('unknown_file_action')
        print(json.dumps({'ok': True, 'result': result}, ensure_ascii=True))
    except (ValueError, OSError) as error:
        print(json.dumps({'ok': False, 'error': str(error) if isinstance(error, ValueError) else 'repository_io_error'}))


if __name__ == '__main__':
    serve()

"""Thread-local public command output; never changes command execution or result semantics."""
from contextlib import contextmanager
from contextvars import ContextVar

_sink = ContextVar('command_output_sink', default=None)


@contextmanager
def command_output(sink):
    token = _sink.set(sink)
    try:
        yield
    finally:
        _sink.reset(token)


def emit_output(text):
    sink = _sink.get()
    if sink and text:
        sink(text)

"""Authenticated loopback API, shared by Electron and deterministic integration tests."""

import asyncio
import json
from pathlib import Path
import secrets

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse
from fastapi.staticfiles import StaticFiles
from fastapi.exceptions import RequestValidationError
from pydantic import BaseModel, Field, ValidationError

from repopilot.desktop.control import request_pause
from repopilot.desktop.read_model import DesktopReader
from repopilot.desktop.settings import SettingsStore, SettingsUpdate, GitHubSettings
from repopilot.desktop.comments import IssueDiscussion, CommentDraft
from repopilot.delivery.forge import ForgeError
from repopilot.storage.jobs import StateError


class Operation(BaseModel):
    revision: int = Field(ge=0)
    commit: str | None = None
    approved: bool | None = None
    reason: str = Field(default='', max_length=8000)
    request_id: str | None = None
    version: str | None = None


def create_app(root: Path, token: str, ui: Path | None = None, *, settings_store=None):
    if len(token) < 32:
        raise ValueError('desktop_session_token_too_short')
    reader = DesktopReader(root)
    settings = settings_store or SettingsStore(root)
    discussion = IssueDiscussion(reader, settings)
    app = FastAPI(docs_url=None, redoc_url=None, openapi_url=None)

    @app.middleware('http')
    async def boundary(request: Request, call_next):
        host = request.url.hostname
        origin = request.headers.get('origin')
        if host != '127.0.0.1' or (origin and origin != str(request.base_url).rstrip('/')):
            return JSONResponse({'detail': 'origin_denied'}, status_code=403)
        if request.url.path.startswith('/api/'):
            supplied = request.headers.get('authorization', '')
            if not secrets.compare_digest(supplied, 'Bearer ' + token):
                return JSONResponse({'detail': 'session_required'}, status_code=401)
        response = await call_next(request)
        response.headers['Cache-Control'] = 'no-store'
        response.headers['X-Content-Type-Options'] = 'nosniff'
        response.headers['Content-Security-Policy'] = (
            "default-src 'self'; script-src 'self'; style-src 'self' 'unsafe-inline'; "
            "img-src 'self' data:; font-src 'self'; connect-src 'self'; frame-ancestors 'none'; base-uri 'none'")
        return response

    @app.exception_handler(StateError)
    async def state_error(request, exc):
        return JSONResponse({'detail': str(exc)}, status_code=404 if str(exc) == 'job_not_found' else 409)

    @app.exception_handler(ForgeError)
    async def forge_error(request, exc):
        return JSONResponse({'detail': str(exc), 'retry_after': exc.retry_after}, status_code=502)

    @app.exception_handler(RequestValidationError)
    async def validation_error(request, exc):
        # Never echo submitted PEM, API keys or tokens in validation responses.
        return JSONResponse({'detail': 'invalid_fields', 'fields': [list(e['loc']) for e in exc.errors()]}, status_code=422)

    @app.exception_handler(ValidationError)
    async def configuration_error(request, exc):
        return JSONResponse({'detail': 'model_or_connection_configuration_invalid', 'fields': [list(e['loc']) for e in exc.errors()]}, status_code=422)

    @app.get('/api/settings')
    def get_settings():
        return settings.public()

    @app.post('/api/settings')
    def save_settings(update: SettingsUpdate):
        return settings.save(update)

    @app.post('/api/settings/check-github')
    def check_github(target: GitHubSettings):
        return settings.check_github(target.repository)

    @app.post('/api/settings/check-model')
    def check_model():
        value = settings.runtime_settings()
        return {'status': 'format_valid', 'provider': value.provider, 'model': value.model, 'inference_verified': False}

    @app.get('/api/runs/{run_id}/comments')
    def get_comments(run_id: str):
        return discussion.list(run_id)

    @app.post('/api/runs/{run_id}/comments')
    def post_comment(run_id: str, draft: CommentDraft):
        return discussion.send(run_id, draft)

    @app.get('/health')
    def health():
        return {'status': 'ok', 'protocol': 1}

    @app.get('/api/runs')
    def runs():
        return {'runs': reader.runs(), 'queue': reader.queue()}

    @app.get('/api/runs/{run_id}')
    def detail(run_id: str):
        return reader.detail(run_id)

    @app.get('/api/runs/{run_id}/events')
    async def events(request: Request, run_id: str, cursor: int = 0):
        if cursor < 0:
            raise HTTPException(422, 'invalid_cursor')
        reader.state(run_id)

        async def stream():
            offset = cursor
            while not await request.is_disconnected():
                records, offset, reset = await asyncio.to_thread(reader.events, run_id, offset)
                snapshot = await asyncio.to_thread(reader.detail, run_id)
                data = json.dumps({'events': records, 'cursor': offset, 'reset': reset,
                                   'snapshot': snapshot}, ensure_ascii=False)
                yield f'id: {offset}\nevent: update\ndata: {data}\n\n'
                await asyncio.sleep(1)
        return StreamingResponse(stream(), media_type='text/event-stream', headers={'X-Accel-Buffering': 'no'})

    @app.post('/api/runs/{run_id}/{action}')
    def operate(run_id: str, action: str, operation: Operation):
        state = reader.state(run_id)
        if operation.revision != state['_revision']:
            raise StateError('checkpoint_conflict')
        if action == 'pause':
            if state['status'] != 'running':
                raise StateError('run_not_running')
            request_pause(reader.root, run_id)
            return {'status': 'pause_requested', 'boundary': 'phase_checkpoint'}
        if action == 'decision':
            if operation.approved is None or not operation.commit or not operation.reason.strip():
                raise HTTPException(422, 'decision_fields_required')
            from repopilot.orchestration import workflow
            return workflow.decide(run_id, reader.root, commit=operation.commit,
                                   approved=operation.approved, reason=operation.reason)
        if action == 'answer':
            if not operation.request_id or not operation.version or not operation.reason.strip():
                raise HTTPException(422, 'answer_fields_required')
            from repopilot.orchestration import human
            return human.answer(run_id, reader.root, request_id=operation.request_id,
                                version=operation.version, text=operation.reason)
        raise HTTPException(404, 'operation_unavailable')

    if ui and Path(ui).is_dir():
        app.mount('/', StaticFiles(directory=ui, html=True), name='ui')
    return app

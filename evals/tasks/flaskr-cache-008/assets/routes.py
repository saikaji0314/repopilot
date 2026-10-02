"""工单 HTTP 接口；参数校验后调用业务层。"""
from flask import Blueprint, jsonify, request
from . import service

bp = Blueprint("tickets", __name__, url_prefix="/tickets")


@bp.get("")
def ticket_list():
    status = request.args.get("status")
    if status is not None and status not in {"open", "closed"}:
        return jsonify(error="invalid status"), 400
    return jsonify(items=service.list_tickets(status=status))


@bp.get("/<int:ticket_id>")
def ticket_get(ticket_id):
    ticket = service.ticket_detail(ticket_id)
    if ticket is None:
        return jsonify(error="not found"), 404
    return jsonify(ticket)


@bp.patch("/<int:ticket_id>")
def ticket_rename(ticket_id):
    payload = request.get_json(silent=True) or {}
    title = payload.get("title")
    if not isinstance(title, str) or not title:
        return jsonify(error="title required"), 400
    ticket = service.rename_ticket(ticket_id, title)
    if ticket is None:
        return jsonify(error="not found"), 404
    return jsonify(ticket)

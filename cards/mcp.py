"""Stateless MCP endpoint with separate read and approval-based add credentials.

JSON responses keep this compatible with the existing Django WSGI deployment.
No SSE stream, browser-session authentication, edit, or delete tools are provided.
"""
import hashlib
import json

from django.conf import settings
from django.db import DatabaseError
from django.http import HttpResponse, JsonResponse
from django.utils.crypto import constant_time_compare
from django.views.decorators.csrf import csrf_exempt
from django.views.decorators.debug import sensitive_variables

from .duplicates import existing_obverses, obverse_key
from .mcp_cards import ADD_TOOL, add_cards

VERSIONS = ("2025-03-26", "2025-06-18", "2025-11-25")
MAX_BYTES = 64 * 1024
MAX_PHRASES = 100
TOOL = {
    "name": "check_existing_cards",
    "title": "Check phrases in Cardy",
    "description": (
        "Check up to 100 candidate phrases against all current Cardy Obverses, including Learned cards. "
        "Matches ignore capitalization, extra whitespace, and Unicode compatibility variants. "
        "Reverse and Extra are not compared. This does not detect paraphrases or deleted cards. "
        "Returns one exists flag per submitted phrase without adding, editing, or deleting cards."
    ),
    "inputSchema": {
        "type": "object",
        "properties": {"phrases": {"type": "array", "minItems": 1, "maxItems": MAX_PHRASES,
                                   "items": {"type": "string", "minLength": 1, "maxLength": 300}}},
        "required": ["phrases"], "additionalProperties": False,
    },
    "outputSchema": {
        "type": "object",
        "properties": {
            "matches": {"type": "array", "items": {
                "type": "object", "properties": {"phrase": {"type": "string"}, "exists": {"type": "boolean"}},
                "required": ["phrase", "exists"], "additionalProperties": False,
            }},
            "includes_learned": {"type": "boolean"},
        },
        "required": ["matches", "includes_learned"], "additionalProperties": False,
    },
    "annotations": {"readOnlyHint": True, "destructiveHint": False,
                    "idempotentHint": True, "openWorldHint": False},
}


def rpc_error(request_id, code, message, status=200):
    return JsonResponse({"jsonrpc": "2.0", "id": request_id,
                         "error": {"code": code, "message": message}}, status=status)


def valid_digest(value):
    return len(value) == 64 and all(c in "0123456789abcdef" for c in value)


@sensitive_variables()
def dispatch(request):
    expected_hash = settings.CARDY_MCP_TOKEN_SHA256
    write_hash = settings.CARDY_MCP_WRITE_TOKEN_SHA256
    # A missing/malformed configuration never opens anonymous access.
    if not valid_digest(expected_hash) and not valid_digest(write_hash):
        return HttpResponse(status=404)
    scheme, _, token = request.headers.get("Authorization", "").partition(" ")
    token_hash = hashlib.sha256(token.encode("utf-8")).hexdigest()
    can_read = valid_digest(expected_hash) and constant_time_compare(token_hash, expected_hash)
    can_write = valid_digest(write_hash) and constant_time_compare(token_hash, write_hash)
    if scheme.lower() != "bearer" or not 32 <= len(token) <= 256 or not (can_read or can_write):
        response = HttpResponse(status=401)
        response["WWW-Authenticate"] = 'Bearer realm="Cardy MCP"'
        return response
    # Force Django's Host validation even when no browser Origin is present.
    host = request.get_host()
    origin = request.headers.get("Origin")
    if origin is not None and origin != f"{request.scheme}://{host}":
        return HttpResponse(status=403)
    if request.method != "POST":
        response = HttpResponse(status=405)
        response["Allow"] = "POST"
        return response
    if request.content_type != "application/json":
        return HttpResponse(status=415)
    accept = request.headers.get("Accept", "")
    accepted = {item.split(";", 1)[0].strip() for item in accept.split(",")}
    if not {"application/json", "text/event-stream"}.issubset(accepted):
        return HttpResponse(status=406)
    if request.headers.get("MCP-Protocol-Version", "2025-03-26") not in VERSIONS:
        return HttpResponse(status=400)
    raw = request.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        return HttpResponse(status=413)
    try:
        message = json.loads(raw.decode("utf-8"))
    except (ValueError, UnicodeError, RecursionError):
        return rpc_error(None, -32700, "Invalid JSON.", status=400)
    if (not isinstance(message, dict) or message.get("jsonrpc") != "2.0"
            or not isinstance(message.get("method"), str)):
        return rpc_error(None, -32600, "Invalid request.", status=400)
    request_id = message.get("id")
    if "id" in message and (isinstance(request_id, bool) or not isinstance(request_id, (str, int))):
        return rpc_error(None, -32600, "Invalid request ID.", status=400)
    params = message.get("params", {})
    if "id" not in message:
        # Notifications have no JSON-RPC response, including unknown notifications.
        return HttpResponse(status=202)
    if not isinstance(params, dict):
        return rpc_error(request_id, -32602, "Parameters must be an object.")
    method = message["method"]
    if method == "initialize":
        client = params.get("clientInfo")
        if (not isinstance(params.get("protocolVersion"), str)
                or not isinstance(params.get("capabilities"), dict)
                or not isinstance(client, dict)
                or not all(isinstance(client.get(key), str) for key in ("name", "version"))):
            return rpc_error(request_id, -32602, "Invalid initialization parameters.")
        version = params["protocolVersion"]
        result = {
            "protocolVersion": version if version in VERSIONS else VERSIONS[-1],
            "capabilities": {"tools": {"listChanged": False}},
            "serverInfo": {"name": "cardy", "version": "1.0.0"},
            "instructions": ("Check phrases with the read-only connection. Add cards only after explicit approval of "
                             "the exact cards in the current chat; never add from a scheduled run. "
                             "A missing phrase does not necessarily mean a new lesson."),
        }
    elif method == "ping":
        result = {}
    elif method == "tools/list":
        if params.get("cursor"):
            return rpc_error(request_id, -32602, "This server has no further tool pages.")
        result = {"tools": ([TOOL] if can_read else []) + ([ADD_TOOL] if can_write else [])}
    elif method == "tools/call":
        if params.get("name") == ADD_TOOL["name"]:
            if not can_write:
                return rpc_error(request_id, -32602, "This connection has read-only access.")
            try:
                data = add_cards(params.get("arguments", {}))
            except ValueError as error:
                return rpc_error(request_id, -32602, str(error))
            except DatabaseError:
                return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": {
                    "content": [{"type": "text", "text": "Cardy is unavailable. Retry the same approved batch_id; do not create a new batch."}],
                    "isError": True,
                }})
            return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": json.dumps(data)}], "structuredContent": data, "isError": False,
            }})
        if params.get("name") != TOOL["name"]:
            return rpc_error(request_id, -32602, "Unknown tool.")
        if not can_read:
            return rpc_error(request_id, -32602, "Use the read-only connection to check phrases.")
        arguments = params.get("arguments", {})
        if not isinstance(arguments, dict) or set(arguments) != {"phrases"}:
            return rpc_error(request_id, -32602, "Provide only a phrases array.")
        phrases = arguments["phrases"]
        if (not isinstance(phrases, list) or not 1 <= len(phrases) <= MAX_PHRASES
                or any(not isinstance(p, str) or not p.strip() or len(p) > 300 for p in phrases)):
            return rpc_error(request_id, -32602, "Provide 1–100 nonblank phrases, each at most 300 characters.")
        try:
            known = existing_obverses()
        except DatabaseError:
            # Never include private database details in tool responses or tracebacks.
            return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": {
                "content": [{"type": "text", "text": "Cardy is unavailable. Do not treat phrases as new; retry later."}],
                "isError": True,
            }})
        data = {"matches": [{"phrase": p, "exists": obverse_key(p) in known} for p in phrases],
                "includes_learned": True}
        result = {"content": [{"type": "text", "text": json.dumps(data, ensure_ascii=False)}],
                  "structuredContent": data, "isError": False}
    else:
        return rpc_error(request_id, -32601, "Method not found.")
    return JsonResponse({"jsonrpc": "2.0", "id": request_id, "result": result})


@csrf_exempt
@sensitive_variables()
def endpoint(request):
    # CSRF exemption applies only here: every request requires a bearer token.
    response = dispatch(request)
    response["Cache-Control"] = "no-store"
    response["Vary"] = "Authorization, Origin"
    return response

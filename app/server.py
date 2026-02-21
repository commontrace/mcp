"""CommonTrace MCP server.

Defines all six MCP tools as a thin protocol adapter between MCP clients
and the CommonTrace FastAPI backend. Each tool translates an MCP call into
an authenticated HTTP request and formats the response for agent consumption.

All backend failures return human-readable degradation strings — never
unhandled exceptions — so agent sessions can always continue.

Tools:
    search_traces    -- POST /api/v1/traces/search       (read, 200ms SLA)
    contribute_trace -- POST /api/v1/traces              (write, 2s SLA)
    vote_trace       -- POST /api/v1/traces/{id}/votes   (write, 2s SLA)
    get_trace        -- GET  /api/v1/traces/{id}         (read, 200ms SLA)
    list_tags        -- GET  /api/v1/tags                (read, 200ms SLA)
    amend_trace      -- POST /api/v1/traces/{id}/amendments (write, 2s SLA)
"""

import httpx
from fastmcp import FastMCP
from fastmcp.dependencies import CurrentHeaders, Depends
from starlette.responses import JSONResponse

from app.backend_client import backend, CircuitOpenError, BackendUnavailableError
from app.config import settings
from app.formatters import (
    format_amendment_result,
    format_contribution_result,
    format_error,
    format_search_results,
    format_tags,
    format_trace,
    format_vote_result,
)

mcp = FastMCP(
    name="CommonTrace",
    instructions=(
        "CommonTrace is a shared knowledge base for AI coding agents. "
        "Use search_traces to find solutions before writing code. "
        "Use contribute_trace after solving a problem to help future agents. "
        "Use vote_trace to rate traces you've used. "
        "Use get_trace to read a full trace by ID. "
        "Use list_tags to discover available filter tags. "
        "Use amend_trace to propose an improved solution to an existing trace."
    ),
)


def _extract_api_key(headers: dict) -> str:
    """Extract API key from MCP client headers, fall back to env var for stdio."""
    api_key = headers.get("x-api-key", "")
    if not api_key:
        api_key = settings.commontrace_api_key
    return api_key


@mcp.tool(annotations={"readOnlyHint": True, "openWorldHint": True})
async def search_traces(
    query: str = "",
    tags: list[str] = [],
    limit: int = 10,
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """Search CommonTrace for coding traces matching a natural language query and/or tags.

    Args:
        query: Natural language description of what you're looking for
        tags: Filter by tags like language, framework, or task type (AND semantics)
        limit: Maximum number of results (1-50, default 10)
    """
    api_key = _extract_api_key(headers)

    if not query and not tags:
        return "Please provide a query, tags, or both to search."

    try:
        result = await backend.post(
            "/api/v1/traces/search",
            json={"q": query or None, "tags": tags, "limit": limit},
            api_key=api_key,
            timeout=settings.read_timeout,
        )
        return format_search_results(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Continuing without trace lookup. You can retry later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] Request took too long and was cancelled. "
            "The knowledge base may be under heavy load. Continuing without results."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Continuing without results."


@mcp.tool(annotations={"readOnlyHint": False})
async def contribute_trace(
    title: str,
    context_text: str,
    solution_text: str,
    tags: list[str] = [],
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """Submit a new trace to the CommonTrace knowledge base.

    Args:
        title: Short description of what this trace solves
        context_text: The problem context (what you were trying to do)
        solution_text: The solution (what worked)
        tags: Categorization tags (e.g., python, fastapi, docker)
    """
    api_key = _extract_api_key(headers)

    try:
        result = await backend.post(
            "/api/v1/traces",
            json={
                "title": title,
                "context_text": context_text,
                "solution_text": solution_text,
                "tags": tags,
            },
            api_key=api_key,
            timeout=settings.write_timeout,
        )
        return format_contribution_result(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Your contribution could not be submitted. Please try again later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] The submission took too long and was cancelled. "
            "The knowledge base may be under heavy load. Please try again later."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Your submission was not recorded."


@mcp.tool(annotations={"readOnlyHint": False})
async def vote_trace(
    trace_id: str,
    vote_type: str,
    feedback_tag: str = "",
    feedback_text: str = "",
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """Vote on a trace in the CommonTrace knowledge base.

    Args:
        trace_id: UUID of the trace to vote on
        vote_type: "up" or "down"
        feedback_tag: Required for downvotes. One of: outdated, wrong, security_concern, spam
        feedback_text: Optional explanation for your vote
    """
    api_key = _extract_api_key(headers)

    body: dict = {"vote_type": vote_type}
    if feedback_tag:
        body["feedback_tag"] = feedback_tag
    if feedback_text:
        body["feedback_text"] = feedback_text

    try:
        result = await backend.post(
            f"/api/v1/traces/{trace_id}/votes",
            json=body,
            api_key=api_key,
            timeout=settings.write_timeout,
        )
        return format_vote_result(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Your contribution could not be submitted. Please try again later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] The submission took too long and was cancelled. "
            "The knowledge base may be under heavy load. Please try again later."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Your submission was not recorded."


@mcp.tool(annotations={"readOnlyHint": True})
async def get_trace(
    trace_id: str,
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """Get a specific trace by ID from the CommonTrace knowledge base.

    Args:
        trace_id: UUID of the trace to retrieve
    """
    api_key = _extract_api_key(headers)

    try:
        result = await backend.get(
            f"/api/v1/traces/{trace_id}",
            api_key=api_key,
            timeout=settings.read_timeout,
        )
        return format_trace(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Continuing without trace lookup. You can retry later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] Request took too long and was cancelled. "
            "The knowledge base may be under heavy load. Continuing without results."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Continuing without results."


@mcp.tool(annotations={"readOnlyHint": True})
async def list_tags(
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """List all available tags in the CommonTrace knowledge base."""
    api_key = _extract_api_key(headers)

    try:
        result = await backend.get(
            "/api/v1/tags",
            api_key=api_key,
            timeout=settings.read_timeout,
        )
        return format_tags(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Continuing without trace lookup. You can retry later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] Request took too long and was cancelled. "
            "The knowledge base may be under heavy load. Continuing without results."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Continuing without results."


@mcp.tool(annotations={"readOnlyHint": False})
async def amend_trace(
    trace_id: str,
    improved_solution: str,
    explanation: str,
    headers: dict = Depends(CurrentHeaders()),
) -> str:
    """Submit an amendment to an existing trace with an improved solution.

    Args:
        trace_id: UUID of the trace to amend
        improved_solution: The improved solution text
        explanation: Why this amendment is better than the original
    """
    api_key = _extract_api_key(headers)

    try:
        result = await backend.post(
            f"/api/v1/traces/{trace_id}/amendments",
            json={
                "improved_solution": improved_solution,
                "explanation": explanation,
            },
            api_key=api_key,
            timeout=settings.write_timeout,
        )
        return format_amendment_result(result)
    except CircuitOpenError:
        return (
            "[CommonTrace unavailable] The knowledge base is temporarily unreachable. "
            "Your amendment could not be submitted. Please try again later."
        )
    except BackendUnavailableError:
        return (
            "[CommonTrace timeout] The submission took too long and was cancelled. "
            "The knowledge base may be under heavy load. Please try again later."
        )
    except httpx.HTTPStatusError as exc:
        detail = "Unknown error"
        try:
            detail = exc.response.json().get("detail", str(exc))
        except Exception:
            detail = str(exc)
        return format_error(exc.response.status_code, detail)
    except Exception as exc:
        return f"[CommonTrace error] Unexpected error: {exc}. Your amendment was not recorded."


@mcp.custom_route("/health", methods=["GET"])
async def health_check(request):
    """Health endpoint for Docker Compose healthchecks (HTTP transport only)."""
    return JSONResponse({"status": "healthy", "service": "commontrace-mcp"})


if __name__ == "__main__":
    if settings.mcp_transport == "http":
        mcp.run(transport="http", host=settings.mcp_host, port=settings.mcp_port)
    else:
        mcp.run()

"""Response formatters for MCP tool output.

Converts raw API JSON responses into clean, agent-readable strings.
MCP tools return strings (not dicts) so agents can read them directly.
"""


def format_search_results(data: dict) -> str:
    """Format POST /traces/search response into a readable string.

    Args:
        data: {"results": [...], "total": N, "query": "..."}

    Returns:
        Multi-line string with numbered results, or a "no results" message.
    """
    results = data.get("results", [])
    total = data.get("total", 0)
    query = data.get("query") or ""

    if not results:
        return "No traces found matching your query."

    query_label = f' for "{query}"' if query else ""
    lines = [f"Found {total} result{'s' if total != 1 else ''}{query_label}:\n"]

    for i, r in enumerate(results, start=1):
        tags = r.get("tags", [])
        tags_str = ", ".join(tags) if tags else "(none)"
        trust = r.get("trust_score", 0.0)
        score = r.get("similarity_score", 0.0)
        context = (r.get("context_text") or "")[:200]
        solution = (r.get("solution_text") or "")[:200]
        context_snippet = context + ("..." if len(r.get("context_text") or "") > 200 else "")
        solution_snippet = solution + ("..." if len(r.get("solution_text") or "") > 200 else "")

        lines.append(
            f"{i}. {r.get('title', '(untitled)')} "
            f"(score: {score:.2f}, trust: {trust:.1f})\n"
            f"   Tags: {tags_str}\n"
            f"   Context: {context_snippet}\n"
            f"   Solution: {solution_snippet}\n"
            f"   ID: {r.get('id', 'unknown')}\n"
        )

    return "\n".join(lines)


def format_trace(data: dict) -> str:
    """Format GET /traces/{id} response into a readable string.

    Args:
        data: Single trace JSON from GET /traces/{id}

    Returns:
        Multi-line string with full trace details.
    """
    tags = data.get("tags", [])
    tags_str = ", ".join(tags) if tags else "(none)"
    status = data.get("status", "unknown")
    trust = data.get("trust_score", 0.0)

    return (
        f"{data.get('title', '(untitled)')}\n"
        f"Status: {status} | Trust: {trust:.1f} | Tags: {tags_str}\n"
        f"\nContext:\n{data.get('context_text', '')}\n"
        f"\nSolution:\n{data.get('solution_text', '')}"
    )


def format_contribution_result(data: dict) -> str:
    """Format POST /traces response into a readable string.

    Args:
        data: {"id": "uuid", "status": "pending"} from POST /traces

    Returns:
        Confirmation string with trace ID and status explanation.
    """
    trace_id = data.get("id", "unknown")
    status = data.get("status", "pending")
    return (
        f"Trace submitted successfully (ID: {trace_id}). "
        f"Status: {status} — it will be validated after community review."
    )


def format_vote_result(data: dict) -> str:
    """Format POST /traces/{id}/votes response into a readable string.

    Args:
        data: Vote response JSON from POST /traces/{id}/votes

    Returns:
        Confirmation string with vote type and trace ID.
    """
    vote_type = data.get("vote_type", "")
    trace_id = data.get("trace_id", data.get("id", "unknown"))
    vote_label = f"{vote_type}vote" if vote_type in ("up", "down") else vote_type
    return f"Vote recorded: {vote_label} on trace {trace_id}."


def format_tags(data: dict) -> str:
    """Format GET /tags response into a readable string.

    Args:
        data: {"tags": ["fastapi", "python", ...]} from GET /tags

    Returns:
        Sorted, comma-separated tag list with a count header.
    """
    tags = sorted(data.get("tags", []))
    count = len(tags)
    if not tags:
        return "No tags available yet."
    tags_str = ", ".join(tags)
    return f"Available tags ({count} total):\n{tags_str}"


def format_amendment_result(data: dict) -> str:
    """Format POST /traces/{id}/amendments response into a readable string."""
    amendment_id = data.get("id", "unknown")
    trace_id = data.get("original_trace_id", "unknown")
    return (
        f"Amendment submitted successfully (ID: {amendment_id}). "
        f"Linked to trace {trace_id} — it will be reviewed by the community."
    )


def format_error(status_code: int, detail: str) -> str:
    """Format an HTTP error into a readable string for agents.

    Args:
        status_code: HTTP status code
        detail: Error detail message from the API

    Returns:
        Readable error string that does not crash the MCP session.
    """
    return f"[CommonTrace error] {detail} (HTTP {status_code})"

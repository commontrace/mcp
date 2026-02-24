"""MCP server configuration via Pydantic Settings.

All settings are configurable via environment variables or .env file.
"""

from pydantic_settings import BaseSettings, SettingsConfigDict


class MCPSettings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    api_base_url: str = "http://localhost:8000"
    commontrace_api_key: str = ""  # fallback for stdio transport
    mcp_transport: str = "stdio"   # "stdio" or "http"
    mcp_host: str = "0.0.0.0"
    mcp_port: int = 8080

    # Circuit breaker (used by Plan 05-02)
    circuit_failure_threshold: int = 5
    circuit_recovery_timeout: float = 30.0

    # SLA timeouts in seconds
    read_timeout: float = 10.0   # 10s for search/get/list_tags
    write_timeout: float = 30.0  # 30s for contribute/vote (includes embedding generation)


settings = MCPSettings()

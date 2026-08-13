from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=("../.env", ".env"),  # works from both repo root and backend/
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # api
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # default provider for both tiers; must be registered in app.llm.registry
    # API providers remain supported; CLI providers use saved local CLI auth.
    llm_provider: str = "openai"

    # per-tier overrides, empty falls back to llm_provider
    llm_strong_provider: str = ""
    llm_fast_provider: str = ""

    # model names, must be valid for the chosen provider
    # strong tier: reasoning-critical tasks (repair, semantic validation)
    llm_strong_model: str = "gpt-5.6-sol"
    # fast tier: mechanical transformations (format conversion, simple checks)
    llm_fast_model: str = "gpt-5.6-luna"

    # provider credentials, real values come from .env
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    gemini_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # local harnesses; registration only occurs when the executable is available
    codex_cli_path: str = "codex"
    claude_cli_path: str = "claude"
    llm_cli_timeout_seconds: float = Field(default=300.0, gt=0)

    # diagram converter — must match a name registered in app.model.registry
    diagram_converter: str = "pydantic"

    # workspace — where session snapshots are stored
    workspace_dir: str = "workspaces"

    # behaviour
    max_repair_iterations: int = 3


settings = Settings()

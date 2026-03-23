from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # api
    api_prefix: str = "/api/v1"
    cors_origins: list[str] = ["http://localhost:5173", "http://localhost:3000"]

    # llm routing
    # "strong" model for reasoning-critical tasks (repair, semantic validation)
    llm_strong_model: str = "claude-opus-4-6"
    # "fast" model for mechanical transformations (format conversion, simple checks)
    llm_fast_model: str = "claude-haiku-4-5-20251001"
    anthropic_api_key: str = ""

    # diagram converter — must match a name registered in app.model.registry
    diagram_converter: str = "pydantic"

    # behaviour
    max_repair_iterations: int = 3


settings = Settings()

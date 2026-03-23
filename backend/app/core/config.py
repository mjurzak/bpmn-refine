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

    # llm provider — must match a name registered in app.llm.registry
    # options: anthropic | openai | ollama
    llm_provider: str = "anthropic"

    # model names — set to models supported by the chosen provider
    # "strong" model for reasoning-critical tasks (repair, semantic validation)
    llm_strong_model: str = "claude-opus-4-6"
    # "fast" model for mechanical transformations (format conversion, simple checks)
    llm_fast_model: str = "claude-haiku-4-5-20251001"

    # provider credentials / endpoints
    anthropic_api_key: str = ""
    openai_api_key: str = ""
    ollama_base_url: str = "http://localhost:11434"

    # diagram converter — must match a name registered in app.model.registry
    diagram_converter: str = "pydantic"

    # behaviour
    max_repair_iterations: int = 3


settings = Settings()

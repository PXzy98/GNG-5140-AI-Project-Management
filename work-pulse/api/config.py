from pydantic_settings import BaseSettings
from pydantic import Field


class Settings(BaseSettings):
    openrouter_api_key: str = Field(default="", alias="OPENROUTER_API_KEY")
    database_url: str = Field(
        default="postgresql+asyncpg://workpulse:workpulse@localhost:5432/workpulse",
        alias="DATABASE_URL",
    )
    redis_url: str = Field(default="redis://localhost:6379/0", alias="REDIS_URL")
    log_level: str = Field(default="INFO", alias="LOG_LEVEL")
    use_mock_db: bool = Field(default=True, alias="USE_MOCK_DB")
    # Local Ollama instance
    ollama_base_url: str = Field(default="http://192.168.2.25:11434", alias="OLLAMA_BASE_URL")
    ollama_model: str = Field(default="qwen3.5:35b-a3b", alias="OLLAMA_MODEL")

    model_config = {"env_file": ".env", "populate_by_name": True}


settings = Settings()

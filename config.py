from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    gemini_api_key: str
    gemini_model: str
    gemini_rpm_limit: int
    gemini_timeout_s: int
    gemini_max_retries: int
    debug: bool = False

    model_config = SettingsConfigDict(
        env_file=".env.development",
        env_file_encoding="utf-8",
    )


settings = Settings()
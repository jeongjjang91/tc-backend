from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LLM
    llm_api_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-oss"

    # Oracle App DB
    app_db_dsn: str = "localhost:1521/APPDB"
    app_db_user: str = "voc_app"
    app_db_password: str = ""

    # Oracle TC DB (read-only)
    tc_db_dsn: str = "localhost:1521/TCDB"
    tc_db_user: str = "voc_readonly"
    tc_db_password: str = ""

    # 설정
    config_dir: str = "config"
    confidence_threshold: float = 0.7
    log_level: str = "INFO"


_settings: Settings | None = None


def get_settings() -> Settings:
    global _settings
    if _settings is None:
        _settings = Settings()
    return _settings

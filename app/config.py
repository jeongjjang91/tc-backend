from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_file_encoding="utf-8")

    # LLM
    llm_api_base_url: str = "http://localhost:8080/v1"
    llm_api_key: str = ""
    llm_model: str = "gpt-oss"

    # MySQL App DB (세션/메시지/feedback/few_shot)
    app_db_host: str = "localhost"
    app_db_port: int = 3306
    app_db_name: str = "APPDB"
    app_db_user: str = "voc_app"
    app_db_password: str = ""

    # MySQL TC DB (read-only, Text-to-SQL 대상)
    tc_db_host: str = "localhost"
    tc_db_port: int = 3307
    tc_db_name: str = "TCDB"
    tc_db_user: str = "voc_readonly"
    tc_db_password: str = ""

    # Confluence (Phase 2)
    confluence_base_url: str = "http://localhost/confluence"
    confluence_token: str = ""
    confluence_space_key: str = "TC"

    # Splunk (Phase 3)
    splunk_host: str = "localhost"
    splunk_port: int = 8089
    splunk_token: str = ""
    splunk_index: str = "main"

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

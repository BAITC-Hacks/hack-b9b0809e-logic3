from typing import Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    ekt_mode: Literal['demo', 'live'] = 'demo'
    ekt_api_base: str = 'https://ekt.kz/api'
    ekt_api_user: str = 'apiuser'
    ekt_api_password: SecretStr = SecretStr('')
    catalog_max_pages: int = Field(5000, ge=1, le=50000)
    catalog_ttl_seconds: int = Field(86400, ge=1)
    catalog_cache_path: str = 'data/catalog-cache.json'
    catalog_concurrency: int = Field(4, ge=1, le=8)
    catalog_page_size: int = Field(100, ge=1, le=100)
    ekt_api_timeout_seconds: int = Field(30, ge=1, le=120)
    detail_ttl_seconds: int = Field(60, ge=1)
    openai_api_key: SecretStr = SecretStr('')
    openai_model: str = 'gpt-4.1-mini'
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(3600, ge=60)
    auth_db_path: str = 'data/accounts.sqlite3'
    auth_ttl_seconds: int = Field(604800, ge=60, le=2592000)

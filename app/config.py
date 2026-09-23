from typing import Literal
from pydantic import Field, SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file='.env', extra='ignore')
    ekt_mode: Literal['demo', 'live'] = 'demo'
    ekt_api_base: str = 'https://ekt.kz/api'
    ekt_api_user: str = 'apiuser'
    ekt_api_password: SecretStr = SecretStr('')
    catalog_max_pages: int = Field(5, ge=1, le=5000)
    catalog_ttl_seconds: int = Field(900, ge=1)
    detail_ttl_seconds: int = Field(60, ge=1)
    openai_api_key: SecretStr = SecretStr('')
    openai_model: str = 'gpt-4.1-mini'
    cookie_secure: bool = False
    session_ttl_seconds: int = Field(3600, ge=60)

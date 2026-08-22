from functools import lru_cache
from pathlib import Path

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="CODEATLAS_", extra="ignore")

    app_name: str = "CodeAtlas"
    env: str = "development"
    log_level: str = "INFO"
    api_prefix: str = "/api/v1"
    postgres_dsn: str = "postgresql://codeatlas:codeatlas@localhost:5432/codeatlas"
    redis_url: str = "redis://localhost:6379/0"
    qdrant_url: str = "http://localhost:6333"
    reranker_url: str | None = None
    embedding_backend: str = "hash"
    embedding_model: str = "BAAI/bge-small-zh-v1.5"
    embedding_dimensions: int = 512
    reranker_backend: str = "feature"
    reranker_model: str = "BAAI/bge-reranker-base"
    github_token: str | None = None
    github_api_url: str = "https://api.github.com"
    neo4j_uri: str = "bolt://localhost:7687"
    neo4j_user: str = "neo4j"
    neo4j_password: str = "codeatlas-password"
    otlp_endpoint: str | None = None
    repository_root: Path = Field(default_factory=lambda: Path.cwd())
    enable_external_services: bool = False
    write_confirmation_secret: str = "change-me"
    retrieval_limit: int = 8
    graph_max_hops: int = 2


@lru_cache
def get_settings() -> Settings:
    return Settings()

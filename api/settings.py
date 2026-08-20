"""Configuração da API.

Nenhum segredo tem valor default. O token do GitHub só existe como
`SecretStr`, nunca aparece em log, resposta ou repr, e é opcional: sem ele a
aplicação funciona inteira, só não empurra o YAML para o repositório.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import Field, SecretStr, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", env_prefix="FATURA_", extra="ignore")

    # --- dados locais -------------------------------------------------------
    rules_path: Path = Path("/data/categories.yml")
    db_path: Path = Path("/data/state.db")
    transaction_ttl_hours: int = 24
    max_upload_bytes: int = 10 * 1024 * 1024
    max_files_per_upload: int = 24

    # --- GitHub -------------------------------------------------------------
    # Token fine-grained com Contents: Read and write NESTE repositório apenas.
    # Injetado por Docker secret ou .env fora da imagem; nunca no Dockerfile.
    github_token: SecretStr | None = None
    github_repo: str = "leoscastilho/parser-de-fatura-multibancos"
    github_branch: str = "main"
    github_file_path: str = "categories.yml"
    github_author_name: str = "fatura-bot"
    github_author_email: str = "fatura-bot@users.noreply.github.com"

    cors_origins: list[str] = Field(default_factory=lambda: ["http://localhost:5173"])

    @field_validator("github_token", mode="before")
    @classmethod
    def _token_vazio_e_ausente(cls, value):
        """`FATURA_GITHUB_TOKEN=` (vazio) significa SEM GitHub, não token vazio.

        O docker-compose passa `${FATURA_GITHUB_TOKEN:-}`, então rodar sem `.env`
        entrega string vazia — que não é None e fazia `github_enabled` dizer que
        sim. O PyGithub então estourava num `assert len(token) > 0`, e o download
        do CSV virava 500.
        """
        if value is None:
            return None
        raw = value.get_secret_value() if isinstance(value, SecretStr) else str(value)
        return raw.strip() or None

    @property
    def github_enabled(self) -> bool:
        if self.github_token is None:
            return False
        return bool(self.github_token.get_secret_value().strip())


@lru_cache
def get_settings() -> Settings:
    return Settings()

"""Runtime settings, loaded from the environment and `.env`."""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

from pydantic import Field, field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]+")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    sec_user_agent: str
    data_dir: Path = Path("./data")
    sec_max_requests_per_second: float = Field(default=8.0, gt=0, le=10)

    @field_validator("sec_user_agent")
    @classmethod
    def _require_contact_email(cls, value: str) -> str:
        # SEC blocks undeclared agents; a name plus email is the documented format.
        if not _EMAIL.search(value):
            raise ValueError(
                "SEC_USER_AGENT must include a contact email, e.g. 'Jane Doe jane@x.com'"
            )
        return value


@lru_cache
def get_settings() -> Settings:
    return Settings()  # type: ignore[call-arg]

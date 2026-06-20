from __future__ import annotations

from pydantic import BaseModel, Field


class ModelSettingsUpdate(BaseModel):
    provider: str = Field(default="mock")
    model: str = Field(default="mock-fast")
    api_key: str | None = Field(default=None)


class CustomProviderCreate(BaseModel):
    display_name: str
    base_url: str
    model: str
    api_key_label: str = "API Key"


class ConnectorCreate(BaseModel):
    name: str = "Mock Connector"
    platform: str = "mock"
    connector_type: str = "mock"
    auth_method: str = "none"
    max_scan_pages: int = 20


class PassiveLinkCreate(BaseModel):
    url: str
    title: str | None = None
    platform: str | None = None


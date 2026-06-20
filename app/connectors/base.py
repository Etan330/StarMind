from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class ConnectorItem:
    raw_url: str
    title: str
    platform: str
    author: str | None = None
    content_type: str = "link"
    metadata: dict[str, Any] = field(default_factory=dict)


class BaseConnector:
    platform: str

    async def login_check(self) -> bool:
        raise NotImplementedError

    async def fetch_favorites_page(self, page_cursor=None):
        raise NotImplementedError

    async def parse_items(self, page_html_or_json):
        raise NotImplementedError

    async def scan_until_boundary(self, connector_state: dict[str, Any]) -> list[ConnectorItem]:
        raise NotImplementedError


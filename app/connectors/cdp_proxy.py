from __future__ import annotations

import asyncio
import json
import subprocess
import os
from dataclasses import dataclass
from typing import Any

import httpx


class CDPConnectionError(RuntimeError):
    pass


@dataclass
class CDPTab:
    tab_id: str
    url: str = ""
    title: str = ""


# web-access skill's cdp-proxy.mjs location
SKILL_DIR = os.path.expanduser("~/.claude/skills/web-access")
CDP_PROXY_SCRIPT = os.path.join(SKILL_DIR, "scripts", "cdp-proxy.mjs")
CHECK_DEPS_SCRIPT = os.path.join(SKILL_DIR, "scripts", "check-deps.mjs")

PROXY_BASE = "http://localhost:3456"


class CDPProxy:
    """Wrapper around web-access skill's cdp-proxy.mjs (HTTP API on port 3456).

    This replaces direct WebSocket CDP manipulation with calls to the
    skill's managed proxy, which handles browser discovery, tab lifecycle,
    idle cleanup, and connection pinning.
    """

    def __init__(self, base_url: str = PROXY_BASE) -> None:
        self._base = base_url.rstrip("/")
        self._proxy_process: subprocess.Popen | None = None

    async def check_status(self) -> dict[str, Any]:
        """Check if cdp-proxy is running and connected to a browser."""
        try:
            async with httpx.AsyncClient(timeout=3) as client:
                resp = await client.get(f"{self._base}/health")
                if resp.status_code == 200:
                    data = resp.json()
                    return {"connected": True, "browser": data.get("browser", ""), "tabs": data.get("tabs", 0)}
        except Exception:
            pass
        return {
            "connected": False,
            "browser": "",
            "hint": (
                "CDP Proxy 未运行。请确保：\n"
                "1. 浏览器已开启远程调试（地址栏访问 chrome://inspect/#remote-debugging 勾选允许）\n"
                "2. 运行: node ~/.claude/skills/web-access/scripts/check-deps.mjs"
            ),
        }

    async def connect(self) -> bool:
        """Ensure proxy is running. Start it if not."""
        status = await self.check_status()
        if status["connected"]:
            return True

        # Try to start the proxy
        if os.path.exists(CDP_PROXY_SCRIPT):
            await self._start_proxy()
            # Wait for proxy to become available
            for _ in range(10):
                await asyncio.sleep(1)
                status = await self.check_status()
                if status["connected"]:
                    return True

        raise CDPConnectionError(status.get("hint", "无法连接到 CDP Proxy"))

    async def _start_proxy(self) -> None:
        """Start cdp-proxy.mjs in background."""
        if self._proxy_process and self._proxy_process.poll() is None:
            return
        env = {**os.environ, "CLAUDE_SKILL_DIR": SKILL_DIR}
        self._proxy_process = subprocess.Popen(
            ["node", CDP_PROXY_SCRIPT],
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
        )

    async def new_tab(self, url: str) -> CDPTab:
        """Open a new background tab via POST /new."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self._base}/new", content=url)
            if resp.status_code != 200:
                raise CDPConnectionError(f"Failed to open tab: {resp.text}")
            data = resp.json()
        return CDPTab(tab_id=data["targetId"], url=url)

    async def eval_script(self, tab: CDPTab, expression: str) -> Any:
        """Execute JS in a tab via POST /eval."""
        async with httpx.AsyncClient(timeout=30) as client:
            resp = await client.post(f"{self._base}/eval?target={tab.tab_id}", content=expression)
            if resp.status_code != 200:
                raise CDPConnectionError(f"eval failed: {resp.text}")
            data = resp.json()
        if data.get("error"):
            raise CDPConnectionError(f"JS error: {data['error']}")
        return data.get("value", data.get("result"))

    async def click(self, tab: CDPTab, selector: str) -> None:
        """Click an element via POST /click."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self._base}/click?target={tab.tab_id}", content=selector)
            if resp.status_code != 200:
                raise CDPConnectionError(f"click failed: {resp.text}")

    async def click_at(self, tab: CDPTab, x: float, y: float) -> None:
        """Click viewport coordinates via POST /clickXY."""
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(
                f"{self._base}/clickXY?target={tab.tab_id}",
                json={"x": x, "y": y},
            )
            if resp.status_code != 200:
                raise CDPConnectionError(f"click_at failed: {resp.text}")

    async def key(
        self,
        tab: CDPTab,
        key: str,
        code: str | None = None,
        windows_virtual_key_code: int | None = None,
        modifiers: int = 0,
    ) -> None:
        """Dispatch a browser-level keyboard event via POST /key."""
        payload: dict[str, Any] = {
            "key": key,
            "code": code or key,
            "modifiers": modifiers,
        }
        if windows_virtual_key_code is not None:
            payload["windowsVirtualKeyCode"] = windows_virtual_key_code
        async with httpx.AsyncClient(timeout=10) as client:
            resp = await client.post(f"{self._base}/key?target={tab.tab_id}", json=payload)
            if resp.status_code != 200:
                raise CDPConnectionError(f"key failed: {resp.text}")

    async def scroll(self, tab: CDPTab, distance: int = 800) -> None:
        """Scroll via GET /scroll."""
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(f"{self._base}/scroll?target={tab.tab_id}&y={distance}")
        await asyncio.sleep(0.8)

    async def scroll_to_bottom(self, tab: CDPTab) -> None:
        """Scroll to bottom via GET /scroll?direction=bottom."""
        async with httpx.AsyncClient(timeout=10) as client:
            await client.get(f"{self._base}/scroll?target={tab.tab_id}&direction=bottom")
        await asyncio.sleep(0.8)

    async def navigate(self, tab: CDPTab, url: str) -> None:
        """Navigate existing tab via POST /navigate."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.post(f"{self._base}/navigate?target={tab.tab_id}", content=url)
            if resp.status_code != 200:
                raise CDPConnectionError(f"navigate failed: {resp.text}")

    async def wait_for_load(self, tab: CDPTab, timeout: float = 10) -> None:
        """Wait until page is loaded (poll /info for readyState)."""
        end = asyncio.get_event_loop().time() + timeout
        while asyncio.get_event_loop().time() < end:
            try:
                async with httpx.AsyncClient(timeout=3) as client:
                    resp = await client.get(f"{self._base}/info?target={tab.tab_id}")
                    if resp.status_code == 200:
                        info = resp.json()
                        if info.get("readyState") in ("complete", "interactive"):
                            return
            except Exception:
                pass
            await asyncio.sleep(0.5)

    async def get_info(self, tab: CDPTab) -> dict[str, Any]:
        """Get tab info (title, url, readyState)."""
        async with httpx.AsyncClient(timeout=5) as client:
            resp = await client.get(f"{self._base}/info?target={tab.tab_id}")
            return resp.json() if resp.status_code == 200 else {}

    async def screenshot(self, tab: CDPTab, file_path: str) -> str:
        """Take screenshot via GET /screenshot."""
        async with httpx.AsyncClient(timeout=15) as client:
            resp = await client.get(f"{self._base}/screenshot?target={tab.tab_id}&file={file_path}")
            if resp.status_code != 200:
                raise CDPConnectionError(f"screenshot failed: {resp.text}")
        return file_path

    async def get_cookies(self, tab: CDPTab, domain: str | None = None) -> list[dict[str, Any]]:
        """Get cookies by evaluating document.cookie in the tab."""
        raw = await self.eval_script(tab, "document.cookie")
        if not raw:
            return []
        cookies = []
        for pair in str(raw).split(";"):
            pair = pair.strip()
            if "=" in pair:
                name, value = pair.split("=", 1)
                cookies.append({"name": name.strip(), "value": value.strip(), "domain": domain or ""})
        return cookies

    async def close_tab(self, tab: CDPTab) -> None:
        """Close tab via GET /close."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                await client.get(f"{self._base}/close?target={tab.tab_id}")
        except Exception:
            pass

    async def list_targets(self) -> list[dict[str, Any]]:
        """List all open tabs via GET /targets."""
        try:
            async with httpx.AsyncClient(timeout=5) as client:
                resp = await client.get(f"{self._base}/targets")
                return resp.json() if resp.status_code == 200 else []
        except Exception:
            return []

    async def close_all(self) -> None:
        """Close all managed tabs."""
        targets = await self.list_targets()
        for t in targets:
            tid = t.get("id") or t.get("targetId")
            if tid:
                await self.close_tab(CDPTab(tab_id=tid))


cdp_proxy = CDPProxy()

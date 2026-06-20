from __future__ import annotations


class GuardrailViolation(ValueError):
    pass


class AgentGuardrails:
    blocked_terms = [
        "删除 local_data",
        "删除原始资料",
        "导出 api key",
        "显示 api key",
        "读取 cookie",
        "泄露 cookie",
    ]

    def validate_user_input(self, text: str) -> None:
        normalized = text.lower()
        if len(text) > 8000:
            raise GuardrailViolation("问题太长，请先拆成更小的问题。")
        for term in self.blocked_terms:
            if term.lower() in normalized:
                raise GuardrailViolation("这个请求会触碰本地资料或密钥安全边界，StarMind 不会执行。")

    def validate_tool_name(self, tool_name: str, allowed_tools: set[str]) -> None:
        if tool_name not in allowed_tools:
            raise GuardrailViolation(f"工具未授权：{tool_name}")

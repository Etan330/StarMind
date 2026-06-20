from __future__ import annotations

from uuid import uuid4

from sqlalchemy.orm import Session

from app.agent.guardrails import AgentGuardrails, GuardrailViolation
from app.agent.instructions import QUERY_INSTRUCTIONS, SYSTEM_INSTRUCTIONS
from app.agent.memory import AgentMemory
from app.agent.observability import AgentTracer
from app.agent.tools import KnowledgeSearchTool
from app.api_models import AgentAnswer
from app.llm import get_provider_runtime


class AgentRunner:
    def __init__(self, db: Session) -> None:
        self.db = db
        self.guardrails = AgentGuardrails()
        self.memory = AgentMemory()
        self.tracer = AgentTracer()

    async def answer_question(
        self,
        question: str,
        provider_id: str | None = None,
        model: str | None = None,
        model_profile_name: str | None = None,
    ) -> AgentAnswer:
        run_id = f"run-{uuid4().hex}"
        try:
            self.guardrails.validate_user_input(question)
        except GuardrailViolation as exc:
            answer = str(exc)
            self.tracer.log("guardrail_block", {"run_id": run_id, "question": question, "reason": answer})
            return AgentAnswer(run_id=run_id, answer=answer, sources=[], model=model or "", provider=provider_id or "")

        search = KnowledgeSearchTool(self.db).run(question)
        provider, resolved_model, provider_config = get_provider_runtime(provider_id, model)
        resolved_provider = provider.provider_name
        self.tracer.log(
            "agent_start",
            {
                "run_id": run_id,
                "provider": resolved_provider,
                "model": resolved_model,
                "profile": model_profile_name,
                "tool_results": [search.metadata],
            },
        )

        if provider_config.get("api_style") != "mock" and not provider_config.get("base_url"):
            answer = "当前模型供应商还没有配置 Base URL。请先到设置页补全模型接口。"
        else:
            try:
                prompt = f"{QUERY_INSTRUCTIONS}\n\n本地资料：\n{search.content}\n\n用户问题：{question}"
                answer = await provider.chat(
                    [
                        {"role": "system", "content": SYSTEM_INSTRUCTIONS},
                        {"role": "user", "content": prompt},
                    ],
                    model=resolved_model,
                    temperature=0.2,
                )
            except Exception as exc:
                answer = (
                    "StarMind 已经完成 Agent 框架调用，但模型没有成功返回。"
                    "请确认 DeepSeek API Key 已保存且网络可访问。"
                    f"\n\n错误：{type(exc).__name__}: {exc}"
                )

        self.memory.remember_run(run_id, question, answer)
        self.tracer.log("agent_finish", {"run_id": run_id, "answer_preview": answer[:500]})
        sources = search.metadata.get("items", [])
        return AgentAnswer(
            run_id=run_id,
            answer=answer,
            sources=sources,
            model=resolved_model,
            provider=resolved_provider,
            profile=model_profile_name,
        )

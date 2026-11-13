"""可选 Langfuse 追踪：请求独立回调，共享 SDK 客户端，不参与业务决策。"""

import asyncio
from contextlib import contextmanager
from dataclasses import dataclass
import logging
from typing import Any, Iterator

from app.config import get_settings
from app.logging_config import _sanitize, log_event

logger = logging.getLogger(__name__)
_client: Any = None


def initialize_tracing() -> None:
    """只在显式启用时加载 SDK；缺失凭证属于配置错误，不静默忽略。"""
    global _client
    settings = get_settings()
    if not settings.langfuse_enabled or _client is not None:
        return
    if not settings.langfuse_public_key.strip() or not settings.langfuse_secret_key.strip():
        raise RuntimeError("启用 Langfuse 需要 LANGFUSE_PUBLIC_KEY 和 LANGFUSE_SECRET_KEY")

    from langfuse import Langfuse
    from langfuse.types import MaskOtelSpansResult, OtelSpanPatch

    secrets = tuple(value for value in (
        settings.langfuse_secret_key, settings.langfuse_public_key,
        settings.deepseek_api_key, settings.zhipu_api_key,
    ) if value)

    def mask(*, data: Any) -> Any:
        def redact(value: Any) -> Any:
            if isinstance(value, str):
                for secret in secrets:
                    value = value.replace(secret, "***")
            elif isinstance(value, dict):
                return {key: redact(item) for key, item in value.items()}
            elif isinstance(value, list):
                return [redact(item) for item in value]
            return value
        return redact(_sanitize(data))

    def mask_spans(*, params):
        # dubonline：先让调用结构与用量可见，正文采集单独授权，避免默认复制整份证据。
        fields = () if settings.langfuse_capture_content else (
            "langfuse.observation.input", "langfuse.observation.output",
            "langfuse.observation.status_message",
        )
        patches = {}
        for key, span in params.spans.items():
            replacements = {}
            for name, value in span.attributes.items():
                if name not in fields and isinstance(value, str):
                    masked = value
                    for secret in secrets:
                        masked = masked.replace(secret, "***")
                    if masked != value:
                        replacements[name] = masked
            patches[key] = OtelSpanPatch(delete_attributes=fields, set_attributes=replacements)
        return MaskOtelSpansResult(span_patches=patches)

    _client = Langfuse(
        public_key=settings.langfuse_public_key,
        secret_key=settings.langfuse_secret_key,
        base_url=settings.langfuse_host,
        environment=settings.langfuse_environment,
        release=settings.langfuse_release,
        timeout=5,
        tracing_enabled=True,
        mask=mask,
        mask_otel_spans=mask_spans,
    )
    log_event(logger, "langfuse_initialized")


async def shutdown_tracing() -> None:
    """后台批量导出在退出时尽力清空；同步 SDK I/O 不占用事件循环。"""
    global _client
    client, _client = _client, None
    if client is not None:
        try:
            await asyncio.to_thread(client.shutdown)
        except Exception as exc:
            log_event(logger, "langfuse_shutdown_failed", error_type=type(exc).__name__)


@dataclass
class RequestTrace:
    config: dict
    trace_id: str | None = None
    observation: Any = None
    status: str = "disconnected"

    def finish(self) -> None:
        if self.observation is not None:
            try:
                self.observation.update(
                    metadata={"status": self.status},
                    level="ERROR" if self.status == "error" else "WARNING" if self.status != "success" else "DEFAULT",
                )
            except Exception as exc:
                log_event(logger, "langfuse_finish_failed", error_type=type(exc).__name__)
            finally:
                try:
                    self.observation.end()
                except Exception as exc:
                    log_event(logger, "langfuse_end_failed", error_type=type(exc).__name__)


@contextmanager
def trace_request(config: dict, *, request_id: str, session_id: str, user_id: str) -> Iterator[RequestTrace]:
    """显式父 span 连接回调，避免将 OTel 当前上下文跨异步生成器 yield 保持。"""
    result = RequestTrace(config=config)
    if _client is not None:
        try:
            from langfuse import Langfuse, propagate_attributes
            from langfuse.langchain import CallbackHandler

            trace_id = Langfuse.create_trace_id(seed=request_id)
            metadata = {"request_id": request_id, "application": "financial-report-assistant"}
            with propagate_attributes(
                user_id=user_id, session_id=session_id,
                trace_name="financial-report-request", metadata=metadata,
            ):
                result.observation = _client.start_observation(
                    name="financial-report-request", as_type="span",
                    trace_context={"trace_id": trace_id}, metadata=metadata,
                )
            handler = CallbackHandler(
                public_key=get_settings().langfuse_public_key,
                trace_context={"trace_id": trace_id, "parent_span_id": result.observation.id},
            )
            result.config = {
                **config,
                "run_name": "financial-report-main",
                "callbacks": [*(config.get("callbacks") or []), handler],
                "metadata": {
                    **(config.get("metadata") or {}), **metadata,
                    "langfuse_user_id": user_id,
                    "langfuse_session_id": session_id,
                    "langfuse_trace_name": "financial-report-request",
                },
            }
            result.trace_id = trace_id
            log_event(logger, "langfuse_trace_started", trace_id=trace_id, request_id=request_id, session_id=session_id)
        except Exception as exc:
            # 追踪故障降级，保留原 config；不改变模型、计算与会话行为。
            result.status = "error"
            log_event(logger, "langfuse_trace_failed", error_type=type(exc).__name__)
    try:
        yield result
    except Exception:
        result.status = "error"
        raise
    finally:
        result.finish()

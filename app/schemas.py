"""对话和会话 API 的输入输出结构；本地个人工作区，可选关联 Langfuse Trace，不包含反馈接口。"""

from pydantic import BaseModel, Field, field_validator


class AgentRequest(BaseModel):
    """一次对话必须指定已有会话，问题允许来自网页的多行文本。"""
    question: str = Field(min_length=1, max_length=20_000)
    session_id: str = Field(min_length=1, max_length=200)

    @field_validator("question")
    @classmethod
    def clean_question(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("问题不能为空")
        return value


class AgentResponse(BaseModel):
    """request_id 用于本地关联；trace_id 仅在 Langfuse 追踪建立后提供。"""
    answer: str
    request_id: str
    session_id: str
    message_id: str
    trace_id: str | None = None


class SessionCreate(BaseModel):
    title: str | None = Field(default=None, max_length=200)


class SessionUpdate(BaseModel):
    title: str = Field(min_length=1, max_length=200)

    @field_validator("title")
    @classmethod
    def clean_title(cls, value: str) -> str:
        value = value.strip()
        if not value:
            raise ValueError("会话标题不能为空")
        return value


class SessionResponse(BaseModel):
    session_id: str
    user_id: str
    title: str
    created_at: str
    updated_at: str
    last_message_preview: str = ""

"""
LLM客户端封装
统一使用OpenAI格式调用
"""

import json
import logging
import re
from typing import Optional, Dict, Any, List

from openai import OpenAI
from pydantic import BaseModel, ValidationError

from ..config import Config

logger = logging.getLogger("mirofish.llm_client")


# ---------------------------------------------------------------------------
# Graphiti structured-output fallback client
# ---------------------------------------------------------------------------

def _fuzzy_remap(data: dict, model: type[BaseModel]) -> dict:
    """Remap *data* keys to match *model* field names using substring matching.

    Handles cases where LLMs return ``entities`` instead of ``extracted_entities``.
    """
    required = {k for k, v in model.model_fields.items() if v.is_required()}
    if required.issubset(data.keys()):
        return data  # nothing to fix

    result = dict(data)
    for field in set(model.model_fields.keys()) - data.keys():
        for resp_key in data.keys():
            if resp_key in field or field in resp_key:
                result[field] = result.pop(resp_key)
                logger.debug("fuzzy-remapped response key '%s' → model field '%s'", resp_key, field)
                break
    return result


class FallbackLLMClient:
    """Wraps graphiti's OpenAIClient with a JSON-mode fallback.

    On the first attempt it delegates to the wrapped client (structured outputs
    via ``beta.chat.completions.parse``).  If the model returns JSON with wrong
    field names and pydantic raises a ValidationError, this client retries using
    plain ``json_object`` response mode and applies fuzzy field remapping before
    Pydantic validation.
    """

    def __init__(self, inner):
        self._inner = inner

    def __getattr__(self, name):
        # Transparent proxy — graphiti internals access .model, .temperature, etc.
        return getattr(self._inner, name)

    async def generate_response(self, messages, response_model=None, **kwargs) -> dict:
        try:
            return await self._inner.generate_response(
                messages, response_model=response_model, **kwargs
            )
        except Exception as exc:
            if response_model is None:
                raise
            err = str(exc).lower()
            if "validation error" not in err and "field required" not in err:
                raise
            logger.warning(
                "Structured output validation failed (%s). Retrying with JSON mode.", exc
            )
            return await self._json_mode_fallback(messages, response_model, **kwargs)

    async def _json_mode_fallback(self, messages, response_model: type[BaseModel], **kwargs):
        from graphiti_core.llm_client.openai_client import (
            DEFAULT_MODEL,
            DEFAULT_SMALL_MODEL,
            ModelSize,
        )

        model_size = kwargs.get("model_size", ModelSize.medium)
        max_tokens = kwargs.get("max_tokens", self._inner.max_tokens)
        model = (
            (self._inner.small_model or DEFAULT_SMALL_MODEL)
            if model_size == ModelSize.small
            else (self._inner.model or DEFAULT_MODEL)
        )

        openai_messages = []
        for m in messages:
            content = self._inner._clean_input(m.content)
            if m.role in ("user", "system"):
                openai_messages.append({"role": m.role, "content": content})

        schema_str = json.dumps(response_model.model_json_schema())
        if openai_messages:
            openai_messages[-1]["content"] += (
                f"\n\nReturn a JSON object matching exactly this schema:\n{schema_str}"
            )

        response = await self._inner.client.chat.completions.create(
            model=model,
            messages=openai_messages,
            response_format={"type": "json_object"},
            temperature=self._inner.temperature,
            max_tokens=max_tokens,
        )

        raw = response.choices[0].message.content
        try:
            data = json.loads(raw)
        except json.JSONDecodeError as e:
            raise ValueError(f"JSON mode returned non-JSON: {raw[:200]}") from e

        remapped = _fuzzy_remap(data, response_model)
        try:
            return response_model.model_validate(remapped).model_dump()
        except ValidationError as e:
            raise ValueError(
                f"JSON mode fallback still failed after remapping. Response: {raw[:400]}"
            ) from e


class LLMClient:
    """LLM客户端"""
    
    def __init__(
        self,
        api_key: Optional[str] = None,
        base_url: Optional[str] = None,
        model: Optional[str] = None
    ):
        self.api_key = api_key or Config.LLM_API_KEY
        self.base_url = base_url or Config.LLM_BASE_URL
        self.model = model or Config.LLM_MODEL_NAME
        
        if not self.api_key:
            raise ValueError("LLM_API_KEY 未配置")
        
        self.client = OpenAI(
            api_key=self.api_key,
            base_url=self.base_url
        )
    
    def chat(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.7,
        max_tokens: int = 4096,
        response_format: Optional[Dict] = None
    ) -> str:
        """
        发送聊天请求
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            response_format: 响应格式（如JSON模式）
            
        Returns:
            模型响应文本
        """
        kwargs = {
            "model": self.model,
            "messages": messages,
            "temperature": temperature,
            "max_tokens": max_tokens,
        }
        
        if response_format:
            kwargs["response_format"] = response_format
        
        response = self.client.chat.completions.create(**kwargs)
        content = response.choices[0].message.content
        # 部分模型（如MiniMax M2.5）会在content中包含<think>思考内容，需要移除
        content = re.sub(r'<think>[\s\S]*?</think>', '', content).strip()
        return content
    
    def chat_json(
        self,
        messages: List[Dict[str, str]],
        temperature: float = 0.3,
        max_tokens: int = 4096
    ) -> Dict[str, Any]:
        """
        发送聊天请求并返回JSON
        
        Args:
            messages: 消息列表
            temperature: 温度参数
            max_tokens: 最大token数
            
        Returns:
            解析后的JSON对象
        """
        response = self.chat(
            messages=messages,
            temperature=temperature,
            max_tokens=max_tokens,
            response_format={"type": "json_object"}
        )
        # 清理markdown代码块标记
        cleaned_response = response.strip()
        cleaned_response = re.sub(r'^```(?:json)?\s*\n?', '', cleaned_response, flags=re.IGNORECASE)
        cleaned_response = re.sub(r'\n?```\s*$', '', cleaned_response)
        cleaned_response = cleaned_response.strip()

        try:
            return json.loads(cleaned_response)
        except json.JSONDecodeError:
            raise ValueError(f"LLM返回的JSON格式无效: {cleaned_response}")


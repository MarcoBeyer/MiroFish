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

def _get_inner_model(field_info) -> type[BaseModel] | None:
    """Extract the Pydantic model from a field annotation like list[Edge]."""
    import typing
    ann = field_info.annotation
    origin = getattr(ann, '__origin__', None)
    if origin is list:
        args = getattr(ann, '__args__', ())
        if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
            return args[0]
    return None


def _fuzzy_remap(data: dict, model: type[BaseModel]) -> dict:
    """Remap *data* keys to match *model* field names, recursively.

    Strategy (in order):
    1. Substring match — ``entities`` → ``extracted_entities``
    2. Type match — if a required field expects list[X] and only one response
       key has a list value, map it regardless of name (``answer`` → ``extracted_entities``)
    3. Recurse into nested Pydantic models (e.g. list[Edge] items)
    """
    required = {k for k, v in model.model_fields.items() if v.is_required()}
    if required.issubset(data.keys()):
        # Keys match — but still recurse into nested models
        return _remap_nested(data, model)

    result = dict(data)
    missing = set(model.model_fields.keys()) - result.keys()
    extra = set(result.keys()) - set(model.model_fields.keys())

    # Pass 1: substring match
    for field in list(missing):
        for resp_key in list(extra):
            if resp_key in field or field in resp_key:
                result[field] = result.pop(resp_key)
                missing.discard(field)
                extra.discard(resp_key)
                logger.debug("fuzzy-remapped '%s' → '%s' (substring)", resp_key, field)
                break

    # Pass 2: type-based match for remaining missing required fields
    still_missing = missing & required
    if still_missing and extra:
        for field in list(still_missing):
            field_info = model.model_fields[field]
            is_list_field = 'list' in str(field_info.annotation).lower()
            candidates = [k for k in extra if isinstance(result.get(k), list) == is_list_field]
            if len(candidates) == 1:
                resp_key = candidates[0]
                result[field] = result.pop(resp_key)
                still_missing.discard(field)
                extra.discard(resp_key)
                logger.debug("fuzzy-remapped '%s' → '%s' (type match)", resp_key, field)

    return _remap_nested(result, model)


def _remap_nested(data: dict, model: type[BaseModel]) -> dict:
    """Recurse into list[SomePydanticModel] fields and remap each item."""
    for field_name, field_info in model.model_fields.items():
        inner = _get_inner_model(field_info)
        if inner is None or field_name not in data:
            continue
        val = data[field_name]
        if isinstance(val, list):
            data[field_name] = [
                _fuzzy_remap(item, inner) if isinstance(item, dict) else item
                for item in val
            ]
    return data


def _build_schema_instruction(model: type[BaseModel]) -> str:
    """Build explicit field-name instructions from a Pydantic model.

    Instead of dumping raw JSON schema (which GLM ignores), produce
    a clear example showing the exact keys the model must use.
    """
    def _describe_model(m: type[BaseModel], indent: int = 2) -> str:
        lines = []
        prefix = " " * indent
        for name, field in m.model_fields.items():
            ann = field.annotation
            origin = getattr(ann, '__origin__', None)
            if origin is list:
                args = getattr(ann, '__args__', ())
                if args and isinstance(args[0], type) and issubclass(args[0], BaseModel):
                    inner = _describe_model(args[0], indent + 4)
                    lines.append(f'{prefix}"{name}": [{{\n{inner}\n{prefix}  }}]')
                else:
                    lines.append(f'{prefix}"{name}": [...]')
            elif isinstance(ann, type) and issubclass(ann, BaseModel):
                inner = _describe_model(ann, indent + 2)
                lines.append(f'{prefix}"{name}": {{\n{inner}\n{prefix}}}')
            else:
                desc = field.description or name
                lines.append(f'{prefix}"{name}": "<{desc}>"')
        return ",\n".join(lines)

    body = _describe_model(model)
    return (
        f"\n\nIMPORTANT: You MUST respond with a JSON object using EXACTLY these field names "
        f"(do NOT rename any field):\n{{\n{body}\n}}"
    )


class FallbackLLMClient:
    """Subclass of graphiti's OpenAIClient with a JSON-mode fallback.

    Overrides ``_generate_response`` to catch pydantic ValidationErrors caused
    by field-name mismatches (e.g. GLM returns ``entities`` instead of
    ``extracted_entities``) and retries using plain json_object mode + fuzzy
    field remapping.
    """

    @classmethod
    def build(cls, config):
        """Create a FallbackLLMClient that is also an OpenAIClient instance."""
        from graphiti_core.llm_client.openai_client import OpenAIClient

        # Dynamically create a subclass of OpenAIClient so isinstance checks pass
        class _FallbackOpenAIClient(OpenAIClient):
            async def _generate_response(self, messages, response_model=None, max_tokens=None, model_size=None):
                """Skip beta.chat.completions.parse() entirely — go straight to JSON mode.

                z.ai / GLM doesn't support OpenAI structured outputs, so the parse()
                call always fails. Instead, use json_object response_format with
                explicit field-name instructions in the prompt.
                """
                from graphiti_core.llm_client.config import DEFAULT_MAX_TOKENS, ModelSize as MS
                if model_size is None:
                    model_size = MS.medium
                if max_tokens is None:
                    max_tokens = DEFAULT_MAX_TOKENS
                if response_model is not None:
                    return await self._json_fallback(messages, response_model, max_tokens, model_size)
                return await super()._generate_response(messages, response_model, max_tokens, model_size)

            async def _json_fallback(self, messages, response_model, max_tokens, model_size):
                from graphiti_core.llm_client.config import ModelSize as MS
                model = self.small_model if model_size == MS.small else self.model
                openai_messages = [
                    {"role": m.role, "content": m.content}
                    for m in messages
                    if m.role in ("user", "system")
                ]

                # Build explicit field instructions from the Pydantic model
                # so GLM uses exact field names instead of inventing its own
                schema_instruction = _build_schema_instruction(response_model)
                if openai_messages:
                    openai_messages[-1]["content"] += schema_instruction

                response = await self.client.chat.completions.create(
                    model=model,
                    messages=openai_messages,
                    response_format={"type": "json_object"},
                    temperature=self.temperature,
                    max_tokens=max_tokens,
                )
                raw = response.choices[0].message.content
                try:
                    data = json.loads(raw)
                except json.JSONDecodeError as e:
                    raise ValueError(f"JSON mode returned non-JSON: {raw[:200]}") from e
                # GLM sometimes returns values as JSON-encoded strings, e.g.
                # {"answer": "[{...}]"} instead of {"answer": [{...}]}
                for k, v in data.items():
                    if isinstance(v, str) and v.startswith(('[', '{')):
                        try:
                            data[k] = json.loads(v)
                        except json.JSONDecodeError:
                            pass
                remapped = _fuzzy_remap(data, response_model)
                try:
                    return response_model.model_validate(remapped).model_dump()
                except ValidationError as e:
                    raise ValueError(
                        f"JSON mode fallback still failed after remapping. Response: {raw[:400]}"
                    ) from e

        return _FallbackOpenAIClient(config=config)


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


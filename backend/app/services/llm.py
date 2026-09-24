from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import LLMCache


PROMPT_VERSION = "monitoring-v1"


@dataclass
class LLMResult:
    payload: dict[str, Any]
    token_usage: dict[str, Any]
    model_name: str
    cached: bool = False


def redact_sensitive_text(value: str) -> str:
    text = re.sub(r"(?<!\d)\d{17}[\dXx](?!\d)", "[身份证号已脱敏]", value)
    text = re.sub(r"(?<!\d)1[3-9]\d{9}(?!\d)", "[手机号已脱敏]", text)
    text = re.sub(
        r"(?<!\d)(?:\d[ -]?){15,19}(?!\d)", "[账号已脱敏]", text
    )
    return text


def _content_from_response(payload: dict[str, Any]) -> str:
    choices = payload.get("choices")
    if isinstance(choices, list) and choices:
        message = choices[0].get("message") if isinstance(choices[0], dict) else None
        if isinstance(message, dict) and isinstance(message.get("content"), str):
            return message["content"]
    if isinstance(payload.get("message"), str):
        return payload["message"]
    raise ValueError("大模型没有返回可解析内容。")


def _parse_json_content(content: str) -> dict[str, Any]:
    stripped = content.strip()
    if stripped.startswith("```"):
        stripped = re.sub(r"^```(?:json)?\s*", "", stripped)
        stripped = re.sub(r"\s*```$", "", stripped)
    parsed = json.loads(stripped)
    if not isinstance(parsed, dict):
        raise ValueError("大模型必须返回 JSON 对象。")
    return parsed


class LLMService:
    def __init__(self) -> None:
        self.settings = get_settings()

    @property
    def is_configured(self) -> bool:
        return bool(
            self.settings.llm_base_url
            and self.settings.llm_model
            and self.settings.llm_api_key
        )

    def complete_json(
        self,
        db: Session,
        *,
        purpose: str,
        system_prompt: str,
        user_payload: dict[str, Any],
        max_output_tokens: int = 4000,
    ) -> LLMResult | None:
        if not self.is_configured:
            return None
        canonical = json.dumps(
            {
                "prompt_version": PROMPT_VERSION,
                "purpose": purpose,
                "model": self.settings.llm_model,
                "system": system_prompt,
                "payload": user_payload,
            },
            ensure_ascii=False,
            sort_keys=True,
            separators=(",", ":"),
        )
        input_hash = hashlib.sha256(canonical.encode("utf-8")).hexdigest()
        cached = db.execute(
            select(LLMCache).where(LLMCache.input_hash == input_hash)
        ).scalar_one_or_none()
        if cached:
            return LLMResult(
                payload=cached.response_payload or {},
                token_usage=cached.token_usage or {},
                model_name=cached.model_name,
                cached=True,
            )

        url = f"{self.settings.llm_base_url}{self.settings.llm_chat_path}"
        headers = {"Content-Type": "application/json"}
        if self.settings.llm_api_key:
            headers["Authorization"] = f"Bearer {self.settings.llm_api_key}"
        request_payload = {
            "model": self.settings.llm_model,
            "stream": False,
            "temperature": 0.1,
            "max_tokens": max_output_tokens,
            "messages": [
                {"role": "system", "content": system_prompt},
                {
                    "role": "user",
                    "content": redact_sensitive_text(
                        json.dumps(user_payload, ensure_ascii=False)
                    ),
                },
            ],
        }
        if "api.deepseek.com" in self.settings.llm_base_url:
            request_payload["response_format"] = {"type": "json_object"}
            request_payload["thinking"] = {"type": "disabled"}
        with httpx.Client(timeout=90, follow_redirects=True) as client:
            response = client.post(url, headers=headers, json=request_payload)
            response.raise_for_status()
            raw = response.json()
        parsed = _parse_json_content(_content_from_response(raw))
        usage = raw.get("usage") if isinstance(raw.get("usage"), dict) else {}
        cache = LLMCache(
            input_hash=input_hash,
            purpose=purpose,
            provider=self.settings.llm_base_url,
            model_name=self.settings.llm_model,
            response_payload=parsed,
            token_usage=usage,
        )
        db.add(cache)
        db.commit()
        return LLMResult(
            payload=parsed,
            token_usage=usage,
            model_name=self.settings.llm_model,
        )

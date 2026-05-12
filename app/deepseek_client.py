from __future__ import annotations

import json
import re
from dataclasses import dataclass
from typing import Any, Dict, Optional

import httpx
from tenacity import retry, stop_after_attempt, wait_exponential

from .config import Settings


@dataclass
class AnalysisResult:
    macro_category: str
    tech_category: str
    sentiment_score: float | None
    impact_score: float | None
    analysis_text: str


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_enabled and self.settings.deepseek_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def analyze_article(
        self, *, title: str, summary: str | None, macro_hint: str, tech_hint: str
    ) -> Optional[AnalysisResult]:
        if not self.enabled:
            return None

        prompt = {
            "title": title,
            "summary": summary or "",
            "hints": {
                "macro_category": macro_hint,
                "tech_category": tech_hint,
                "macro_choices": ["industry", "stock", "academic"],
                "tech_choices": ["low_power", "high_power", "high_frequency", "materials", "packaging", "other"],
            },
            "task": (
                "Return strict JSON with keys: macro_category, tech_category, "
                "sentiment_score(-1~1), impact_score(0~100), analysis_text(<=120 words). "
                "Classify primarily from the title. Use summary only as auxiliary context."
            ),
        }
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "You are an analyst for GaN semiconductor industry intelligence. "
                        "Output strict JSON only."
                    ),
                },
                {"role": "user", "content": json.dumps(prompt, ensure_ascii=False)},
            ],
            "temperature": 0.2,
            "response_format": {"type": "json_object"},
        }
        raw = self._chat_completion(data)
        parsed = _extract_json(raw)
        return AnalysisResult(
            macro_category=str(parsed.get("macro_category", macro_hint)),
            tech_category=str(parsed.get("tech_category", tech_hint)),
            sentiment_score=_float_or_none(parsed.get("sentiment_score")),
            impact_score=_float_or_none(parsed.get("impact_score")),
            analysis_text=str(parsed.get("analysis_text", "")).strip(),
        )

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def summarize_daily(self, report_payload: Dict[str, Any]) -> Optional[str]:
        """Daily briefing — concise 100-150 words, Chinese."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是氮化镓半导体行业分析师。"
                        "请根据提供的昨日资讯，用中文写一段简洁的日报摘要，100-150字，"
                        "重点提炼最重要的1-3条动态，并简评其产业意义。"
                    ),
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.4,
        }
        return self._chat_completion(data).strip()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def summarize_weekly(self, report_payload: Dict[str, Any]) -> Optional[str]:
        """Weekly summary — 200-280 words, Chinese."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是氮化镓半导体行业分析师。"
                        "请根据提供的本周资讯，用中文写一段周报摘要，200-280字，"
                        "涵盖本周主要技术、产业与市场动态，并指出值得持续关注的趋势。"
                    ),
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.4,
        }
        return self._chat_completion(data).strip()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def summarize_monthly(self, report_payload: Dict[str, Any]) -> Optional[str]:
        """Monthly review with trend comparison — 350-450 words, Chinese."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": (
                        "你是氮化镓半导体行业资深分析师。"
                        "请根据提供的本月与上月资讯数据，用中文写一份月度报告，350-450字，"
                        "包含：① 本月整体动态总结 ② 与上月相比的变化趋势（数量、类别分布） "
                        "③ 重点技术/产业事件点评 ④ 下月值得关注的方向。"
                    ),
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.4,
        }
        return self._chat_completion(data).strip()

    def _chat_completion(self, payload: Dict[str, Any]) -> str:
        if not self.enabled:
            raise RuntimeError("DeepSeek API key is not configured.")
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=self.settings.deepseek_timeout_seconds) as client:
            resp = client.post(url, headers=headers, json=payload)
            resp.raise_for_status()
            content = resp.json()["choices"][0]["message"]["content"]
            return str(content)


def _extract_json(raw: str) -> Dict[str, Any]:
    text = raw.strip()
    if text.startswith("```"):
        text = re.sub(r"^```(?:json)?", "", text).strip()
        text = re.sub(r"```$", "", text).strip()
    return json.loads(text)


def _float_or_none(value: Any) -> float | None:
    try:
        if value is None or value == "":
            return None
        return float(value)
    except (ValueError, TypeError):
        return None

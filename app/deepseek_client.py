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


# ────────────────────────────────────────────────────────────────────────────
# Report prompts. These reports are meant to read like a short paper: every claim
# carries its evidence and its uncertainty, not a headline recap.
# ────────────────────────────────────────────────────────────────────────────
_ANALYST_PERSONA = '你是一位氮化镓（GaN）功率与射频半导体领域的资深产业分析师，同时具备材料物理、器件工艺与产业经济的训练背景。你的读者是行业从业者与技术决策者，他们要的不是新闻复述，而是有依据、有推理链条、能支撑决策的深度论述。'

_ANALYST_METHOD = '写作方法（必须严格遵守）：\n1. 论证密度优先于篇幅。每一个判断都必须给出依据，依据来自所提供资讯中的具体信息（公司名、数值、时间、技术指标、来源），并写出从依据到结论的推理链条，不允许只抛结论。\n2. 遇到关键技术概念（如 GaN-on-Si 衬底迁移、增强型 e-mode HEMT、p-GaN 栅、Cascode 结构、Rds(on)/Qg/Coss 品质因数、动态导通电阻、良率爬坡、6 吋转 8 吋产线等），要简要说明其物理或工程含义，并解释它在本期资讯语境下为何重要。\n3. 严格区分三类陈述并在行文中标明：【事实】来自资讯的客观信息；【厂商宣称】企业单方口径，需注明未经第三方验证；【推断】你的分析结论，必须同时写出成立前提与不确定性。\n4. 主动做交叉验证：指出不同来源的资讯何处互相印证、何处互相矛盾，矛盾处给出可能的解释。\n5. 诚实说明局限：明确指出哪些关键数据缺失导致判断无法收敛，以及需要什么信息才能验证。\n6. 不要罗列文章标题清单——正文之后系统会自动附上完整原文链接列表，重复即冗余。\n7. 禁止空泛套话（“值得关注”“前景广阔”“具有重要意义”等），除非其后紧跟具体、可检验的理由。\n8. 输入数据中的 summary / prior_analysis / impact_score 字段是你论证的主要素材，要真正引用其内容，不要只看标题。\n9. 全文用简体中文。可使用 Markdown 的 ## 二级标题、- 无序列表与 **加粗**，不要使用表格。'

_DAILY_SYSTEM = '你是一位氮化镓（GaN）功率与射频半导体领域的资深产业分析师，同时具备材料物理、器件工艺与产业经济的训练背景。你的读者是行业从业者与技术决策者，他们要的不是新闻复述，而是有依据、有推理链条、能支撑决策的深度论述。\n\n任务：根据提供的资讯数据，撰写一份深度分析，正文 900-1400 字。\n必须包含以下小节（用 ## 二级标题）：\n## 本期要点研判\n挑出 1-3 条最具信息量的动态，逐条展开：发生了什么、技术或商业实质是什么、为何重要。\n## 技术维度分析\n从材料、器件结构、工艺或封装角度切入，分析本期资讯反映的技术进展或瓶颈。\n## 产业与市场含义\n分析对供应链、竞争格局、成本结构或下游应用（快充、数据中心电源、车载 OBC、光伏逆变等）的影响。\n## 存疑与待验证\n列出无法从现有资讯确认的关键问题。\n\n写作方法（必须严格遵守）：\n1. 论证密度优先于篇幅。每一个判断都必须给出依据，依据来自所提供资讯中的具体信息（公司名、数值、时间、技术指标、来源），并写出从依据到结论的推理链条，不允许只抛结论。\n2. 遇到关键技术概念（如 GaN-on-Si 衬底迁移、增强型 e-mode HEMT、p-GaN 栅、Cascode 结构、Rds(on)/Qg/Coss 品质因数、动态导通电阻、良率爬坡、6 吋转 8 吋产线等），要简要说明其物理或工程含义，并解释它在本期资讯语境下为何重要。\n3. 严格区分三类陈述并在行文中标明：【事实】来自资讯的客观信息；【厂商宣称】企业单方口径，需注明未经第三方验证；【推断】你的分析结论，必须同时写出成立前提与不确定性。\n4. 主动做交叉验证：指出不同来源的资讯何处互相印证、何处互相矛盾，矛盾处给出可能的解释。\n5. 诚实说明局限：明确指出哪些关键数据缺失导致判断无法收敛，以及需要什么信息才能验证。\n6. 不要罗列文章标题清单——正文之后系统会自动附上完整原文链接列表，重复即冗余。\n7. 禁止空泛套话（“值得关注”“前景广阔”“具有重要意义”等），除非其后紧跟具体、可检验的理由。\n8. 输入数据中的 summary / prior_analysis / impact_score 字段是你论证的主要素材，要真正引用其内容，不要只看标题。\n9. 全文用简体中文。可使用 Markdown 的 ## 二级标题、- 无序列表与 **加粗**，不要使用表格。'

_WEEKLY_SYSTEM = '你是一位氮化镓（GaN）功率与射频半导体领域的资深产业分析师，同时具备材料物理、器件工艺与产业经济的训练背景。你的读者是行业从业者与技术决策者，他们要的不是新闻复述，而是有依据、有推理链条、能支撑决策的深度论述。\n\n任务：根据提供的本周资讯数据，撰写一份深度周度分析报告，正文 2000-3000 字。\n必须包含以下小节（用 ## 二级标题）：\n## 一、本周总览与结构特征\n结合 macro_count / tech_count 的分布，分析本周资讯在学术与产业、各技术类别间的分布说明了什么，而不是复述数字。样本量小的类别要指出其波动不具统计意义。\n## 二、技术进展深度解读\n挑选 2-4 项最有实质内容的技术进展，逐项深入：技术路线、关键指标、与既有方案对比、工程化难度与量产距离。\n## 三、产业动态与竞争格局\n厂商动作、产能、供应链、客户导入进展及其相互关系。\n## 四、交叉印证与矛盾点\n把本周不同来源的信息放在一起比对，指出印证与冲突之处。\n## 五、趋势判断与前瞻\n给出可证伪的判断，并明确说明观察哪些指标可以验证或推翻它。\n## 六、本周分析的局限\n\n写作方法（必须严格遵守）：\n1. 论证密度优先于篇幅。每一个判断都必须给出依据，依据来自所提供资讯中的具体信息（公司名、数值、时间、技术指标、来源），并写出从依据到结论的推理链条，不允许只抛结论。\n2. 遇到关键技术概念（如 GaN-on-Si 衬底迁移、增强型 e-mode HEMT、p-GaN 栅、Cascode 结构、Rds(on)/Qg/Coss 品质因数、动态导通电阻、良率爬坡、6 吋转 8 吋产线等），要简要说明其物理或工程含义，并解释它在本期资讯语境下为何重要。\n3. 严格区分三类陈述并在行文中标明：【事实】来自资讯的客观信息；【厂商宣称】企业单方口径，需注明未经第三方验证；【推断】你的分析结论，必须同时写出成立前提与不确定性。\n4. 主动做交叉验证：指出不同来源的资讯何处互相印证、何处互相矛盾，矛盾处给出可能的解释。\n5. 诚实说明局限：明确指出哪些关键数据缺失导致判断无法收敛，以及需要什么信息才能验证。\n6. 不要罗列文章标题清单——正文之后系统会自动附上完整原文链接列表，重复即冗余。\n7. 禁止空泛套话（“值得关注”“前景广阔”“具有重要意义”等），除非其后紧跟具体、可检验的理由。\n8. 输入数据中的 summary / prior_analysis / impact_score 字段是你论证的主要素材，要真正引用其内容，不要只看标题。\n9. 全文用简体中文。可使用 Markdown 的 ## 二级标题、- 无序列表与 **加粗**，不要使用表格。'

_MONTHLY_SYSTEM = '你是一位氮化镓（GaN）功率与射频半导体领域的资深产业分析师，同时具备材料物理、器件工艺与产业经济的训练背景。你的读者是行业从业者与技术决策者，他们要的不是新闻复述，而是有依据、有推理链条、能支撑决策的深度论述。\n\n任务：根据提供的本月与上月资讯数据，撰写一份深度月度研究报告，正文 3000-4200 字。\n必须包含以下小节（用 ## 二级标题）：\n## 一、月度总览\n## 二、环比结构变化分析\n必须实际使用 this_macro_count / prev_macro_count / this_tech_count / prev_tech_count 的数字做对比，分析分布变化背后的产业含义。若某类别基数过小，要明确指出其变化属统计噪声而非趋势。\n## 三、技术主线梳理\n识别本月贯穿多条资讯的技术主线，追溯其演进逻辑。\n## 四、产业与市场格局演变\n## 五、关键事件深度点评\n挑选 3-5 个事件做论文式的展开论证。\n## 六、下月前瞻与观察指标\n给出具体、可验证的观察点，而非泛泛的预测。\n## 七、方法论局限\n说明本报告基于 RSS 公开资讯抓取，存在来源偏倚、覆盖不全、中英文源不均衡等局限。\n\n写作方法（必须严格遵守）：\n1. 论证密度优先于篇幅。每一个判断都必须给出依据，依据来自所提供资讯中的具体信息（公司名、数值、时间、技术指标、来源），并写出从依据到结论的推理链条，不允许只抛结论。\n2. 遇到关键技术概念（如 GaN-on-Si 衬底迁移、增强型 e-mode HEMT、p-GaN 栅、Cascode 结构、Rds(on)/Qg/Coss 品质因数、动态导通电阻、良率爬坡、6 吋转 8 吋产线等），要简要说明其物理或工程含义，并解释它在本期资讯语境下为何重要。\n3. 严格区分三类陈述并在行文中标明：【事实】来自资讯的客观信息；【厂商宣称】企业单方口径，需注明未经第三方验证；【推断】你的分析结论，必须同时写出成立前提与不确定性。\n4. 主动做交叉验证：指出不同来源的资讯何处互相印证、何处互相矛盾，矛盾处给出可能的解释。\n5. 诚实说明局限：明确指出哪些关键数据缺失导致判断无法收敛，以及需要什么信息才能验证。\n6. 不要罗列文章标题清单——正文之后系统会自动附上完整原文链接列表，重复即冗余。\n7. 禁止空泛套话（“值得关注”“前景广阔”“具有重要意义”等），除非其后紧跟具体、可检验的理由。\n8. 输入数据中的 summary / prior_analysis / impact_score 字段是你论证的主要素材，要真正引用其内容，不要只看标题。\n9. 全文用简体中文。可使用 Markdown 的 ## 二级标题、- 无序列表与 **加粗**，不要使用表格。'


class DeepSeekClient:
    def __init__(self, settings: Settings):
        self.settings = settings

    @property
    def enabled(self) -> bool:
        return bool(self.settings.deepseek_enabled and self.settings.deepseek_api_key)

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def analyze_article(
        self, *, title: str, summary: str | None, macro_hint: str, tech_hint: str,
        macro_keys: list | None = None, tech_keys: list | None = None,
    ) -> Optional[AnalysisResult]:
        if not self.enabled:
            return None

        prompt = {
            "title": title,
            "summary": summary or "",
            "hints": {
                "macro_category": macro_hint,
                "tech_category": tech_hint,
                "macro_choices": macro_keys or ["industry", "stock", "academic"],
                "tech_choices": tech_keys or [
                    "industry_low_power", "industry_high_power", "industry_high_frequency",
                    "industry_materials", "industry_packaging", "industry_other",
                    "academic_low_power", "academic_high_power", "academic_high_frequency",
                    "academic_materials", "academic_packaging", "academic_other",
                ],
            },
            "task": (
                "Return strict JSON with keys: macro_category, tech_category, "
                "sentiment_score(-1~1), impact_score(0~100), analysis_text(<=120 words, Chinese). "
                "macro_category MUST be exactly one value from macro_choices. "
                "tech_category MUST be exactly one value from tech_choices (most specific match). "
                "Classify from title first; use summary as context."
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
        """Daily deep-dive — 900-1400 words of argued analysis, Chinese."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": _DAILY_SYSTEM,
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.5,
            "max_tokens": self.settings.deepseek_report_max_tokens,
        }
        return self._chat_completion(
            data, timeout=self.settings.deepseek_report_timeout_seconds
        ).strip()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def summarize_weekly(self, report_payload: Dict[str, Any]) -> Optional[str]:
        """Weekly deep-dive — 2000-3000 words, sectioned like a short paper."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": _WEEKLY_SYSTEM,
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.5,
            "max_tokens": self.settings.deepseek_report_max_tokens,
        }
        return self._chat_completion(
            data, timeout=self.settings.deepseek_report_timeout_seconds
        ).strip()

    @retry(stop=stop_after_attempt(3), wait=wait_exponential(multiplier=1, min=1, max=8), reraise=True)
    def summarize_monthly(self, report_payload: Dict[str, Any]) -> Optional[str]:
        """Monthly research report — 3000-4200 words with MoM structural analysis."""
        if not self.enabled:
            return None
        data = {
            "model": self.settings.deepseek_model,
            "messages": [
                {
                    "role": "system",
                    "content": _MONTHLY_SYSTEM,
                },
                {"role": "user", "content": json.dumps(report_payload, ensure_ascii=False)},
            ],
            "temperature": 0.5,
            "max_tokens": self.settings.deepseek_report_max_tokens,
        }
        return self._chat_completion(
            data, timeout=self.settings.deepseek_report_timeout_seconds
        ).strip()

    def _chat_completion(self, payload: Dict[str, Any], timeout: float | None = None) -> str:
        if not self.enabled:
            raise RuntimeError("DeepSeek API key is not configured.")
        url = f"{self.settings.deepseek_base_url.rstrip('/')}/chat/completions"
        headers = {
            "Authorization": f"Bearer {self.settings.deepseek_api_key}",
            "Content-Type": "application/json",
        }
        with httpx.Client(timeout=timeout or self.settings.deepseek_timeout_seconds) as client:
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

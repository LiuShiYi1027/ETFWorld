"""
AI 研判服务（任意兼容 OpenAI Chat Completions 协议的模型服务）

在"规则引擎打分"之外叠一层大模型的自然语言解读：
- 网格研判 grid_review：输入服务端从估值库组装的全量上下文（估值绝对值与
  历史中位、多周期分位、近期动量、波动率、规则分），让模型做研究性解读；
- 计划体检 plan_review：输入用户的具体网格计划与压力测试结果，
  让模型找出这份计划最脆弱的地方。

⚠️ 定位是"研究助手"而非"投顾"：只做数据解读与风险提示，不输出买卖指令。
⚠️ Key 只在后端读取（环境变量），绝不下发到前端。前端只调本服务的代理接口。

结果按 (标的, 数据日期) 做进程内缓存：估值一天只变一次，重复打开面板不再计费。

配置（写入 backend/.env，见 .env.example）：
  AI_API_URL / AI_API_KEY / AI_MODEL（旧变量名 DEEPSEEK_* 仍兼容读取）
"""
import json
import logging
from typing import Dict, List, Optional

import requests

from backend.config import settings

logger = logging.getLogger(__name__)


class AINotConfigured(RuntimeError):
    """未配置 AI_API_KEY 时抛出，调用方据此让前端回退"""


_SYSTEM = "你是严谨的网格策略研究助手，负责解读数据、提示风险，不提供投资建议。只依据给定数据判断，不编造数据，输出严格 JSON。"

# 期望的返回结构（与前端 AI 面板字段一致）
_SCHEMA_HINT = (
    '只返回严格JSON，无其它文字：'
    '{"verdict":"适合或谨慎或不适合",'
    '"oneLine":"一句话结论40字内",'
    '"detail":"深入解读2-3段，每段2-3句：结合给定数据具体分析（分位与中位的关系、'
    '多周期分位背离、动量与波动的组合含义），忌空话套话",'
    '"reasons":["依据1","依据2","依据3","依据4","依据5"],'
    '"risks":["风险1","风险2","风险3"],'
    '"paramHint":"参数倾向30字内"}'
)

_PCT_LABEL = {'3y': '3年', '5y': '5年', '10y': '10年', 'all': '全历史'}


def _period_pct(entry: Optional[Dict]) -> Optional[float]:
    """单个周期的综合分位：PE/PB 分位取可用项平均（与规则引擎口径一致）"""
    if not entry:
        return None
    avail = [v for v in (entry.get('pe'), entry.get('pb')) if v is not None]
    return round(sum(avail) / len(avail), 1) if avail else None


def _build_prompt(ctx: Dict) -> str:
    """网格研判 prompt：全量估值上下文，让模型说规则引擎说不出的东西"""
    name = ctx.get('name') or ctx.get('ts_code') or '该标的'
    parts = [f"标的：{name}（{ctx.get('category') or 'A股指数'}）。"]
    pe, pb = ctx.get('pe_ttm'), ctx.get('pb')
    if pe is not None:
        med = ctx.get('pe_median')
        parts.append(f"PE_TTM {pe}" + (f"（历史中位 {med}）" if med is not None else "") + "。")
    if pb is not None:
        med = ctx.get('pb_median')
        parts.append(f"PB {pb}" + (f"（历史中位 {med}）" if med is not None else "") + "。")
    pcts = ctx.get('percentiles') or {}
    pct_seg = '，'.join(
        f"{_PCT_LABEL.get(p, p)} {_period_pct(pcts.get(p))}%"
        for p in ('3y', '5y', '10y', 'all') if _period_pct(pcts.get(p)) is not None)
    if pct_seg:
        parts.append(f"估值分位：{pct_seg}（越低越便宜）。")
    ret_seg = '，'.join(
        f"{label} {val:+.1f}%"
        for key, label in (('ret_20d', '20日'), ('ret_60d', '60日'), ('ret_120d', '120日'))
        if (val := ctx.get(key)) is not None)
    if ret_seg:
        parts.append(f"近期动量：{ret_seg}。")
    dist = ctx.get('dist_52w_high')
    if dist is not None:
        parts.append(f"距近一年高点 {dist:+.1f}%。")
    vol = ctx.get('volatility')
    if vol is not None:
        parts.append(f"年化波动 {vol:.0f}%。")
    if ctx.get('score') is not None:
        parts.append(f"规则引擎网格分 {ctx['score']}/100，规则结论「{ctx.get('verdict') or '—'}」。")
    parts.append("请解读以上数据，判断它现在是否适合做网格交易。"
                 "只做研究解读，不给出买卖指令或收益承诺。")
    parts.append(_SCHEMA_HINT)
    return "".join(parts)


def _build_plan_prompt(plan: Dict) -> str:
    """计划体检 prompt：具体参数 + 压力测试，找出计划最脆弱的地方"""
    pt = plan.get('pressure_test') or {}
    version = plan.get('version') or '1.0'
    parts = [f"用户的网格计划「{plan.get('name')}」，"
             f"标的 {plan.get('symbol_name') or ''}（{plan.get('symbol')}），网格{version}。"]
    params = (f"基准价 {plan.get('base_price')}，格距 {plan.get('grid_step')}%，"
              f"共 {plan.get('grid_count')} 格，首格金额 {plan.get('amount_per_grid')} 元")
    if float(plan.get('step_increase') or 0) > 0:
        params += f"，逐格加码 {plan.get('step_increase')}%"
    if float(plan.get('profit_retention') or 0) > 0:
        params += f"，留利润 {plan.get('profit_retention')}%"
    parts.append(params + "。")
    if pt:
        parts.append(
            f"压力测试：满格共需资金 {pt.get('total_capital')} 元，"
            f"最深一格跌至 {pt.get('lowest_price')}（距基准 {pt.get('max_fall_pct')}%），"
            f"满格浮亏 {pt.get('max_unrealized_loss_pct')}%。")
    if plan.get('note'):
        parts.append(f"用户备注：{plan['note']}。")
    parts.append("请体检这份计划：最脆弱的地方是什么？资金能否扛到满格？"
                 "参数与「低估买入、波动套利、不死标的」的网格理念匹配吗？"
                 "verdict 含义：适合=计划稳健可执行，谨慎=有明显短板需调整，不适合=计划不可行。"
                 "只做研究解读，不给出买卖指令或收益承诺。")
    parts.append(_SCHEMA_HINT)
    return "".join(parts)


_WEEKLY_SCHEMA_HINT = (
    '只返回严格JSON，无其它文字：'
    '{"done":"本周执行回顾，2-3句，提到具体计划与数字",'
    '"discipline":"纪律检查，1-2句，违约要点名",'
    '"next":"下周关注，1-2句，结合估值与预警"}'
)


def _build_weekly_prompt(ctx: Dict) -> str:
    """周报复述 prompt：组合复盘数据 + 预警 → 研究助手口吻的周报"""
    totals = ctx.get('totals') or {}
    parts = [f"本周（{ctx.get('week')}）用户的网格组合复盘数据："]
    parts.append(f"合计：套利回合 {totals.get('rounds', 0)} 次，"
                 f"已实现 {totals.get('realized_pnl', 0)} 元，"
                 f"该买没买 {totals.get('missed_buy', 0)} 次，"
                 f"该卖没卖 {totals.get('missed_sell', 0)} 次。")
    for p in (ctx.get('plans') or [])[:8]:
        parts.append(f"计划「{p.get('name')}」（{p.get('status')}）：回合 {p.get('rounds')}，"
                     f"已实现 {p.get('realized_pnl')} 元，"
                     f"违约 买{p.get('missed_buy')}/卖{p.get('missed_sell')}。")
    alerts = ctx.get('alerts') or {}
    for kind, label in (('broken', '破网'), ('high', '高位运行'), ('valuation', '估值越界')):
        for a in (alerts.get(kind) or [])[:3]:
            extra = f"，分位 {a['valuation_percentile']}%" if a.get('valuation_percentile') else ''
            parts.append(f"{label}预警：「{a.get('name')}」现价 {a.get('cur')}{extra}。")
    parts.append("把以上数据复述成一份周报。要求：研究助手口吻，只说数据里有的东西，"
                 "不编造行情，不给出买卖指令或收益承诺。")
    parts.append(_WEEKLY_SCHEMA_HINT)
    return "".join(parts)


class AIService:
    """AI 研判客户端（OpenAI 兼容 chat/completions，服务商不限）"""

    def __init__(self, timeout: int = 120):
        # 推理型模型（如 DeepSeek V 系 Pro）思考耗时远超普通模型，30s 会被误杀
        self.timeout = timeout
        self._cache: Dict = {}

    @property
    def enabled(self) -> bool:
        return bool(settings.AI_API_KEY)

    def grid_review(self, ctx: Dict) -> Dict:
        """网格适配性研判。ctx 由调用方从估值库组装（ReadinessService.assess）"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        key = ('grid', ctx.get('ts_code'), ctx.get('trade_date'))
        if key not in self._cache:
            self._remember(key, self._ask(_build_prompt(ctx)))
        return self._cache[key]

    def plan_review(self, plan: Dict) -> Dict:
        """网格计划体检。plan 含 levels 与 pressure_test（GridService.get_plan）"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        key = ('plan', plan.get('id'), plan.get('created_at'))
        if key not in self._cache:
            self._remember(key, self._ask(_build_plan_prompt(plan)))
        return self._cache[key]

    def weekly_review(self, ctx: Dict) -> Dict:
        """组合周报复述。ctx 含 week/totals/plans/alerts，按周缓存"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        key = ('weekly', ctx.get('week'))
        if key not in self._cache:
            self._remember(key, self._normalize_weekly(
                self._parse(self._chat([
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _build_weekly_prompt(ctx)},
                ]))))
        return self._cache[key]

    @staticmethod
    def _normalize_weekly(data: Dict) -> Dict:
        return {
            "done": str(data.get("done", ""))[:300],
            "discipline": str(data.get("discipline", ""))[:200],
            "next": str(data.get("next", ""))[:200],
            "source": "ai",
        }

    def _remember(self, key, value) -> None:
        if len(self._cache) > 256:  # 进程内缓存，防止长时间运行无限增长
            self._cache.clear()
        self._cache[key] = value

    def _ask(self, prompt: str) -> Dict:
        content = self._chat([
            {"role": "system", "content": _SYSTEM},
            {"role": "user", "content": prompt},
        ])
        return self._normalize(self._parse(content))

    def _chat(self, messages: List[Dict]) -> str:
        payload = {
            "model": settings.AI_MODEL,
            "messages": messages,
            "temperature": 0.3,
            "stream": False,
            # 主流兼容服务支持强制 JSON 输出；不支持的见下方重试
            "response_format": {"type": "json_object"},
        }
        headers = {
            "Authorization": f"Bearer {settings.AI_API_KEY}",
            "Content-Type": "application/json",
        }
        logger.info("AI 请求 → %s | %s", settings.AI_API_URL, settings.AI_MODEL)
        resp = requests.post(settings.AI_API_URL, json=payload,
                             headers=headers, timeout=self.timeout)
        if resp.status_code in (400, 422):
            # 个别兼容服务不认识 response_format，去掉重试一次（_parse 有截取兜底）
            payload.pop('response_format', None)
            resp = requests.post(settings.AI_API_URL, json=payload,
                                 headers=headers, timeout=self.timeout)
        resp.raise_for_status()
        body = resp.json()
        usage = body.get("usage", {})
        logger.info("AI 完成 ← tokens=%s/%s",
                    usage.get("prompt_tokens"), usage.get("completion_tokens"))
        return body["choices"][0]["message"]["content"]

    @staticmethod
    def _parse(content: str) -> Dict:
        try:
            return json.loads(content)
        except json.JSONDecodeError:
            # 模型偶尔包裹多余文字，截取第一个 JSON 对象兜底
            start, end = content.find('{'), content.rfind('}')
            if start >= 0 and end > start:
                return json.loads(content[start:end + 1])
            raise

    @staticmethod
    def _normalize(data: Dict) -> Dict:
        verdict = data.get("verdict")
        if verdict not in ("适合", "谨慎", "不适合"):
            verdict = "谨慎"
        return {
            "verdict": verdict,
            "oneLine": str(data.get("oneLine", ""))[:60],
            "detail": str(data.get("detail", ""))[:1200],
            "reasons": [str(x) for x in (data.get("reasons") or [])][:5],
            "risks": [str(x) for x in (data.get("risks") or [])][:4],
            "paramHint": str(data.get("paramHint", ""))[:40],
            "source": "ai",
        }

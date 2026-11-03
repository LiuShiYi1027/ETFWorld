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
import math
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


def _finite_number(value) -> Optional[float]:
    """把有效有限数值与缺失/非法值区分开；资金为 0 仍是已知数据。"""
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if math.isfinite(number) else None


_AFFORDABILITY_FIELDS = (
    ('principal', '组合本金'),
    ('cash', '可用现金'),
    ('grid_full_capital', '全部网格剩余满格资金需求'),
    ('plan_invested', '当前计划已投入资金'),
    ('remaining_need', '当前计划剩余资金需求'),
)


def _missing_affordability_fields(plan: Dict) -> List[str]:
    """资金承受力判断所需上下文；0 是有效值，None/空字符串才是缺失。"""
    return [label for key, label in _AFFORDABILITY_FIELDS
            if _finite_number(plan.get(key)) is None]


def _build_plan_prompt(plan: Dict) -> str:
    """计划体检 prompt：具体参数 + 压力测试，找出计划最脆弱的地方"""
    pt = plan.get('pressure_test') or {}
    version = plan.get('version') or '1.0'
    parts = [f"用户的网格计划「{plan.get('name')}」，"
             f"标的 {plan.get('symbol_name') or ''}（{plan.get('symbol')}），网格{version}。"]
    if plan.get('grid_mode') == 'shares':
        params = (f"基准价 {plan.get('base_price')}，格距 {plan.get('grid_step')}%，"
                  f"共 {plan.get('grid_count')} 格，等份额模式，每格 {plan.get('shares_per_grid')} 份")
    else:
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
    missing_capital = _missing_affordability_fields(plan)
    if missing_capital:
        parts.append(
            f"资金上下文不完整，缺少：{'、'.join(missing_capital)}。"
            "禁止判断资金承受力：不得判断现金是否足够、能否扛到满格、是否无偿付风险，"
            "也不得据此宣称计划稳健可执行或不可行；只能分析其他已有数据，并明确说明资金结论为数据不足。")
    else:
        parts.append(
            f"资金上下文：组合本金 {plan['principal']} 元，可用现金 {plan['cash']} 元，"
            f"全部网格剩余满格资金需求 {plan['grid_full_capital']} 元，"
            f"当前计划已投入 {plan['plan_invested']} 元，剩余需求 {plan['remaining_need']} 元。"
            "只有核对以上五项数据后才允许判断资金承受力，且须同时考虑其他网格的资金占用。")
    if plan.get('note'):
        parts.append(f"用户备注：{plan['note']}。")
    affordability_question = ("资金能否扛到满格？" if not missing_capital
                              else "除资金承受力外，还有哪些可由现有数据确认的短板？")
    verdict_guide = ("verdict 含义：适合=计划稳健可执行，谨慎=有明显短板需调整，"
                     "不适合=有完整数据证明计划不可行。" if not missing_capital else
                     "verdict 含义：谨慎=存在短板或关键数据不足，不适合=有完整数据证明计划不可行；"
                     "资金数据不全时不得以适合表示计划稳健可执行，也不得判断资金不可行。")
    parts.append(f"请体检这份计划：最脆弱的地方是什么？{affordability_question}"
                 "参数与「低估买入、波动套利、不死标的」的网格理念匹配吗？"
                 f"{verdict_guide}只做研究解读，不给出买卖指令或收益承诺。")
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


_OPTIMIZE_SCHEMA_HINT = (
    '只返回严格JSON，无其它文字：'
    '{"step":数字(格距%),'
    '"count":数字(格数),'
    '"oneLine":"推荐语40字内",'
    '"reasons":["依据1","依据2","依据3"],'
    '"warning":"风险提示50字内"}'
)


def _build_optimize_prompt(ctx: Dict) -> str:
    """参数寻优解读 prompt：矩阵 + 市场上下文，要"网格真实性"而非纯分数"""
    parts = [f"标的：{ctx.get('symbol_name') or ctx.get('symbol')}（ETF）。"
             f"在近 {ctx.get('n')} 个交易日的真实行情上对 25 组网格参数做了回测。"]
    idx = ctx.get('index') or {}
    if idx.get('valuation_percentile') is not None:
        parts.append(f"关联指数 {idx.get('name')}：估值分位 {idx['valuation_percentile']}%，"
                     f"雷达结论「{idx.get('verdict')}」，"
                     f"年化波动 {idx.get('volatility')}%。")
    if ctx.get('safety_ratio') is not None:
        parts.append(f"用户组合的满格资金/本金安全线当前为 {round(ctx['safety_ratio'] * 100, 1)}%"
                     f"（70% 为警告线，新网格满格资金会推高该值）。")
    parts.append(f"每格金额 {ctx.get('amount')} 元，逐格加码 {ctx.get('inc')}%，留利润 {ctx.get('ret')}%。")
    parts.append("回测矩阵（格距×格数: 收益%/回撤%/套利次数/资金利用率%/低活性，"
                 "按得分取前 12 项）：")
    cells = sorted(ctx.get('cells') or [],
                   key=lambda c: c.get('score', 0), reverse=True)[:12]
    rows = []
    for c in cells:
        rows.append(f"{c['step']}%×{c['count']}: {c['ret']}/{c['dd']}/{c['trades']}"
                    f"/{c.get('invested_pct', 0)}{'（低活性）' if c.get('low_activity') else ''}")
    parts.append('；'.join(rows) + '。')
    best = ctx.get('best') or {}
    if best:
        parts.append(f"按验证集 score=收益−0.45×回撤 的最高分组合是 "
                     f"{best.get('step')}%×{best.get('count')}。")
    costs = ctx.get('costs') if isinstance(ctx.get('costs'), dict) else {}
    validation = ctx.get('validation') if isinstance(ctx.get('validation'), dict) else {}
    if costs:
        parts.append(f"回测成本假设（JSON）：{json.dumps(costs, ensure_ascii=False, sort_keys=True)}。"
                     "矩阵收益须按该口径理解为费后结果。")
    else:
        parts.append("未提供 costs 成本假设，无法确认矩阵是费后结果。")
    if validation:
        parts.append(f"时序样本外验证（JSON）：{json.dumps(validation, ensure_ascii=False, sort_keys=True)}。"
                     "矩阵 ret/dd/trades/score 为验证集结果，train_* 为训练集结果。")
    else:
        parts.append("未提供 validation 时序样本外验证，矩阵不能视为已完成样本外验证。")
    costs_complete = all(_finite_number(costs.get(key)) is not None
                         for key in ('fee_rate', 'min_fee', 'slippage_rate'))
    train_n = _finite_number(validation.get('train_n'))
    validation_n = _finite_number(validation.get('validation_n'))
    validation_complete = (validation.get('method') == 'chronological_holdout'
                           and train_n is not None and train_n > 0
                           and validation_n is not None and validation_n > 0)
    verification_status = ("本次已提供费后成本口径与时序样本外验证，可结合两者讨论稳健性，"
                           "仍不得承诺未来有效。" if costs_complete and validation_complete else
                           "本次缺少完整费后成本口径或时序样本外验证，无法确认稳健，不得宣称稳健。")
    parts.append("请在这些组合里选一个「网格真实性」最好的：既看风险调整得分，"
                 "也看套利次数与资金利用率——成交过少（低活性）的组合更像低频抄底而非网格；"
                 "同时结合估值分位（高位易破网宜保守、低位可适度深格）与组合安全线给出建议。"
                 "必须依据费后结果和样本外验证评价参数，比较训练集与验证集是否明显衰减。"
                 f"{verification_status}"
                 "缺少任一验证信息时，必须明确写出无法确认稳健，"
                 "不得宣称稳健、可靠或未来有效，也不得把样本内费前最优外推为未来表现。"
                 "只做研究解读，不给出买卖指令或收益承诺。")
    parts.append(_OPTIMIZE_SCHEMA_HINT)
    return "".join(parts)


_DISCOVERY_SCHEMA_HINT = (
    '只返回严格JSON，无其它文字：'
    '{"items":[{"name":"行业名",'
    '"verdict":"适合或谨慎或不适合",'
    '"reason":"理由40字内",'
    '"risk":"风险30字内"}]}'
)


def _build_discovery_prompt(ctx: Dict) -> str:
    """寻品研判 prompt：数据筛过的候选，AI 判"不死属性"与网格适配"""
    parts = ["以下行业指数通过了数据初筛（估值分位<50%、年化波动≥12%、有活跃 ETF）。"
             "逐只研判它适不适合做网格交易："]
    for c in ctx.get('items') or []:
        parts.append(f"{c['name']}（{c['level']}）：分位 {c['valuation_percentile']}%，"
                     f"年化波动 {c['volatility']}%，评分 {c['score']}，"
                     f"关联 ETF {c['etf']['name']}（日成交 {c['etf']['amount_yi']} 亿）。")
    parts.append("网格交易的前提是「不死品种」：需求长期存在、不会被技术或政策颠覆。"
                 "请逐只判断：①该行业是否永续（如金融/消费/医药不死，"
                 "落后产能或纯主题需谨慎）；②波动是否来自周期性均值回归而非单边消亡。"
                 "verdict 含义：适合=低估且不死；谨慎=低估但行业逻辑有争议；"
                 "不适合=行业可能消亡。只说数据与行业逻辑里有的东西，"
                 "不编造，不给出买卖指令。")
    parts.append(_DISCOVERY_SCHEMA_HINT)
    return "".join(parts)


_EXIT_SCHEMA_HINT = (
    '只返回严格JSON，无其它文字：'
    '{"verdict":"建议收网或准备退出或继续运行或数据不足",'
    '"oneLine":"一句话结论40字内",'
    '"detail":"深入解读2-3段，每段2-3句：结合估值分位与计划状态具体分析，忌空话套话",'
    '"reasons":["依据1","依据2","依据3"],'
    '"actions":["建议动作1","建议动作2","建议动作3"]}'
)


def _exit_current_price(ctx: Dict) -> Optional[float]:
    """优先使用明确现价；持仓市值/份额可验证地推导现价时也接受。"""
    pos = ctx.get('position') if isinstance(ctx.get('position'), dict) else {}
    for container in (ctx, pos):
        price = _finite_number(container.get('current_price'))
        if price is not None and price > 0:
            return price
    shares = _finite_number(pos.get('shares'))
    market_value = _finite_number(pos.get('market_value'))
    if shares is not None and shares > 0 and market_value is not None:
        price = market_value / shares
        if price > 0:
            return price
    return None


def _exit_data_gaps(ctx: Dict) -> List[str]:
    """退出行动所需的最低数据集；任何关键项缺失都不得生成行动建议。"""
    gaps = []
    pos = ctx.get('position') if isinstance(ctx.get('position'), dict) else {}
    shares = _finite_number(pos.get('shares'))
    if shares is None or shares < 0:
        gaps.append('持仓份额')
    elif shares > 0:
        for key, label in (('cost', '持仓成本'), ('market_value', '持仓市值'),
                           ('unrealized_pnl', '持仓浮盈')):
            if _finite_number(pos.get(key)) is None:
                gaps.append(label)
    if _exit_current_price(ctx) is None:
        gaps.append('ETF 现价')
    idx = ctx.get('index') if isinstance(ctx.get('index'), dict) else {}
    valuation = _finite_number(idx.get('valuation_percentile'))
    if valuation is None or not 0 <= valuation <= 100:
        gaps.append('估值综合分位')
    return gaps


def _exit_data_insufficient(gaps: List[str]) -> Dict:
    missing = '、'.join(gaps) if gaps else '关键数据'
    return {
        'verdict': '数据不足',
        'oneLine': '关键数据不足，无法研判退出',
        'detail': f'缺少或无法验证：{missing}。当前不判断收网、准备退出或继续运行，也不生成行动建议。',
        'reasons': [f'缺少或无法验证：{missing}'],
        'actions': [],
        'source': 'ai',
    }


def _build_exit_prompt(ctx: Dict) -> str:
    """退出研判 prompt：计划状态 + 持仓 + 估值分位，回答"该不该收网、怎么收" """
    plan = ctx.get('plan') or {}
    parts = [f"用户的网格计划「{plan.get('name')}」，标的 {plan.get('symbol_name') or ''}"
             f"（{plan.get('symbol')}），状态 {plan.get('status')}。"]
    if plan.get('grid_mode') == 'shares':
        parts.append(f"参数：基准价 {plan.get('base_price')}，格距 {plan.get('grid_step')}%，"
                     f"共 {plan.get('grid_count')} 格，等份额模式，每格 {plan.get('shares_per_grid')} 份。")
    else:
        parts.append(f"参数：基准价 {plan.get('base_price')}，格距 {plan.get('grid_step')}%，"
                     f"共 {plan.get('grid_count')} 格，每格 {plan.get('amount_per_grid')} 元。")
    pos = ctx.get('position') if isinstance(ctx.get('position'), dict) else {}
    shares = _finite_number(pos.get('shares'))
    if shares is None:
        parts.append("持仓数据缺失，不能按无持仓处理。")
    elif shares > 0:
        mv = pos.get('market_value')
        parts.append(f"当前持仓 {pos['shares']} 份，成本 {pos.get('cost')} 元"
                     + (f"，市值 {mv} 元，浮盈 {pos.get('unrealized_pnl')} 元。" if mv is not None else "。"))
    else:
        parts.append("当前该计划已无持仓。")
    price = _exit_current_price(ctx)
    parts.append(f"ETF 现价 {price:.4f}。" if price is not None else "ETF 现价缺失。")
    if ctx.get('rounds') is not None:
        parts.append(f"历史套利 {ctx['rounds']} 回合，已实现收益 {ctx.get('realized_pnl')} 元。")
    idx = ctx.get('index') or {}
    if idx.get('valuation_percentile') is not None:
        parts.append(f"关联指数 {idx.get('name')}：估值综合分位 {idx['valuation_percentile']}%"
                     f"（70% 以上进入偏高区、80% 以上为高估区），"
                     f"雷达结论「{idx.get('verdict')}」。")
        if idx.get('dist_52w_high') is not None:
            parts.append(f"距近一年高点 {idx['dist_52w_high']:+.1f}%，"
                         f"年化波动 {idx.get('volatility')}%。")
    else:
        parts.append("估值综合分位缺失。")
    parts.append("网格策略的生命周期是「低估开网 → 震荡套利 → 高估收网」。"
                 "只有持仓、ETF 现价与估值综合分位完整有效时，才可判断停止买入、逐步卖出或关闭计划；"
                 "任一关键数据缺失时必须返回 verdict=数据不足、actions=[]，不得给任何行动建议。"
                 "数据完整时，如果建议收网，给出具体的收尾节奏（如先停买单、逢反弹分批卖出、保留利润仓等）。"
                 "verdict 含义：建议收网=高估明确应立即收尾；准备退出=进入偏高区只卖不买、制定退出计划；"
                 "继续运行=估值仍可接受；数据不足=无法研判且无行动建议。只做研究解读，不给出收益承诺。")
    parts.append(_EXIT_SCHEMA_HINT)
    return "".join(parts)


class AIService:
    """AI 研判客户端（OpenAI 兼容 chat/completions，服务商不限）"""

    def __init__(self, timeout: int = 150):
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
        capital_key = tuple(_finite_number(plan.get(key)) for key, _ in _AFFORDABILITY_FIELDS)
        key = ('plan', plan.get('id'), plan.get('created_at'), capital_key)
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

    def optimize_review(self, ctx: Dict) -> Dict:
        """参数寻优解读：读 25 格矩阵给出带上下文的建议。按(标的,窗口,参数)缓存"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        key = ('optimize', ctx.get('symbol'), ctx.get('n'), ctx.get('amount'),
               ctx.get('inc'), ctx.get('ret'))
        if key not in self._cache:
            valid = [(c.get('step'), c.get('count')) for c in (ctx.get('cells') or [])]
            self._remember(key, self._normalize_optimize(
                self._parse(self._chat([
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _build_optimize_prompt(ctx)},
                ])), valid=valid, fallback=ctx.get('best')))
        return self._cache[key]

    def discovery_review(self, ctx: Dict) -> Dict:
        """寻品研判：对数据筛过的候选逐只判"不死属性"。按扫描批次缓存"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        key = ('discovery', ctx.get('batch'))
        if key not in self._cache:
            self._remember(key, self._normalize_discovery(
                self._parse(self._chat([
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _build_discovery_prompt(ctx)},
                ]))))
        return self._cache[key]

    def exit_review(self, ctx: Dict) -> Dict:
        """退出研判：该不该收网、怎么收。按计划、持仓与行情状态缓存"""
        if not self.enabled:
            raise AINotConfigured("AI_API_KEY 未配置，请在设置页或 backend/.env 填入后重启")
        gaps = _exit_data_gaps(ctx)
        if gaps:
            return _exit_data_insufficient(gaps)
        plan = ctx.get('plan') or {}
        pos = ctx.get('position') or {}
        idx = ctx.get('index') or {}
        market_key = (
            _exit_current_price(ctx), idx.get('trade_date'),
            idx.get('valuation_percentile'), idx.get('dist_52w_high'), idx.get('volatility'),
        )
        position_key = tuple(pos.get(key) for key in
                             ('shares', 'cost', 'market_value', 'unrealized_pnl'))
        key = ('exit', plan.get('id'), plan.get('status'), position_key, market_key,
               ctx.get('rounds'), ctx.get('realized_pnl'))
        if key not in self._cache:
            self._remember(key, self._normalize_exit(
                self._parse(self._chat([
                    {"role": "system", "content": _SYSTEM},
                    {"role": "user", "content": _build_exit_prompt(ctx)},
                ]))))
        return self._cache[key]

    @staticmethod
    def _normalize_exit(data: Dict) -> Dict:
        verdict = data.get("verdict")
        if verdict == '数据不足':
            return _exit_data_insufficient(['模型确认关键数据不足'])
        if verdict not in ("建议收网", "准备退出", "继续运行"):
            return _exit_data_insufficient(['AI 返回了无法识别的 verdict，无法形成有效研判'])
        return {
            "verdict": verdict,
            "oneLine": str(data.get("oneLine", ""))[:60],
            "detail": str(data.get("detail", ""))[:1200],
            "reasons": [str(x) for x in (data.get("reasons") or [])][:5],
            "actions": [str(x) for x in (data.get("actions") or [])][:4],
            "source": "ai",
        }

    @staticmethod
    def _normalize_discovery(data: Dict) -> Dict:
        items = []
        for it in (data.get("items") or [])[:12]:
            verdict = it.get("verdict")
            if verdict not in ("适合", "谨慎", "不适合"):
                verdict = "谨慎"
            items.append({
                "name": str(it.get("name", "")).split('（')[0].strip()[:20],
                "verdict": verdict,
                "reason": str(it.get("reason", ""))[:60],
                "risk": str(it.get("risk", ""))[:50],
            })
        return {"items": items, "source": "ai"}

    @staticmethod
    def _normalize_optimize(data: Dict, valid=None, fallback: Dict = None) -> Dict:
        def _num(v, default):
            try:
                return float(v)
            except (TypeError, ValueError):
                return default
        step = _num(data.get("step"), 0)
        count = int(_num(data.get("count"), 0))
        pairs = {(float(s), int(float(c))) for s, c in (valid or [])
                 if s is not None and c is not None}
        snapped = False
        if pairs and (step, count) not in pairs:
            fb = fallback or {}
            fb_step, fb_count = _num(fb.get('step'), 0), int(_num(fb.get('count'), 0))
            if (fb_step, fb_count) in pairs:
                step, count = fb_step, fb_count
            else:
                step, count = min(pairs, key=lambda p: (p[0] - step) ** 2 + (p[1] - count) ** 2)
            snapped = True
        warning = str(data.get("warning", ""))
        if snapped and not warning.strip():
            warning = 'AI 推荐参数不在回测矩阵内，已降级为矩阵内有效组合'
        return {
            "step": step,
            "count": count,
            "oneLine": str(data.get("oneLine", ""))[:80],
            "reasons": [str(x)[:60] for x in (data.get("reasons") or [])][:5],
            "warning": warning[:100],
            "source": "ai",
        }

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

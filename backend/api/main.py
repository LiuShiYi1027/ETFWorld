"""
ETFWorld FastAPI 应用

启动: uvicorn backend.api.main:app --reload
"""
import logging
import requests
from pathlib import Path
from typing import Optional

from fastapi import FastAPI, HTTPException
from fastapi.responses import FileResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel, Field

from backend.utils.db import init_db, get_session
from backend.models.database import ValuationTable
from backend.services.valuation_service import ValuationService
from backend.services.grid_service import GridService
from backend.services.trade_service import TradeService
from backend.services.sw_service import SWService
from backend.services.readiness_service import ReadinessService
from backend.services.etf_service import ETFService
from backend.services.ai_service import AIService, AINotConfigured
from backend.services.backtest_service import BacktestService
from backend.services.portfolio_service import PortfolioService
from backend.services.today_service import TodayService
from backend.services.review_service import ReviewService
from backend.config import settings

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')

app = FastAPI(title='ETFWorld', description='基于E大投资理念的网格策略辅助工具')

FRONTEND_DIR = Path(__file__).resolve().parent.parent.parent / 'frontend'
FRONTEND = FRONTEND_DIR / 'index.html'

# 模块化前端的静态资源（js/css）；vendor 挂载见下方原有位置
app.mount('/js', StaticFiles(directory=FRONTEND_DIR / 'js'), name='js')
app.mount('/css', StaticFiles(directory=FRONTEND_DIR / 'css'), name='css')

grid_service = GridService()
trade_service = TradeService()
sw_service = SWService()
readiness_service = ReadinessService()
etf_service = ETFService()
ai_service = AIService()
backtest_service = BacktestService()
portfolio_service = PortfolioService()
today_service = TodayService(grid_service, trade_service, etf_service,
                             readiness_service, portfolio_service)
review_service = ReviewService(grid_service, trade_service, portfolio_service,
                               etf_service)


@app.on_event('startup')
def startup():
    init_db()
    ValuationService().init_index_info()


@app.get('/')
@app.head('/')  # pywebview/监控探针的 HEAD 请求不应 405
def index():
    return FileResponse(FRONTEND)


@app.get('/api/health')
@app.head('/api/health')
def health():
    """桌面壳用于判断本地服务是否已经就绪。"""
    return {'status': 'ok'}


@app.get('/api/version')
def version():
    """应用版本：打包时由 CI 从 git tag 写入 frontend/version.json；源码运行为 dev"""
    import json as _json
    vf = FRONTEND_DIR / 'version.json'
    try:
        if vf.exists():
            return {'version': _json.loads(vf.read_text(encoding='utf-8')).get('version', 'dev')}
    except Exception:  # noqa: BLE001
        pass
    return {'version': 'dev'}


# ---------- 本机设置 ----------

class SettingsUpdate(BaseModel):
    tushare_token: Optional[str] = None
    tushare_api_url: str = 'https://ttx.dailyfetch.top/'
    ai_api_key: Optional[str] = None
    ai_api_url: str = 'https://api.deepseek.com/chat/completions'
    ai_model: str = 'deepseek-chat'
    clear_tushare_token: bool = False
    clear_ai_api_key: bool = False


@app.get('/api/settings')
def settings_get():
    # 首启引导检测：未配置数据源或库内无估值数据时前端进入向导
    with get_session() as session:
        has_data = session.query(ValuationTable.id).limit(1).first() is not None
    return {
        'tushare_configured': bool(settings.TUSHARE_TOKEN),
        'tushare_api_url': settings.TUSHARE_API_URL,
        'ai_configured': bool(settings.AI_API_KEY),
        'ai_api_url': settings.AI_API_URL,
        'ai_model': settings.AI_MODEL,
        'has_data': has_data,
    }


@app.put('/api/settings')
def settings_update(payload: SettingsUpdate):
    values = {
        'TUSHARE_API_URL': payload.tushare_api_url.strip(),
        'AI_API_URL': payload.ai_api_url.strip(),
        'AI_MODEL': payload.ai_model.strip(),
    }
    if payload.clear_tushare_token:
        values['TUSHARE_TOKEN'] = ''
    elif payload.tushare_token and payload.tushare_token.strip():
        values['TUSHARE_TOKEN'] = payload.tushare_token.strip()
    if payload.clear_ai_api_key:
        values['AI_API_KEY'] = ''
    elif payload.ai_api_key and payload.ai_api_key.strip():
        values['AI_API_KEY'] = payload.ai_api_key.strip()
    if not values['TUSHARE_API_URL'] or not values['AI_API_URL'] or not values['AI_MODEL']:
        raise HTTPException(400, '接口地址和模型名称不能为空')
    settings.save_runtime_settings(values)
    return settings_get()


@app.post('/api/settings/test/{provider}')
def settings_test(provider: str):
    try:
        if provider == 'tushare':
            if not settings.TUSHARE_TOKEN:
                raise HTTPException(400, '请先填写并保存 Tushare Token')
            response = requests.post(
                settings.TUSHARE_API_URL,
                json={
                    'api_name': 'index_basic',
                    'token': settings.TUSHARE_TOKEN,
                    'params': {'limit': 1},
                    'fields': 'ts_code,name',
                },
                timeout=15,
            )
            response.raise_for_status()
            body = response.json()
            if body.get('code') not in (None, 0):
                raise HTTPException(400, body.get('msg') or 'Token 校验失败')
            return {'ok': True, 'message': 'Tushare 连接正常'}
        if provider == 'ai':
            if not settings.AI_API_KEY:
                raise HTTPException(400, '请先填写并保存 AI API Key')
            response = requests.post(
                settings.AI_API_URL,
                headers={'Authorization': f'Bearer {settings.AI_API_KEY}', 'Content-Type': 'application/json'},
                json={'model': settings.AI_MODEL, 'messages': [{'role': 'user', 'content': '只回复 OK'}], 'max_tokens': 4},
                timeout=15,
            )
            response.raise_for_status()
            return {'ok': True, 'message': 'AI 服务连接正常'}
        raise HTTPException(404, '未知配置项')
    except HTTPException:
        raise
    except Exception as exc:
        raise HTTPException(400, f'连接失败：{exc}')


# 本地静态资源（ECharts、字体等），桌面/离线运行不依赖 CDN
app.mount('/vendor', StaticFiles(directory=str(FRONTEND_DIR / 'vendor')), name='vendor')


# ---------- 估值 ----------

@app.get('/api/valuation/overview')
def valuation_overview():
    """各指数最新估值与分位点概览"""
    return ValuationService().get_overview()


@app.get('/api/valuation/history/{ts_code}')
def valuation_history(ts_code: str, limit: int = 500):
    return ValuationService().get_history(ts_code, limit)


@app.post('/api/valuation/update')
def valuation_update():
    """拉取最新估值数据（需配置TUSHARE_TOKEN）"""
    result = ValuationService().update_latest()
    if result.get('status') == 'failed':
        raise HTTPException(400, result.get('error'))
    return result


@app.post('/api/valuation/backfill')
def valuation_backfill(start: str, end: str):
    """回填历史数据，日期格式YYYYMMDD"""
    svc = ValuationService()
    result = svc.backfill(start, end)
    if result.get('status') == 'failed':
        raise HTTPException(400, result.get('error'))
    svc.calc_percentiles()
    return result


# ---------- 网格就绪度 ----------

@app.get('/api/readiness')
def readiness_all():
    """评估所有监控指数能否开网格，按就绪度排序"""
    return readiness_service.assess_all()


@app.get('/api/readiness/{ts_code}')
def readiness_one(ts_code: str):
    r = readiness_service.assess(ts_code)
    if not r:
        raise HTTPException(404, '无数据，请先回填该指数')
    return r


# ---------- ETF 标的查找 ----------

@app.get('/api/etf/search')
def etf_search(q: str, limit: int = 20):
    """按关键词搜索可交易ETF（匹配名称/基准，按成交额排序）"""
    return etf_service.search(q, limit)


@app.get('/api/etf/for/{ts_code}')
def etf_for_index(ts_code: str):
    """为某个监控指数查找跟踪它的ETF"""
    return etf_service.find_for_index(ts_code)


@app.get('/api/quote')
def quote(symbols: str):
    """批量取 ETF 最新收盘价，symbols 逗号分隔（如 512880,515790）"""
    syms = [s.strip() for s in symbols.split(',') if s.strip()]
    return etf_service.quotes(syms)


# ---------- 申万行业浏览 ----------

@app.get('/api/sw/list')
def sw_list(level: Optional[str] = None):
    """列出申万行业分类（level=L1/L2/L3，省略则全部）"""
    return sw_service.list_industries(level)


@app.get('/api/sw/search')
def sw_search(q: str, with_valuation: bool = True):
    """按关键词搜索申万行业，默认附带最新估值"""
    if with_valuation:
        return sw_service.search_with_valuation(q)
    return sw_service.search(q)


@app.get('/api/sw/valuation/{ts_code}')
def sw_valuation(ts_code: str):
    """查询单个申万行业最新估值"""
    val = sw_service.get_valuation(ts_code)
    if not val:
        raise HTTPException(404, '未查询到估值')
    return val


# ---------- 网格 ----------

class GridParams(BaseModel):
    name: str = '未命名计划'
    symbol: str = ''
    symbol_name: Optional[str] = None
    base_price: float = Field(gt=0)
    grid_step: float = Field(gt=0, le=50, description='网格大小%')
    grid_count: int = Field(gt=0, le=50)
    amount_per_grid: float = Field(gt=0)
    step_increase: float = Field(default=0, ge=0, le=100, description='逐格加码%')
    profit_retention: float = Field(default=0, ge=0, le=100, description='留利润%')
    note: Optional[str] = None


@app.post('/api/grid/preview')
def grid_preview(params: GridParams):
    """预览网格计划（含压力测试），不保存"""
    return grid_service.preview(params.dict())


@app.post('/api/grid/plans')
def grid_create(params: GridParams):
    if not params.symbol:
        raise HTTPException(400, '请填写标的代码')
    return grid_service.create_plan(params.dict())


@app.get('/api/grid/plans')
def grid_list():
    return grid_service.list_plans()


class BacktestParams(BaseModel):
    symbol: str
    grid_step: float = Field(gt=0, le=50)
    grid_count: int = Field(gt=0, le=50)
    amount_per_grid: float = Field(gt=0)
    step_increase: float = Field(default=0, ge=0, le=100)
    profit_retention: float = Field(default=0, ge=0, le=100)
    lookback_days: int = Field(default=250, ge=20, le=1000)


@app.post('/api/grid/backtest')
def grid_backtest(p: BacktestParams):
    """在真实 ETF 历史价格上回测网格 vs 一直持有（默认近250个交易日）"""
    prices = etf_service.daily_closes(p.symbol, p.lookback_days)
    if len(prices) < 20:
        raise HTTPException(400, '历史数据不足 · 请确认标的代码正确且已配置 TUSHARE_TOKEN')
    return backtest_service.backtest(p.dict(), prices)


@app.post('/api/grid/optimize')
def grid_optimize(p: BacktestParams):
    """间距×格数参数寻优，基于同一段真实历史价格"""
    prices = etf_service.daily_closes(p.symbol, p.lookback_days)
    if len(prices) < 20:
        raise HTTPException(400, '历史数据不足 · 请确认标的代码正确且已配置 TUSHARE_TOKEN')
    return backtest_service.optimize(p.dict(), prices)


class BreakActionParams(BaseModel):
    action: str  # hold / extend / stop
    new_base_price: Optional[float] = None  # extend 必填（现价）


@app.post('/api/grid/plans/{plan_id}/break-action')
def grid_plan_break_action(plan_id: int, p: BreakActionParams):
    """破网处置三选一：hold 装死持有 / extend 向下接网 / stop 止损归档"""
    try:
        result = grid_service.break_action(plan_id, p.action, p.new_base_price)
    except ValueError as e:
        raise HTTPException(400, str(e))
    if result is None:
        raise HTTPException(404, '计划不存在')
    return result


@app.get('/api/grid/plans/{plan_id}')
def grid_get(plan_id: int):
    plan = grid_service.get_plan(plan_id)
    if not plan:
        raise HTTPException(404, '计划不存在')
    return plan


@app.patch('/api/grid/plans/{plan_id}/status')
def grid_status(plan_id: int, status: str):
    if status not in ('active', 'paused', 'closed'):
        raise HTTPException(400, '无效状态')
    if not grid_service.update_status(plan_id, status):
        raise HTTPException(404, '计划不存在')
    return {'ok': True}


@app.delete('/api/grid/plans/{plan_id}')
def grid_delete(plan_id: int):
    if not grid_service.delete_plan(plan_id):
        raise HTTPException(404, '计划不存在')
    return {'ok': True}


# ---------- 交易 ----------

class TradeParams(BaseModel):
    plan_id: Optional[int] = None
    symbol: str
    symbol_name: Optional[str] = None
    trade_date: str = Field(description='YYYY-MM-DD')
    direction: str = Field(pattern='^(buy|sell)$')
    price: float = Field(gt=0)
    shares: float = Field(gt=0)
    fee: float = Field(default=0, ge=0)
    grid_level: Optional[int] = None
    note: Optional[str] = None


@app.post('/api/trades')
def trade_add(params: TradeParams):
    try:
        return trade_service.add_trade(params.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.get('/api/trades')
def trade_list(symbol: Optional[str] = None, plan_id: Optional[int] = None):
    return trade_service.list_trades(symbol, plan_id)


@app.delete('/api/trades/{trade_id}')
def trade_delete(trade_id: int):
    if not trade_service.delete_trade(trade_id):
        raise HTTPException(404, '记录不存在')
    return {'ok': True}


@app.get('/api/positions')
def positions():
    return trade_service.get_positions()


# ---------- AI 研判（OpenAI 兼容服务代理） ----------

class AIGridReviewParams(BaseModel):
    ts_code: str


class AIPlanReviewParams(BaseModel):
    plan_id: int


@app.get('/api/ai/status')
def ai_status():
    """前端据此决定是否展示 AI 研判入口，并显示当前模型名"""
    return {'enabled': ai_service.enabled,
            'model': settings.AI_MODEL if ai_service.enabled else None}


@app.post('/api/ai/grid-review')
def ai_grid_review(params: AIGridReviewParams):
    """网格适配性研判：上下文由服务端从估值库组装。未配置key → 503，前端回退规则结论。"""
    ctx = readiness_service.assess(params.ts_code)
    if not ctx:
        raise HTTPException(404, '该指数暂无估值数据，请先更新数据')
    try:
        return ai_service.grid_review(ctx)
    except AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001 — 网络/解析失败统一兜底
        raise HTTPException(502, f'AI 调用失败: {e}')


@app.post('/api/ai/plan-review')
def ai_plan_review(params: AIPlanReviewParams):
    """网格计划体检：计划参数 + 压力测试结果。未配置key → 503，前端提示去配置。"""
    plan = grid_service.get_plan(params.plan_id)
    if not plan:
        raise HTTPException(404, '计划不存在')
    try:
        return ai_service.plan_review(plan)
    except AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001 — 网络/解析失败统一兜底
        raise HTTPException(502, f'AI 调用失败: {e}')


@app.post('/api/ai/weekly-review')
def ai_weekly_review():
    """组合周报复述：复盘统计 + 预警 → 叙述性周报。未配置key → 503。"""
    from datetime import datetime as _dt
    year, week, _ = _dt.now().isocalendar()
    try:
        review = review_service.review()
        alerts = today_service.today()['alerts']
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f'复盘数据汇总失败: {e}')
    ctx = {
        'week': f'{year}-W{week:02d}',
        'totals': review['totals'],
        'plans': [{k: p[k] for k in ('name', 'status', 'rounds', 'realized_pnl',
                                     'missed_buy', 'missed_sell')}
                  for p in review['plans']],
        'alerts': alerts,
    }
    try:
        return ai_service.weekly_review(ctx)
    except AINotConfigured as e:
        raise HTTPException(503, str(e))
    except Exception as e:  # noqa: BLE001
        raise HTTPException(502, f'AI 调用失败: {e}')


@app.get('/api/summary')
def summary():
    return trade_service.get_summary()


@app.get('/api/today')
def today(near_pct: float = 2.0):
    """今日视图：临近档位待办 + 三盏预警灯 + 组合摘要（执行感知）"""
    return today_service.today(near_pct=near_pct)


@app.get('/api/review')
def review(lookback_days: int = 250):
    """复盘统计：分计划回合/已实现/留存/纪律违约 + 组合合计"""
    prices = {}
    try:
        symbols = [p['symbol'] for p in trade_service.get_positions() if p['shares'] > 0]
        if symbols:
            prices = {s: q['close'] for s, q in etf_service.quotes(symbols).items()
                      if q and q.get('close')}
    except Exception as e:  # noqa: BLE001
        logger.warning('复盘页行情获取失败，降级为成本口径: %s', e)
    return review_service.review(lookback_days=lookback_days, prices=prices)


# ---------- 组合层（底仓/网格/现金三账户 + 资金流水） ----------

class FundFlowParams(BaseModel):
    flow_date: str  # YYYY-MM-DD
    direction: str  # deposit / withdraw
    amount: float = Field(gt=0)
    note: Optional[str] = None


@app.get('/api/portfolio')
def portfolio_overview(with_quotes: bool = True):
    """三账户总览：本金/现金/底仓/网格/留存 + 安全线（满格资金÷本金）"""
    prices = {}
    if with_quotes:
        try:
            symbols = [p['symbol'] for p in trade_service.get_positions() if p['shares'] > 0]
            if symbols:
                prices = {s: q['close'] for s, q in etf_service.quotes(symbols).items()
                          if q and q.get('close')}
        except Exception as e:  # noqa: BLE001 — 行情不可用则按成本口径返回
            logger.warning('组合页行情获取失败，降级为成本口径: %s', e)
    return portfolio_service.overview(prices)


@app.get('/api/portfolio/fund-flows')
def fund_flow_list():
    return portfolio_service.list_flows()


@app.post('/api/portfolio/fund-flows')
def fund_flow_add(p: FundFlowParams):
    try:
        return portfolio_service.add_flow(p.dict())
    except ValueError as e:
        raise HTTPException(400, str(e))


@app.delete('/api/portfolio/fund-flows/{flow_id}')
def fund_flow_delete(flow_id: int):
    if not portfolio_service.delete_flow(flow_id):
        raise HTTPException(404, '流水不存在')
    return {'ok': True}

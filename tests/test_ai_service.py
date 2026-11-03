"""AI 研判服务测试：prompt 组装、缓存、容错重试、结果归一化"""
import json

import pytest

from backend.config import settings
from backend.services.ai_service import (
    AINotConfigured, AIService, _build_plan_prompt, _build_prompt,
    _build_optimize_prompt,
)

_CTX = {
    'ts_code': '000300.SH', 'name': '沪深300', 'category': '宽基',
    'trade_date': '2026-07-20', 'pe_ttm': 12.3, 'pb': 1.3,
    'pe_median': 14.5, 'pb_median': 1.6,
    'percentiles': {'3y': {'pe': 15.0, 'pb': 10.0}, '5y': {'pe': 12.0, 'pb': None}},
    'ret_20d': -3.2, 'ret_60d': 5.1, 'ret_120d': -8.4, 'dist_52w_high': -12.3,
    'volatility': 22.3, 'score': 68.5, 'verdict': '适合开启',
}

_PLAN = {'id': 7, 'name': '红利网格', 'symbol': '510880', 'symbol_name': '红利ETF',
         'version': '2.0', 'base_price': 3.0, 'grid_step': 5, 'grid_count': 10,
         'amount_per_grid': 10000, 'step_increase': 10, 'profit_retention': 30,
         'created_at': '2026-07-01T00:00:00',
         'principal': 500000, 'cash': 180000, 'grid_full_capital': 220000,
         'plan_invested': 40000, 'remaining_need': 112000,
         'pressure_test': {'total_capital': 152000, 'lowest_price': 2.107,
                           'max_fall_pct': 29.8, 'max_unrealized_loss_pct': -8.5}}


@pytest.fixture
def svc(monkeypatch):
    monkeypatch.setattr(settings, 'AI_API_KEY', 'sk-test')
    return AIService()


class TestBuildPrompt:
    def test_includes_rich_context(self):
        p = _build_prompt(_CTX)
        assert '沪深300' in p and '宽基' in p
        assert 'PE_TTM 12.3（历史中位 14.5）' in p
        assert '3年 12.5%' in p      # PE/PB 分位取平均 (15+10)/2
        assert '5年 12.0%' in p      # PB 缺失时只用 PE
        assert '20日 -3.2%' in p and '距近一年高点 -12.3%' in p
        assert '规则引擎网格分 68.5/100' in p
        assert '不给出买卖指令' in p  # 研究助手定位，非投顾

    def test_missing_fields_degrade_gracefully(self):
        p = _build_prompt({'ts_code': 'X', 'name': '测试指数'})
        assert '测试指数' in p
        assert 'None' not in p


class TestPlanPrompt:
    def test_includes_plan_and_pressure(self):
        p = _build_plan_prompt(_PLAN)
        assert '红利网格' in p and '网格2.0' in p
        assert '逐格加码 10%' in p and '留利润 30%' in p
        assert '满格共需资金 152000 元' in p
        assert '最深一格跌至 2.107（距基准 29.8%）' in p
        assert '适合=计划稳健可执行' in p

    @pytest.mark.parametrize('key,label', [
        ('principal', '组合本金'),
        ('cash', '可用现金'),
        ('grid_full_capital', '全部网格剩余满格资金需求'),
        ('plan_invested', '当前计划已投入资金'),
        ('remaining_need', '当前计划剩余资金需求'),
    ])
    def test_missing_capital_forbids_affordability_conclusion(self, key, label):
        plan = {**_PLAN, key: None}
        p = _build_plan_prompt(plan)
        assert label in p
        assert '禁止判断资金承受力' in p
        assert '资金结论为数据不足' in p
        assert '只有核对以上五项数据后才允许判断资金承受力' not in p

    def test_complete_capital_context_is_required_and_zero_is_not_missing(self):
        p = _build_plan_prompt({**_PLAN, 'cash': 0})
        assert '组合本金 500000 元' in p and '可用现金 0 元' in p
        assert '当前计划已投入 40000 元，剩余需求 112000 元' in p
        assert '只有核对以上五项数据后才允许判断资金承受力' in p
        assert '禁止判断资金承受力' not in p

    def test_invalid_capital_value_does_not_unlock_affordability(self):
        p = _build_plan_prompt({**_PLAN, 'cash': float('nan')})
        assert '禁止判断资金承受力' in p
        assert '可用现金' in p


class TestNormalizeAndParse:
    def test_verdict_coerced_to_known_set(self):
        assert AIService._normalize({'verdict': '买入'})['verdict'] == '谨慎'
        assert AIService._normalize({'verdict': '适合'})['source'] == 'ai'

    def test_normalize_keeps_and_caps_detail(self):
        out = AIService._normalize({'verdict': '适合', 'detail': 'x' * 2000,
                                    'reasons': ['1', '2', '3', '4', '5', '6']})
        assert len(out['detail']) == 1200
        assert len(out['reasons']) == 5

    def test_parse_strips_wrapping_text(self):
        data = AIService._parse('好的，分析如下：{"verdict":"适合","oneLine":"x"} 以上')
        assert data['verdict'] == '适合'


def _fake_chat(svc, monkeypatch):
    calls = []

    def fake(messages):
        calls.append(messages)
        return json.dumps({'verdict': '适合', 'oneLine': '可以开网格',
                           'reasons': ['低估'], 'risks': ['破网'], 'paramHint': '5%'})
    monkeypatch.setattr(svc, '_chat', fake)
    return calls


class TestReviewFlow:
    def test_grid_review_caches_by_trade_date(self, svc, monkeypatch):
        calls = _fake_chat(svc, monkeypatch)
        r1 = svc.grid_review(_CTX)
        r2 = svc.grid_review(_CTX)
        assert r1['verdict'] == '适合' and r2 is r1  # 同日重复打开走缓存
        assert len(calls) == 1
        svc.grid_review({**_CTX, 'trade_date': '2026-07-21'})  # 数据更新后重新调用
        assert len(calls) == 2

    def test_plan_review_caches_by_plan(self, svc, monkeypatch):
        calls = _fake_chat(svc, monkeypatch)
        svc.plan_review(_PLAN)
        svc.plan_review(_PLAN)
        assert len(calls) == 1

    def test_plan_cache_changes_with_capital_context(self, svc, monkeypatch):
        calls = _fake_chat(svc, monkeypatch)
        svc.plan_review(_PLAN)
        svc.plan_review({**_PLAN, 'cash': _PLAN['cash'] - 50000})
        assert len(calls) == 2
        assert '可用现金 180000 元' in calls[0][1]['content']
        assert '可用现金 130000 元' in calls[1][1]['content']

    def test_not_configured_raises(self, monkeypatch):
        monkeypatch.setattr(settings, 'AI_API_KEY', '')
        with pytest.raises(AINotConfigured):
            AIService().grid_review(_CTX)


class TestOptimizeReview:
    _CTX = {
        'symbol': '510300', 'symbol_name': '沪深300ETF', 'n': 250,
        'amount': 10000, 'inc': 0, 'ret': 0,
        'cells': [
            {'step': 5, 'count': 8, 'ret': 3.2, 'dd': 1.1, 'trades': 11,
             'invested_pct': 35.0, 'low_activity': False, 'score': 2.7},
            {'step': 12, 'count': 14, 'ret': 4.1, 'dd': 0.5, 'trades': 1,
             'invested_pct': 2.1, 'low_activity': True, 'score': 3.9},
        ],
        'best': {'step': 12, 'count': 14, 'score': 3.9},
        'index': {'name': '沪深300', 'valuation_percentile': 87.9,
                  'verdict': '不建议（估值偏高）', 'volatility': 17.3},
        'safety_ratio': 0.42,
    }

    def test_prompt_includes_matrix_and_context(self):
        p = _build_optimize_prompt(self._CTX)
        assert '沪深300ETF' in p and '250 个交易日' in p
        assert '估值分位 87.9%' in p and '安全线当前为 42.0%' in p
        assert '5%×8: 3.2/1.1/11/35.0' in p
        assert '12%×14: 4.1/0.5/1/2.1（低活性）' in p
        assert '低活性' in p and '更像低频抄底而非网格' in p
        assert '费后结果和样本外验证' in p
        assert '缺少任一验证信息时' in p and '不得宣称稳健' in p
        assert '未提供 costs' in p and '未提供 validation' in p
        assert '本次缺少完整费后成本口径或时序样本外验证' in p

    def test_prompt_serializes_fee_and_out_of_sample_validation(self):
        ctx = {**self._CTX,
               'costs': {'fee_rate': 0.0003, 'min_fee': 5, 'slippage_rate': 0.001,
                         'fees': 0, 'slippage': 0, 'total': 0},
               'validation': {'method': 'chronological_holdout', 'train_ratio': 0.7,
                              'train_n': 175, 'validation_n': 75, 'train_end_idx': 174,
                              'validation_start_idx': 175, 'selection': 'validation_score'}}
        p = _build_optimize_prompt(ctx)
        assert '回测成本假设' in p and '"fee_rate": 0.0003' in p
        assert 'chronological_holdout' in p and '"validation_n": 75' in p
        assert 'train_* 为训练集结果' in p
        assert '本次已提供费后成本口径与时序样本外验证' in p
        invalid = _build_optimize_prompt({
            **ctx, 'validation': {**ctx['validation'], 'validation_n': 0}})
        assert '本次缺少完整费后成本口径或时序样本外验证' in invalid

    def test_normalize_and_cache(self, svc, monkeypatch):
        calls = _fake_chat_json(svc, monkeypatch,
                                {'step': 5, 'count': 8, 'oneLine': '选活跃网格',
                                 'reasons': ['成交 11 次'], 'warning': '高位易破网'})
        r1 = svc.optimize_review(self._CTX)
        r2 = svc.optimize_review(self._CTX)
        assert r1['step'] == 5.0 and r1['count'] == 8
        assert r1['oneLine'] == '选活跃网格'
        assert r1['warning'] == '高位易破网'
        assert r2 is r1 and len(calls) == 1  # 同参数走缓存

    def test_invalid_recommendation_snapped_back_to_matrix(self, svc, monkeypatch):
        """AI 返回矩阵外/非法参数时，降级为回测 best，不允许把 0/负参数透传前端"""
        _fake_chat_json(svc, monkeypatch,
                        {'step': 0, 'count': -3, 'oneLine': '乱推荐',
                         'reasons': ['幻觉'], 'warning': ''})
        r = svc.optimize_review(self._CTX)
        assert (r['step'], r['count']) == (12.0, 14)  # 回测 best
        assert '降级' in r['warning']

    def test_not_configured_raises(self, monkeypatch):
        monkeypatch.setattr(settings, 'AI_API_KEY', '')
        with pytest.raises(AINotConfigured):
            AIService().optimize_review(self._CTX)


def _fake_chat_json(svc, monkeypatch, payload):
    calls = []

    def fake(messages):
        calls.append(messages)
        return json.dumps(payload)
    monkeypatch.setattr(svc, '_chat', fake)
    return calls


class TestExitReview:
    _CTX = {
        'plan': {'id': 3, 'name': '证券网格', 'symbol': '512880',
                 'symbol_name': '证券ETF', 'status': 'active',
                 'base_price': 1.15, 'grid_step': 6, 'grid_count': 8,
                 'amount_per_grid': 8000},
        'position': {'shares': 12000, 'cost': 13200.0,
                     'market_value': 15600.0, 'unrealized_pnl': 2400.0},
        'rounds': 7, 'realized_pnl': 1850.0,
        'index': {'name': '证券Ⅱ', 'valuation_percentile': 83.5,
                  'verdict': '不建议（估值偏高）', 'volatility': 28.1,
                  'dist_52w_high': -2.4, 'trade_date': '2026-08-05'},
    }

    def test_prompt_covers_lifecycle(self):
        from backend.services.ai_service import _build_exit_prompt
        p = _build_exit_prompt(self._CTX)
        assert '证券网格' in p and '当前持仓 12000 份' in p
        assert '套利 7 回合' in p and '已实现收益 1850.0 元' in p
        assert '估值综合分位 83.5%' in p and '80% 以上为高估区' in p
        assert '低估开网 → 震荡套利 → 高估收网' in p

    def test_prompt_does_not_treat_missing_position_as_zero(self):
        from backend.services.ai_service import _build_exit_prompt
        p = _build_exit_prompt({'plan': self._CTX['plan']})
        assert '持仓数据缺失' in p and '不能按无持仓处理' in p
        assert 'ETF 现价缺失' in p and '估值综合分位缺失' in p
        assert 'verdict=数据不足、actions=[]' in p

    def test_prompt_accepts_explicit_zero_position_with_quote(self):
        from backend.services.ai_service import _build_exit_prompt
        p = _build_exit_prompt({**self._CTX, 'position': {'shares': 0}, 'current_price': 1.3})
        assert '已无持仓' in p and 'ETF 现价 1.3000' in p
        assert '持仓数据缺失' not in p

    def test_invalid_verdict_is_neutral_and_discards_actions(self):
        out = AIService._normalize_exit({'verdict': '卖出', 'oneLine': '立即卖出',
                                         'detail': '清仓', 'actions': ['a'] * 6})
        assert out['verdict'] == '数据不足'
        assert out['actions'] == []
        assert out['oneLine'] != '立即卖出' and out['detail'] != '清仓'
        assert AIService._normalize_exit({'verdict': '建议收网'})['verdict'] == '建议收网'

    @pytest.mark.parametrize('missing', ['position', 'index'])
    def test_missing_position_or_valuation_never_calls_ai(self, svc, monkeypatch, missing):
        ctx = {key: value for key, value in self._CTX.items() if key != missing}
        calls = _fake_chat_json(svc, monkeypatch,
                                {'verdict': '建议收网', 'oneLine': '立即收网',
                                 'actions': ['卖出全部持仓']})
        out = svc.exit_review(ctx)
        assert out['verdict'] == '数据不足'
        assert out['actions'] == []
        assert '不生成行动建议' in out['detail']
        assert calls == []

    def test_missing_quote_never_becomes_exit_action(self, svc, monkeypatch):
        ctx = {**self._CTX,
               'position': {**self._CTX['position'], 'market_value': None}}
        calls = _fake_chat_json(svc, monkeypatch,
                                {'verdict': '准备退出', 'actions': ['停止买入']})
        out = svc.exit_review(ctx)
        assert out['verdict'] == '数据不足'
        assert 'ETF 现价' in out['detail']
        assert out['actions'] == [] and calls == []

    def test_zero_position_still_requires_quote(self, svc, monkeypatch):
        ctx = {**self._CTX, 'position': {'shares': 0}}
        calls = _fake_chat_json(svc, monkeypatch,
                                {'verdict': '继续运行', 'actions': ['继续买入']})
        out = svc.exit_review(ctx)
        assert out['verdict'] == '数据不足'
        assert out['actions'] == [] and calls == []

    def test_cache_by_plan_and_trade_date(self, svc, monkeypatch):
        calls = _fake_chat_json(svc, monkeypatch,
                                {'verdict': '建议收网', 'oneLine': '高估收尾',
                                 'reasons': ['分位 83.5%'], 'actions': ['先停买单']})
        r1 = svc.exit_review(self._CTX)
        r2 = svc.exit_review(self._CTX)
        assert r1['verdict'] == '建议收网' and r2 is r1
        assert len(calls) == 1
        # 估值数据日期变化后重新调用
        svc.exit_review({**self._CTX,
                         'index': {**self._CTX['index'], 'trade_date': '2026-08-06'}})
        assert len(calls) == 2
        # 同一估值日期下，持仓或现价变化也不能复用旧退出结论
        svc.exit_review({**self._CTX,
                         'position': {**self._CTX['position'], 'shares': 10000,
                                      'market_value': 13000.0, 'unrealized_pnl': 0.0}})
        assert len(calls) == 3
        svc.exit_review({**self._CTX, 'current_price': 1.4})
        assert len(calls) == 4

    def test_not_configured_raises(self, monkeypatch):
        monkeypatch.setattr(settings, 'AI_API_KEY', '')
        with pytest.raises(AINotConfigured):
            AIService().exit_review(self._CTX)


class TestChatRetry:
    def test_retries_without_response_format_on_400(self, svc, monkeypatch):
        import backend.services.ai_service as mod
        calls = []

        class Resp:
            def __init__(self, status, body=None):
                self.status_code = status
                self._body = body or {}

            def raise_for_status(self):
                if self.status_code >= 400:
                    raise mod.requests.HTTPError(str(self.status_code))

            def json(self):
                return self._body

        def fake_post(url, json=None, headers=None, timeout=None):
            calls.append(dict(json))
            if len(calls) == 1:
                return Resp(400)  # 模拟不兼容服务拒绝 response_format
            return Resp(200, {'choices': [{'message': {'content': '{"verdict":"适合"}'}}]})

        monkeypatch.setattr(mod.requests, 'post', fake_post)
        content = svc._chat([{'role': 'user', 'content': 'hi'}])
        assert len(calls) == 2
        assert 'response_format' in calls[0]
        assert 'response_format' not in calls[1]
        assert json.loads(content)['verdict'] == '适合'

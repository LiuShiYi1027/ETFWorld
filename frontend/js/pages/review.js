// 复盘（杂志模式）：合计 KPI + AI 周报 + 分计划复盘 + 收益构成
import * as API from '../api.js';
import { store, toast, openPlan } from '../store.js';

function yuan(v) { return v == null ? '—' : '¥' + Math.round(v).toLocaleString('zh-CN'); }

export const reviewActions = {
  rv() { return store.reviewData; },
  rvKpis() {
    const t = store.reviewData ? store.reviewData.totals : null;
    if (!t) return [];
    const retained = store.portfolioData ? store.portfolioData.accounts.retained : null;
    return [
      { k: '套利回合', v: String(t.rounds), cls: 'pine', f: '买→卖 记 1 回合 · 全部计划' },
      { k: '已实现收益', v: yuan(t.realized_pnl), cls: 'pine', f: `含手续费 -${yuan(t.total_fee).slice(1)}` },
      { k: '留存底仓', v: retained && retained.market_value != null ? yuan(retained.market_value) : '—',
        cls: 'amber', f: '零成本份额市值 · 网格2.0' },
      { k: '纪律违约', v: `${t.missed_buy + t.missed_sell} 次`, cls: 'seal',
        f: `该买没买 ${t.missed_buy} · 该卖没卖 ${t.missed_sell}` },
    ];
  },
  planRows() { return store.reviewData ? store.reviewData.plans : []; },

  breakdown() {
    const rv = store.reviewData, pf = store.portfolioData;
    if (!rv || !pf) return [];
    const grid = Math.max(0, rv.totals.realized_pnl);
    const coreU = pf.accounts.core.positions.reduce((s, p) => s + (p.unrealized_pnl || 0), 0);
    const ret = pf.accounts.retained.market_value || 0;  // 留存成本为 0，市值即浮盈
    const total = grid + Math.max(0, coreU) + ret;
    if (!total) return [];
    const seg = [
      { name: '网格已实现', v: grid, color: '#86EFAC' },
      { name: '底仓浮盈', v: Math.max(0, coreU), color: '#93C5FD' },
      { name: '留存浮盈', v: ret, color: '#FCD34D' },
    ];
    return seg.map(s => ({ ...s, pct: s.v / total * 100,
      label: `${yuan(s.v)} · ${(s.v / total * 100).toFixed(0)}%` }));
  },

  async runWeekly() {
    if (store.weeklyLoading) return;
    if (store.weekly) { store.weekly = null; return; }  // 再点收起
    store.weeklyLoading = true;
    try {
      store.weekly = await API.post('/api/ai/weekly-review');
    } catch (e) {
      toast('AI 周报失败 · ' + e.message, 'warn');
    } finally { store.weeklyLoading = false; }
  },
};

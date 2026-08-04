// 机会：雷达评分表 + 指数详情抽屉（估值带/关键数据/AI 研判）
import * as API from '../api.js';
import { store, toast, switchTab } from '../store.js';

const PERIODS = [['3y', '3年'], ['5y', '5年'], ['10y', '10年'], ['all', '全历史']];

function bandColor(v) {
  return v < 20 ? '#15803D' : v < 40 ? '#65A30D' : v < 60 ? '#B45309' : v < 80 ? '#EA580C' : '#DC2626';
}
function drawerRow() {
  return store.readiness.find(r => r.ts_code === store.drawer) || null;
}
function closeDrawer() { store.drawer = null; }

export const picksActions = {
  radarRows() { return store.readiness; },
  chipCls(level) { return { go: 'go', maybe: 'maybe', wait: 'wait', no: 'no' }[level] || 'wait'; },
  openDrawer(tsCode) {
    store.drawer = tsCode;
    store.drawerAi = null;
    store.drawerAiLoading = false;
  },
  closeDrawer,
  drawerRow,
  drawerBands() {
    const r = drawerRow();
    if (!r || !r.percentiles) return [];
    return PERIODS.filter(([p]) => r.percentiles[p]).map(([p, label]) => {
      const e = r.percentiles[p];
      const vals = [e.pe, e.pb].filter(v => v != null);
      const v = vals.length ? vals.reduce((a, b) => a + b, 0) / vals.length : 0;
      return { label, v: Math.round(v * 10) / 10, color: bandColor(v) };
    });
  },
  drawerKv() {
    const r = drawerRow();
    if (!r) return [];
    const f = (v, d = 2) => v == null ? '—' : Number(v).toFixed(d);
    const pct = (v) => v == null ? '—' : (v > 0 ? '+' : '') + v + '%';
    return [
      ['PE_TTM', `${f(r.pe_ttm)} <small>中位 ${f(r.pe_median)}</small>`],
      ['PB', `${f(r.pb)} <small>中位 ${f(r.pb_median)}</small>`],
      ['年化波动', r.volatility != null ? r.volatility + '%' : '—'],
      ['距52周高点', pct(r.dist_52w_high)],
      ['20日动量', pct(r.ret_20d)], ['60日动量', pct(r.ret_60d)],
      ['120日动量', pct(r.ret_120d)],
      ['建议格距', r.suggested_grid ? r.suggested_grid.grid_step + '%' : '—'],
    ];
  },
  async runDrawerAi() {
    const r = drawerRow();
    if (!r || store.drawerAiLoading) return;
    store.drawerAiLoading = true;
    try {
      store.drawerAi = await API.post('/api/ai/grid-review', { ts_code: r.ts_code });
    } catch (e) {
      toast('AI 研判失败 · ' + e.message);
    } finally { store.drawerAiLoading = false; }
  },
  goPlanner() {
    const r = drawerRow();
    if (r) {
      store.plannerSeed = {
        ts_code: r.ts_code, name: r.name,
        step: r.suggested_grid ? r.suggested_grid.grid_step : 5,
      };
      store._seedApplied = false;
    }
    closeDrawer();
    switchTab('planner');
  },
};

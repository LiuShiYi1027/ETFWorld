// 规划：参数 → 档位表/压力测试 → 回测（echarts）→ 存为计划
import * as API from '../api.js';
import { store, toast, switchTab, loadPlans } from '../store.js';

function formParams() {
  const f = store.plannerForm;
  return {
    symbol: f.symbol, symbol_name: f.symbol_name, name: f.name || (f.symbol_name || '') + '网格',
    base_price: parseFloat(f.base_price), grid_step: parseFloat(f.grid_step),
    grid_count: parseInt(f.grid_count, 10), amount_per_grid: parseFloat(f.amount_per_grid),
    step_increase: parseFloat(f.step_increase || 0), profit_retention: parseFloat(f.profit_retention || 0),
  };
}
function validateForm() {
  const p = formParams();
  if (!p.symbol) return '请先选择标的';
  if (!(p.base_price > 0)) return '基准价必须大于 0';
  if (!(p.grid_step > 0 && p.grid_step <= 20)) return '格距需在 0-20% 之间';
  if (!(p.grid_count >= 2 && p.grid_count <= 30)) return '格数需在 2-30 之间';
  if (!(p.amount_per_grid > 0)) return '每格金额必须大于 0';
  return null;
}

async function pickEtf(e) {
  store.etfResults = [];
  store.etfQuery = '';
  store.plannerForm.symbol = e.ts_code || e.symbol || e.code;
  store.plannerForm.symbol_name = e.name || '';
  if (!store.plannerForm.name) store.plannerForm.name = (e.name || '') + '网格';
  try {
    const q = await API.get(`/api/quote?symbols=${encodeURIComponent(store.plannerForm.symbol)}`);
    const hit = q[store.plannerForm.symbol];
    if (hit && hit.close) store.plannerForm.base_price = hit.close;
  } catch { /* 无行情时保留手填 */ }
  preview();
}

async function preview() {
  const err = validateForm();
  if (err) { toast(err, 'warn'); return; }
  store.plannerLoading = true;
  try {
    store.plannerPreview = await API.post('/api/grid/preview', formParams());
    runBacktest();
  } catch (e) { toast('预览失败 · ' + e.message, 'warn'); }
  finally { store.plannerLoading = false; }
}

async function runBacktest() {
  const p = formParams();
  if (!p.symbol) return;
  store.btLoading = true;
  store.plannerBt = null;
  try {
    store.plannerBt = await API.post('/api/grid/backtest', { ...p, lookback_days: 250 });
    window.PetiteVue.nextTick(() => renderBtChart(store.plannerBt));
  } catch { store.plannerBt = { error: true }; }
  finally { store.btLoading = false; }
}

/* 回测净值曲线（echarts 按需初始化，失败静默） */
function renderBtChart(bt) {
  const el = document.getElementById('bt-equity');
  if (!el || !bt || !bt.g || !window.echarts) return;
  const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el);
  chart.setOption({
    animation: false,
    grid: { left: 46, right: 18, top: 16, bottom: 26 },
    tooltip: { trigger: 'axis' },
    xAxis: { type: 'category', data: bt.g.map((_, i) => i + 1), axisLabel: { fontSize: 10 } },
    yAxis: { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 } },
    series: [
      { name: '一直持有', type: 'line', data: bt.h, symbol: 'none', lineStyle: { color: '#A8A29E', width: 1.5 } },
      { name: '网格策略', type: 'line', data: bt.g, symbol: 'none', lineStyle: { color: '#15803D', width: 2 },
        areaStyle: { color: 'rgba(21,128,61,.08)' } },
    ],
  }, true);
  chart.resize();
}

export const plannerActions = {
  async searchEtf() {
    const q = store.etfQuery && store.etfQuery.trim();
    if (!q) { store.etfResults = []; return; }
    try { store.etfResults = await API.get(`/api/etf/search?q=${encodeURIComponent(q)}&limit=8`); }
    catch { store.etfResults = []; }
  },
  pickEtf,
  preview,
  runBacktest,
  plannerSymbolLabel() {
    const f = store.plannerForm;
    return f.symbol ? `${f.symbol_name || ''} ${f.symbol}` : '未选择';
  },
  async runOptimize() {
    const err = validateForm();
    if (err) { toast(err, 'warn'); return; }
    store.optLoading = true;
    store.plannerOpt = null; store.plannerOptAi = null;
    try {
      store.plannerOpt = await API.post('/api/grid/optimize', { ...formParams(), lookback_days: 250 });
    } catch (e) { toast('寻优失败 · ' + e.message, 'warn'); }
    finally { store.optLoading = false; }
  },
  optimizeRows() {
    if (!store.plannerOpt) return [];
    return [...store.plannerOpt.cells].sort((a, b) => b.score - a.score).slice(0, 10);
  },
  async runOptimizeAi() {
    if (store.optAiLoading) return;
    const err = validateForm();
    if (err) { toast(err, 'warn'); return; }
    store.optAiLoading = true;
    store.plannerOptAi = null;
    try {
      store.plannerOptAi = await API.post('/api/ai/optimize-review', {
        symbol: store.plannerForm.symbol, symbol_name: store.plannerForm.symbol_name,
        amount_per_grid: parseFloat(store.plannerForm.amount_per_grid),
        step_increase: parseFloat(store.plannerForm.step_increase || 0),
        profit_retention: parseFloat(store.plannerForm.profit_retention || 0),
      });
    } catch (e) { toast('AI 解读失败 · ' + e.message, 'warn'); }
    finally { store.optAiLoading = false; }
  },
  async savePlan() {
    const err = validateForm();
    if (err) { toast(err, 'warn'); return; }
    try {
      const r = await API.post('/api/grid/plans', formParams());
      toast(`已保存为计划 <b>#${r.id}</b>`);
      await loadPlans();
      switchTab('plans');
    } catch (e) { toast('保存失败 · ' + e.message, 'warn'); }
  },
  // 机会页带入选中标的（防重入：v-effect 会多次触发）
  async applySeed() {
    const seed = store.plannerSeed;
    if (!seed || store._seedApplied) return;
    store._seedApplied = true;
    store.plannerSeed = null;
    store.plannerForm.grid_step = seed.step || store.plannerForm.grid_step;
    try {
      const etfs = await API.get(`/api/etf/for/${encodeURIComponent(seed.ts_code)}`);
      if (etfs && etfs.length) pickEtf(etfs[0]);
      else toast(`未找到跟踪 ${seed.name} 的 ETF，请手动搜索`, 'warn');
    } catch { /* 忽略，手填 */ }
  },
};

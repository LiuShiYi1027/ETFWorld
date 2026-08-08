// 规划：参数 → 档位表/压力测试 → 回测（echarts）→ 存为计划
import * as API from '../api.js';
import { store, toast, switchTab, loadPlans } from '../store.js';

function formParams() {
  const f = store.plannerForm;
  return {
    symbol: f.symbol, symbol_name: f.symbol_name, name: f.name || (f.symbol_name || '') + '网格',
    base_price: parseFloat(f.base_price), grid_step: parseFloat(f.grid_step),
    grid_count: parseInt(f.grid_count, 10), grid_mode: f.grid_mode || 'amount',
    amount_per_grid: parseFloat(f.amount_per_grid),
    shares_per_grid: parseFloat(f.shares_per_grid),
    step_increase: parseFloat(f.step_increase || 0), profit_retention: parseFloat(f.profit_retention || 0),
  };
}
function validateForm() {
  const p = formParams();
  if (!p.symbol) return '请先选择标的';
  if (!(p.base_price > 0)) return '基准价必须大于 0';
  if (!(p.grid_step > 0 && p.grid_step <= 20)) return '格距需在 0-20% 之间';
  if (!(p.grid_count >= 2 && p.grid_count <= 30)) return '格数需在 2-30 之间';
  if (p.grid_mode === 'shares') {
    if (!(p.shares_per_grid >= 100)) return '等份额模式下每格份额需 ≥ 100（1 手）';
  } else if (!(p.amount_per_grid > 0)) return '每格金额必须大于 0';
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
    store.plannerBt = await API.post('/api/grid/backtest', { ...p, lookback_days: store.plannerLookback, anchor: store.plannerAnchor, compare_rebase: store.compareRebase });
    window.PetiteVue.nextTick(() => renderBtChart(store.plannerBt));
  } catch { store.plannerBt = { error: true }; }
  finally { store.btLoading = false; }
}

/* 回测净值曲线 + 买卖点标注（echarts 按需初始化，失败静默）
   左轴：收益率曲线（网格 vs 持有）；右轴：标的价格 + 买卖点三角标记 */
function renderBtChart(bt) {
  const el = document.getElementById('bt-equity');
  if (!el || !bt || !bt.g || !window.echarts) return;
  const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el);
  const dates = bt.dates && bt.dates.length === bt.n
    ? bt.dates.map(d => `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}`)
    : bt.g.map((_, i) => i + 1);
  const axisDates = bt.dates && bt.dates.length === bt.n
    ? bt.dates.map(d => `${d.slice(2, 4)}-${d.slice(4, 6)}`)
    : dates;
  const buys = [], sells = [];
  (bt.events || []).forEach(e => {
    (e.dir === 'buy' ? buys : sells).push({ value: [axisDates[e.i], e.price], level: e.level });
  });
  chart.setOption({
    animation: false,
    grid: { left: 46, right: 52, top: 18, bottom: 26 },
    tooltip: {
      trigger: 'axis',
      formatter: ps => {
        const i = ps[0].dataIndex;
        let s = `${dates[i]} · 价 ${bt.prices[i]}`;
        for (const p of ps) {
          if (p.seriesName === '买入' || p.seriesName === '卖出') continue;
          s += `<br/>${p.marker}${p.seriesName} <b>${p.value >= 0 ? '+' : ''}${p.value}%</b>`;
        }
        const dayBuys = buys.filter(b => b.value[0] === axisDates[i]);
        const daySells = sells.filter(s2 => s2.value[0] === axisDates[i]);
        dayBuys.forEach(b => { s += `<br/><b style="color:#15803D">买入 G${b.level} @ ${b.value[1]}</b>`; });
        daySells.forEach(s2 => { s += `<br/><b style="color:#B91C1C">卖出 G${s2.level} @ ${s2.value[1]}</b>`; });
        return s;
      },
    },
    xAxis: { type: 'category', data: axisDates,
             axisLabel: { fontSize: 10, interval: Math.max(9, Math.floor(bt.n / 10)) } },
    yAxis: [
      { type: 'value', axisLabel: { formatter: '{value}%', fontSize: 10 }, splitLine: { lineStyle: { color: '#F1EFEA' } } },
      { type: 'value', scale: true, axisLabel: { fontSize: 10, color: '#78716C' }, splitLine: { show: false } },
    ],
    series: [
      { name: '一直持有', type: 'line', data: bt.h, symbol: 'none', lineStyle: { color: '#A8A29E', width: 1.5 }, z: 2 },
      bt.rebase ? { name: '网格·自动重开', type: 'line', data: bt.rebase.g, symbol: 'none',
        lineStyle: { color: '#2563EB', width: 1.5, type: 'dashed' }, z: 2 } : null,
      { name: '价格', type: 'line', data: bt.prices, symbol: 'none', yAxisIndex: 1,
        lineStyle: { color: '#93C5FD', width: 1, type: 'dashed' }, z: 1 },
      { name: '网格策略', type: 'line', data: bt.g, symbol: 'none', lineStyle: { color: '#15803D', width: 2 },
        areaStyle: { color: 'rgba(21,128,61,.08)' }, z: 3 },
      { name: '买入', type: 'scatter', data: buys, yAxisIndex: 1, symbol: 'triangle', symbolSize: 9,
        itemStyle: { color: '#15803D', borderColor: '#fff', borderWidth: 1 }, z: 4 },
      { name: '卖出', type: 'scatter', data: sells, yAxisIndex: 1, symbol: 'triangle', symbolRotate: 180,
        symbolSize: 9, itemStyle: { color: '#B91C1C', borderColor: '#fff', borderWidth: 1 }, z: 4 },
    ].filter(Boolean),
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
  setGridMode(m) {
    store.plannerForm.grid_mode = m;
    if (store.plannerPreview) preview();  // 已预览过就按新模式重算
  },
  setLookback(d) {
    store.plannerLookback = d;
    if (store.plannerPreview) runBacktest();  // 已预览过就自动重跑
  },
  setAnchor(a) {
    store.plannerAnchor = a;
    if (store.plannerPreview) runBacktest();
  },
  toggleCompareRebase() {
    store.compareRebase = !store.compareRebase;
    if (store.plannerPreview) runBacktest();
  },
  // 回合明细：把卖出事件与同一格最近一笔买入配对
  btRounds() {
    const bt = store.plannerBt;
    if (!bt || !bt.events) return [];
    const lastBuy = {};
    const rounds = [];
    const dates = bt.dates || [];
    const fd = i => {
      const d = dates[i];
      return d ? `${d.slice(0, 4)}-${d.slice(4, 6)}-${d.slice(6, 8)}` : `第${i + 1}日`;
    };
    for (const e of bt.events) {
      if (e.dir === 'buy') { lastBuy[e.level] = e; continue; }
      const b = lastBuy[e.level];
      if (!b) continue;
      rounds.push({
        level: e.level, buyDate: fd(b.i), sellDate: fd(e.i),
        buy: b.price, sell: e.price,
        pct: ((e.price / b.price - 1) * 100).toFixed(1),
      });
      delete lastBuy[e.level];
    }
    return rounds.slice(-8).reverse();  // 最近 8 回合，新→旧
  },
  plannerSymbolLabel() {
    const f = store.plannerForm;
    return f.symbol ? `${f.symbol_name || ''} ${f.symbol}` : '未选择';
  },
  async runOptimize() {
    if (store.plannerForm.grid_mode === 'shares') {
      toast('参数寻优暂按等金额口径 · 请切回等金额后寻优', 'warn'); return;
    }
    const err = validateForm();
    if (err) { toast(err, 'warn'); return; }
    store.optLoading = true;
    store.plannerOpt = null; store.plannerOptAi = null;
    try {
      store.plannerOpt = await API.post('/api/grid/optimize', { ...formParams(), lookback_days: store.plannerLookback });
    } catch (e) { toast('寻优失败 · ' + e.message, 'warn'); }
    finally { store.optLoading = false; }
  },
  optimizeRows() {
    if (!store.plannerOpt) return [];
    return [...store.plannerOpt.cells].sort((a, b) => b.score - a.score).slice(0, 10);
  },
  async runOptimizeAi() {
    if (store.optAiLoading) return;
    if (store.plannerForm.grid_mode === 'shares') {
      toast('参数寻优暂按等金额口径 · 请切回等金额后寻优', 'warn'); return;
    }
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
        lookback_days: store.plannerLookback,
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

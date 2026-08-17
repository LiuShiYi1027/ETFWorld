// 定投：计划管理（计划页区块）+ 今天页定投待办
import * as API from '../api.js';
import { nextTick, store, toast, loadDcaPlans, loadToday } from '../store.js';

// 建议倍数 chip 的样式与文案（数据来自服务端 suggestion，这里只管展示）
function sugChip(s) {
  if (!s) return { text: '—', cls: 'wait' };
  if (s.action === 'profit_take') return { text: s.label, cls: 'no' };
  if (s.action === 'pause') return { text: s.label, cls: 'no' };
  if (s.multiplier === 1 && s.valuation_pct == null) return { text: '1× 未关联指数', cls: 'wait' };
  const cls = s.multiplier > 1 ? 'go' : s.multiplier < 1 ? 'maybe' : 'wait';
  return { text: `${s.multiplier}× ${s.label}`, cls };
}

export const dcaActions = {
  // ---- 计划页：定投计划区块 ----
  dcaRows() { return store.dcaPlans; },
  dcaSugChip(p) { return sugChip(p.suggestion); },
  dcaFreqLabel(p) { return p.frequency === 'monthly' ? '每月' : '每周'; },

  openDcaCreate() {
    store.dcaForm = {
      symbol: '', symbol_name: '', name: '', base_amount: 1000,
      frequency: 'weekly', query: '', results: [],
    };
    store.modal = 'dca';
  },
  async dcaSearch() {
    const q = store.dcaForm.query && store.dcaForm.query.trim();
    if (!q) { store.dcaForm.results = []; return; }
    try { store.dcaForm.results = await API.get(`/api/etf/search?q=${encodeURIComponent(q)}&limit=6`); }
    catch { store.dcaForm.results = []; }
  },
  dcaPick(e) {
    store.dcaForm.symbol = e.ts_code || '';
    store.dcaForm.symbol_name = e.name || '';
    if (!store.dcaForm.name) store.dcaForm.name = (e.name || '') + '定投';
    store.dcaForm.results = [];
    store.dcaForm.query = `${e.name} ${e.ts_code}`;
  },
  async saveDcaPlan() {
    const f = store.dcaForm;
    if (!f.symbol) { toast('请先搜索并选择标的', 'warn'); return; }
    if (!(parseFloat(f.base_amount) > 0)) { toast('基准金额必须大于 0', 'warn'); return; }
    try {
      await API.post('/api/dca/plans', {
        name: f.name || undefined, symbol: f.symbol, symbol_name: f.symbol_name,
        base_amount: parseFloat(f.base_amount), frequency: f.frequency,
      });
      store.modal = null;
      toast('定投计划已创建 · 待办会在定投周期出现');
      await loadDcaPlans(); loadToday();
    } catch (e) { toast('保存失败 · ' + e.message, 'warn'); }
  },
  async dcaSetStatus(p, status) {
    await API.patch(`/api/dca/plans/${p.id}/status?status=${status}`);
    toast(status === 'paused' ? '已暂停' : status === 'active' ? '已恢复' : '已结束');
    await loadDcaPlans(); loadToday();
  },
  async delDcaPlan(p, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/dca/plans/${p.id}`);
      toast(`定投计划 <b>${p.name}</b> 已删除（成交记录保留）`);
      await loadDcaPlans(); loadToday();
    } else {
      btn.dataset.armed = '1'; btn.textContent = '确认删除?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删除'; }, 3500);
    }
  },

  // ---- 定投详情 ----
  async openDcaDetail(p) {
    store.dcaDetail = await API.get(`/api/dca/plans/${p.id}`);
    store.dcaDetailTrades = await API.get(`/api/trades?dca_plan_id=${p.id}`);
    store.dcaBt = null;
    runDcaBacktest(store.dcaLookback);
    nextTick(() => {
      const el = document.getElementById('dca-detail');
      if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
    });
  },
  closeDcaDetail() { store.dcaDetail = null; store.dcaBt = null; },
  runDcaBacktest,

  // ---- 今天页：定投待办 ----
  dcaList() {
    if (!store.todayData) return [];
    return (store.todayData.dca_todos || [])
      .filter(t => !store.dismissedTodos[`dca-${t.dca_plan_id}-${t.period_key}`]);
  },
  dcaChip(t) {
    if (t.action === 'profit_take') return { text: '止盈提示', cls: 'no' };
    if (t.action === 'pause') return { text: '停投', cls: 'no' };
    const cls = t.multiplier > 1 ? 'go' : t.multiplier < 1 ? 'maybe' : 'wait';
    return { text: `${t.multiplier}×`, cls };
  },
  // 「已投」：取现价预填成交单（份额按整手换算），确认即记成交并消待办
  async completeDca(t) {
    let price = '';
    try {
      const q = await API.get(`/api/quote?symbols=${encodeURIComponent(t.symbol)}`);
      const hit = q[t.symbol];
      if (hit && hit.close) price = hit.close;
    } catch { /* 无行情则手填 */ }
    store.tradeForm = {
      plan_id: null, dca_plan_id: t.dca_plan_id, dca_period_key: t.period_key,
      symbol: t.symbol, symbol_name: t.symbol_name,
      trade_date: new Date().toISOString().slice(0, 10),
      direction: 'buy',
      price,
      shares: price ? Math.floor(t.amount / price / 100) * 100 : '',
      fee: '', note: `定投·第${(t.periods_done || 0) + 1}期`,
    };
    store.modal = 'trade';
  },
  dismissDca(t) {
    store.dismissedTodos[`dca-${t.dca_plan_id}-${t.period_key}`] = true;
    toast('已标记 · 本期定投稍后处理');
  },
};

// ---- 回测（普通定投 vs 估值增强，同一标的同一段历史） ----
async function runDcaBacktest(days) {
  const d = store.dcaDetail;
  if (!d || store.dcaBtLoading) return;
  store.dcaLookback = days;
  store.dcaBtLoading = true;
  try {
    store.dcaBt = await API.post('/api/dca/backtest', {
      symbol: d.symbol, symbol_name: d.symbol_name,
      base_amount: d.base_amount, frequency: d.frequency, lookback_days: days,
    });
    nextTick(() => renderDcaChart(store.dcaBt));
  } catch (e) {
    store.dcaBt = null;
    toast('回测失败 · ' + e.message, 'warn');
  } finally { store.dcaBtLoading = false; }
}

function renderDcaChart(bt) {
  const el = document.getElementById('dca-bt');
  if (!el || !bt || !window.echarts) return;
  const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el);
  const dates = bt.enhanced.dates.map(d => `${d.slice(2, 4)}-${d.slice(4, 6)}`);
  chart.setOption({
    animation: false,
    grid: { left: 56, right: 16, top: 26, bottom: 24 },
    tooltip: { trigger: 'axis' },
    legend: { top: 0, textStyle: { fontSize: 11, color: '#78716C' }, itemWidth: 14 },
    xAxis: { type: 'category', data: dates,
             axisLabel: { fontSize: 10, interval: Math.max(9, Math.floor(dates.length / 8)) } },
    yAxis: { type: 'value', axisLabel: { fontSize: 10, formatter: v => (v / 10000) + '万' },
             splitLine: { lineStyle: { color: '#F1EFEA' } } },
    series: [
      { name: '增强投入', type: 'line', data: bt.enhanced.cost, symbol: 'none',
        lineStyle: { color: '#A8A29E', width: 1, type: 'dashed' }, z: 1 },
      { name: '普通定投市值', type: 'line', data: bt.plain.value, symbol: 'none',
        lineStyle: { color: '#2563EB', width: 1.5 }, z: 2 },
      { name: '增强定投市值', type: 'line', data: bt.enhanced.value, symbol: 'none',
        lineStyle: { color: '#15803D', width: 2 },
        areaStyle: { color: 'rgba(21,128,61,.07)' }, z: 3 },
    ],
  }, true);
  chart.resize();
}

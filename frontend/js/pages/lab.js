// 实验室：单品种策略对擂 + 轮动沙盒 + 研究笔记
import * as API from '../api.js';
import { nextTick, store, toast, loadLabNotes } from '../store.js';

const DEFAULT_POOL = [
  { symbol: '510300.SH', symbol_name: '沪深300ETF' },
  { symbol: '510500.SH', symbol_name: '中证500ETF' },
  { symbol: '159915.SZ', symbol_name: '创业板ETF' },
  { symbol: '510880.SH', symbol_name: '红利ETF' },
  { symbol: '511010.SH', symbol_name: '国债ETF' },
];

async function runCompare() {
  const f = store.labForm;
  if (!f.symbol) { toast('请先搜索并选择标的', 'warn'); return; }
  if (!f.strategies.length) { toast('至少勾选一个策略', 'warn'); return; }
  store.labLoading = true;
  store.labResult = null;
  try {
    const spec = {
      kind: 'single', symbol: f.symbol, symbol_name: f.symbol_name,
      strategies: f.strategies,
      grid: { grid_step: parseFloat(f.grid_step), grid_count: parseInt(f.grid_count, 10),
              amount_per_grid: parseFloat(f.amount_per_grid) },
      dca: { base_amount: parseFloat(f.base_amount), frequency: f.frequency },
      lookback_days: f.lookback, budget: 100000,
    };
    store.labResult = await API.post('/api/lab/compare', spec);
    store.labResult._spec = spec;
    nextTick(() => renderLabChart('lab-chart', store.labResult));
  } catch (e) { toast('对比失败 · ' + e.message, 'warn'); }
  finally { store.labLoading = false; }
}

async function runRotation() {
  const f = store.rotForm;
  if (f.pool.length < 2) { toast('品种池至少 2 只', 'warn'); return; }
  store.rotLoading = true;
  store.rotResult = null;
  try {
    const spec = {
      kind: 'rotation', pool: f.pool, window: f.window,
      rebalance: f.rebalance, lookback_days: f.lookback, budget: 100000,
    };
    store.rotResult = await API.post('/api/lab/compare', spec);
    store.rotResult._spec = spec;
    nextTick(() => renderLabChart('rot-chart', store.rotResult));
  } catch (e) { toast('回测失败 · ' + e.message, 'warn'); }
  finally { store.rotLoading = false; }
}

function renderLabChart(elId, result) {
  const el = document.getElementById(elId);
  if (!el || !result || !window.echarts) return;
  const chart = window.echarts.getInstanceByDom(el) || window.echarts.init(el);
  const dates = result.dates.map(d => `${d.slice(2, 4)}-${d.slice(4, 6)}`);
  chart.setOption({
    animation: false,
    grid: { left: 46, right: 16, top: 30, bottom: 24 },
    tooltip: { trigger: 'axis',
      formatter: ps => {
        let s = dates[ps[0].dataIndex];
        for (const p of ps) s += `<br/>${p.marker}${p.seriesName} <b>${(p.value - 1).toFixed(2)}%</b>`;
        return s;
      } },
    legend: { top: 0, textStyle: { fontSize: 11, color: '#78716C' }, itemWidth: 14 },
    xAxis: { type: 'category', data: dates,
             axisLabel: { fontSize: 10, interval: Math.max(9, Math.floor(dates.length / 10)) } },
    yAxis: { type: 'value', scale: true,
             axisLabel: { fontSize: 10, formatter: v => ((v - 1) * 100).toFixed(0) + '%' },
             splitLine: { lineStyle: { color: '#F1EFEA' } } },
    series: result.series.map(s => ({
      name: s.name, type: 'line', data: s.nav, symbol: 'none',
      lineStyle: { color: s.color, width: s.key === 'rotation' || s.key === 'grid' ? 2 : 1.3 },
    })),
  }, true);
  chart.resize();
}

function statsRows(result) {
  return (result && result.series) || [];
}

export const labActions = {
  statsRows,

  // ---- 单品种对擂 ----
  async labSearch() {
    const q = store.labForm.query && store.labForm.query.trim();
    if (!q) { store.labForm.results = []; return; }
    try { store.labForm.results = await API.get(`/api/etf/search?q=${encodeURIComponent(q)}&limit=6`); }
    catch { store.labForm.results = []; }
  },
  labPick(e) {
    const f = store.labForm;
    f.symbol = e.ts_code; f.symbol_name = e.name;
    f.query = `${e.name} ${e.ts_code}`; f.results = [];
  },
  toggleStrategy(k) {
    const arr = store.labForm.strategies;
    const i = arr.indexOf(k);
    if (i >= 0) arr.splice(i, 1); else arr.push(k);
  },
  setLabLookback(d) { store.labForm.lookback = d; if (store.labResult) runCompare(); },
  runCompare,

  // ---- 轮动沙盒 ----
  async rotSearch() {
    const q = store.rotForm.query && store.rotForm.query.trim();
    if (!q) { store.rotForm.results = []; return; }
    try { store.rotForm.results = await API.get(`/api/etf/search?q=${encodeURIComponent(q)}&limit=6`); }
    catch { store.rotForm.results = []; }
  },
  rotPick(e) {
    const f = store.rotForm;
    if (f.pool.length >= 6) { toast('品种池最多 6 只', 'warn'); return; }
    if (f.pool.some(p => p.symbol === e.ts_code)) { toast('已在池中', 'warn'); return; }
    f.pool.push({ symbol: e.ts_code, symbol_name: e.name });
    f.query = ''; f.results = [];
  },
  rotRemove(sym) {
    store.rotForm.pool = store.rotForm.pool.filter(p => p.symbol !== sym);
  },
  rotResetPool() { store.rotForm.pool = DEFAULT_POOL.map(p => ({ ...p })); },
  setRotWindow(w) { store.rotForm.window = w; if (store.rotResult) runRotation(); },
  setRotRebalance(r) { store.rotForm.rebalance = r; if (store.rotResult) runRotation(); },
  setRotLookback(d) { store.rotForm.lookback = d; if (store.rotResult) runRotation(); },
  runRotation,

  // ---- 研究笔记 ----
  openNoteSave(kind) {
    const r = kind === 'single' ? store.labResult : store.rotResult;
    if (!r) { toast('先跑一轮回测', 'warn'); return; }
    const stats = {};
    for (const s of r.series) stats[s.name] = s.stats;
    store.labNoteForm = {
      kind, title: '', note: '',
      spec: r._spec, stats,
      _default: kind === 'single'
        ? `${r.symbol_name || r.symbol} ${r.series.map(s => s.name).join('/')}`
        : `轮动 ${r.pool.map(p => p.symbol_name).join('+')} · ${r.window}日`,
    };
    store.modal = 'labnote';
  },
  async saveLabNote() {
    const f = store.labNoteForm;
    if (!f.title.trim()) { toast('起个标题', 'warn'); return; }
    try {
      await API.post('/api/lab/notes', {
        title: f.title.trim(), note: f.note || undefined,
        spec: f.spec, stats: f.stats,
      });
      store.modal = null;
      toast('已存入研究笔记');
      loadLabNotes();
    } catch (e) { toast('保存失败 · ' + e.message, 'warn'); }
  },
  async delLabNote(n, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/lab/notes/${n.id}`);
      toast('笔记已删除');
      loadLabNotes();
    } else {
      btn.dataset.armed = '1'; btn.textContent = '确认?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删'; }, 3500);
    }
  },
  async replayNote(n) {
    // 按 spec 重放：单品种 → 卡片 A；轮动 → 卡片 B
    const spec = n.spec || {};
    if (spec.kind === 'rotation') {
      Object.assign(store.rotForm, {
        pool: spec.pool || [], window: spec.window || 20,
        rebalance: spec.rebalance || 'weekly', lookback: spec.lookback_days || 750,
      });
      await runRotation();
    } else {
      Object.assign(store.labForm, {
        symbol: spec.symbol, symbol_name: spec.symbol_name,
        query: `${spec.symbol_name || ''} ${spec.symbol || ''}`,
        strategies: spec.strategies || ['hold', 'grid', 'dca'],
        lookback: spec.lookback_days || 750,
      });
      if (spec.grid) Object.assign(store.labForm, {
        grid_step: spec.grid.grid_step, grid_count: spec.grid.grid_count,
        amount_per_grid: spec.grid.amount_per_grid });
      if (spec.dca) Object.assign(store.labForm, {
        base_amount: spec.dca.base_amount, frequency: spec.dca.frequency });
      await runCompare();
    }
    window.scrollTo({ top: 0, behavior: 'smooth' });
  },
  noteStatsLine(n) {
    const s = n.stats || {};
    return Object.entries(s).map(([k, v]) =>
      `${k} ${v && v.ret != null ? (v.ret >= 0 ? '+' : '') + v.ret + '%' : '—'}`).join(' · ');
  },
};

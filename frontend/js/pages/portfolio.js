// 组合：三账户视图 + 安全线 + 底仓持仓 + 资金流水
import * as API from '../api.js';
import { store, toast, loadPortfolio, switchTab } from '../store.js';

function wan(v) { return v == null ? '—' : '¥' + (v / 10000).toFixed(1) + '万'; }
function yuan(v) { return v == null ? '—' : '¥' + Math.round(v).toLocaleString('zh-CN'); }

export const portfolioActions = {
  pf() { return store.portfolioData; },
  pfKpis() {
    const d = store.portfolioData;
    if (!d) return [];
    const coreMv = d.accounts.core.market_value, gridMv = d.accounts.grid.market_value;
    const total = (coreMv || 0) + (gridMv || 0) + (d.cash || 0);
    return [
      { k: '本金', v: wan(d.principal), f: '入金 − 出金' },
      { k: '总资产', v: wan(total), f: d.principal ? `vs 本金 ${total >= d.principal ? '+' : ''}${((total / d.principal - 1) * 100).toFixed(1)}%` : '—',
        good: total >= d.principal },
      { k: '底仓', v: wan(d.accounts.core.market_value ?? d.accounts.core.cost), f: `${d.accounts.core.positions.length} 只 · 含留存` },
      { k: '网格', v: wan(d.accounts.grid.market_value ?? d.accounts.grid.cost), f: `${d.accounts.grid.positions.length} 个持仓计划` },
      { k: '现金', v: wan(d.cash), f: '= 本金 − 持仓成本' },
    ];
  },
  // 三账户占比条（含留存拆出）
  acctSegments() {
    const d = store.portfolioData;
    if (!d) return [];
    const core = d.accounts.core.market_value ?? d.accounts.core.cost;
    const gridAll = d.accounts.grid.market_value ?? d.accounts.grid.cost;
    const ret = d.accounts.retained.market_value ?? 0;
    const grid = Math.max(0, gridAll - ret);
    const cash = Math.max(0, d.cash);
    const total = core + grid + ret + cash;
    if (!total) return [];
    const seg = [
      { name: '底仓', v: core, color: '#93C5FD' },
      { name: '网格持仓', v: grid, color: '#86EFAC' },
      { name: '留存底仓（免费）', v: ret, color: '#FCD34D' },
      { name: '现金', v: cash, color: '#D6D3CB' },
    ];
    return seg.map(s => ({ ...s, pct: s.v / total * 100, label: `${wan(s.v)} · ${(s.v / total * 100).toFixed(1)}%` }));
  },
  safety() {
    const d = store.portfolioData;
    if (!d || !d.principal) return null;
    return {
      ratioPct: Math.round((d.safety_ratio || 0) * 100),
      full: yuan(d.grid_full_capital), principal: yuan(d.principal),
      warn: d.safety_warn,
      gap: yuan(Math.max(0, d.grid_full_capital - d.cash)),
      cash: yuan(d.cash),
    };
  },
  coreRows() { return store.portfolioData ? store.portfolioData.accounts.core.positions : []; },
  retainedRows() { return store.portfolioData ? store.portfolioData.accounts.retained.items : []; },
  flowRows() { return store.fundFlows; },

  // 行业分布（占比条复用 acct 样式）；超过 40% 警告线的桶标红
  industrySegments() {
    const d = store.portfolioData;
    if (!d || !d.industries || !d.industries.length) return [];
    const palette = ['#93C5FD', '#86EFAC', '#FCD34D', '#FDA4AF', '#C4B5FD', '#67E8F9', '#D6D3CB'];
    return d.industries.map((b, i) => ({
      name: b.name, pct: b.pct,
      color: b.pct > 40 ? '#F87171' : palette[i % palette.length],
      label: `${wan(b.market_value)} · ${b.pct}%`,
      over: b.pct > 40,
    }));
  },
  industryWarn() {
    const d = store.portfolioData;
    return !!(d && d.concentration_warn);
  },

  // 资金分配建议
  alloc() { return store.allocation; },
  gotoPick(ts) {
    switchTab('picks');
    if (ts) store.drawer = ts;
  },

  openFundFlow() {
    store.flowForm = {
      flow_date: new Date().toISOString().slice(0, 10),
      direction: 'deposit', amount: '', note: '',
    };
    store.modal = 'fundflow';
  },
  async saveFundFlow() {
    const f = store.flowForm;
    if (!(parseFloat(f.amount) > 0)) { toast('金额必须大于 0', 'warn'); return; }
    try {
      await API.post('/api/portfolio/fund-flows', {
        flow_date: f.flow_date, direction: f.direction,
        amount: parseFloat(f.amount), note: f.note || undefined,
      });
      store.modal = null;
      toast('资金流水已保存 · 安全线已更新');
      await loadPortfolio(); // 影响今日页摘要
    } catch (e) { toast(e.message, 'warn'); }
  },
  async delFlow(f, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/portfolio/fund-flows/${f.id}`);
      toast('流水已删除');
      await loadPortfolio();
    } else {
      btn.dataset.armed = '1'; btn.textContent = '确认?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删'; }, 3500);
    }
  },
  fmtYuan: yuan,
};

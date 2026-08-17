// 轮动：计划管理（计划页区块）+ 今天页调仓待办
import * as API from '../api.js';
import { store, toast, nextTick, loadToday, loadRotationPlans } from '../store.js';

function nextTickScroll(id) {
  nextTick(() => {
    const el = document.getElementById(id);
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

export const rotationActions = {
  // ---- 计划页：轮动计划区块 ----
  rotPlanRows() { return store.rotationPlans; },
  rotPoolLabel(p) { return (p.pool || []).map(x => x.symbol_name || x.symbol).join(' / '); },

  openRotCreate() {
    store.rotPlanForm = {
      name: '', pool: [], query: '', results: [],
      window: 20, rebalance: 'weekly',
    };
    store.modal = 'rotation';
  },
  async rotPlanSearch() {
    const q = store.rotPlanForm.query && store.rotPlanForm.query.trim();
    if (!q) { store.rotPlanForm.results = []; return; }
    try { store.rotPlanForm.results = await API.get(`/api/etf/search?q=${encodeURIComponent(q)}&limit=6`); }
    catch { store.rotPlanForm.results = []; }
  },
  rotPlanPick(e) {
    const f = store.rotPlanForm;
    if (f.pool.length >= 6) { toast('品种池最多 6 只', 'warn'); return; }
    if (f.pool.some(p => p.symbol === e.ts_code)) { toast('已在池中', 'warn'); return; }
    f.pool.push({ symbol: e.ts_code, symbol_name: e.name });
    f.query = ''; f.results = [];
    if (!f.name) f.name = '轮动·' + f.pool.map(p => p.symbol_name).join('+');
  },
  rotPlanRemove(sym) {
    store.rotPlanForm.pool = store.rotPlanForm.pool.filter(p => p.symbol !== sym);
  },
  async saveRotPlan() {
    const f = store.rotPlanForm;
    if (f.pool.length < 2) { toast('品种池至少 2 只', 'warn'); return; }
    try {
      await API.post('/api/rotation/plans', {
        name: f.name || '轮动计划', pool: f.pool,
        window: f.window, rebalance: f.rebalance,
      });
      store.modal = null;
      toast('轮动计划已创建 · 调仓待办会出现在今天页');
      await loadRotationPlans(); loadToday();
    } catch (e) { toast('保存失败 · ' + e.message, 'warn'); }
  },
  async rotSetStatus(p, status) {
    await API.patch(`/api/rotation/plans/${p.id}/status?status=${status}`);
    toast(status === 'paused' ? '已暂停' : status === 'active' ? '已恢复' : '已结束');
    await loadRotationPlans(); loadToday();
  },
  async delRotPlan(p, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/rotation/plans/${p.id}`);
      toast(`轮动计划 <b>${p.name}</b> 已删除（成交记录保留）`);
      await loadRotationPlans(); loadToday();
    } else {
      btn.dataset.armed = '1'; btn.textContent = '确认删除?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删除'; }, 3500);
    }
  },

  // ---- 轮动详情 ----
  async openRotDetail(p) {
    store.rotDetail = await API.get(`/api/rotation/plans/${p.id}`);
    store.rotDetailTrades = await API.get(`/api/trades?rotation_plan_id=${p.id}`);
    nextTickScroll('rot-detail');
  },
  closeRotDetail() { store.rotDetail = null; },

  // ---- 今天页：调仓待办 ----
  rotList() {
    return (store.todayData && store.todayData.rotation_todos) || [];
  },
  rotActionLabel(t) {
    return { switch: '调仓', exit: '清仓', enter: '建仓' }[t.action] || t.action;
  },
  // 记卖出：预填当前持仓全卖；记买入：预填目标品种（份额按实际所得手填）
  async rotRecord(t, direction) {
    const isSell = direction === 'sell';
    const leg = isSell ? t.holding : t.target;
    let price = '';
    try {
      const q = await API.get(`/api/quote?symbols=${encodeURIComponent(leg.symbol)}`);
      const hit = q[leg.symbol];
      if (hit && hit.close) price = hit.close;
    } catch { /* 无行情则手填 */ }
    store.tradeForm = {
      plan_id: null, rotation_plan_id: t.rotation_plan_id,
      symbol: leg.symbol, symbol_name: leg.symbol_name,
      trade_date: new Date().toISOString().slice(0, 10),
      direction,
      price,
      shares: isSell && t.holding ? t.holding.shares : '',
      fee: '', note: `轮动调仓·${t.period_label}·${isSell ? '卖出腿' : '买入腿'}`,
    };
    store.modal = 'trade';
  },
};

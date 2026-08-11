// 计划：列表 + 详情（棋盘/标尺/破网处置/AI 体检/成交录入）
import * as API from '../api.js';
import { store, toast, loadPlans, loadPlanDetail, openPlan, loadToday, loadDcaPlans } from '../store.js';

const CELL_LABEL = { wait: '待买', hold: '持有', sold: '已卖', keep: '留存' };

async function runPlanAi() {
  const d = store.detail;
  if (!d || store.planAiLoading) return;
  store.planAiLoading = true;
  store.planAi = null;
  try {
    store.planAi = await API.post('/api/ai/plan-review', { plan_id: d.id });
  } catch (e) {
    toast('AI 体检失败 · ' + e.message);
  } finally { store.planAiLoading = false; }
}

async function runExitAi() {
  const d = store.detail;
  if (!d || store.exitAiLoading) return;
  store.exitAiLoading = true;
  store.exitAi = null;
  try {
    store.exitAi = await API.post('/api/ai/exit-review', { plan_id: d.id });
  } catch (e) {
    toast('AI 退出研判失败 · ' + e.message);
  } finally { store.exitAiLoading = false; }
}

// 计划关联监控指数的估值分位（名称子串匹配，与后端 today 预警同口径）；
// 仅在 ≥70% 时返回（列表 chip 只提示退出区）
function planValuation(p) {
  if (!store.readiness.length || !p.symbol_name) return null;
  const idx = store.readiness.find(r =>
    p.symbol_name.includes(r.name.replace(/[ⅠⅡⅢ]$/, '')));
  const pct = idx && idx.valuation_percentile;
  if (pct == null || pct < 70) return null;
  return { pct, name: idx.name, cls: pct >= 80 ? 'no' : 'maybe' };
}

// 收网退出：关闭计划（不再产生买卖待办）；持仓仍列示在网格账户、不计满格资金
function closePlan(p) {
  const v = planValuation(p);
  const pos = store.portfolioData
    && store.portfolioData.accounts.grid.positions.find(x => x.plan_id === p.id && x.shares > 0);
  const posTxt = pos ? `当前持仓 <b>${pos.shares.toLocaleString('zh-CN')}</b> 份` : '当前无持仓';
  toast(`收网将关闭「<b>${p.name}</b>」：${posTxt}，关闭后不再产生买卖待办，建议逢反弹卖出并记成交`
    + (v ? `（${v.name} 分位 ${v.pct}%）` : ''), 'warn',
    { label: '确认收网', fn: async () => {
      await API.patch(`/api/grid/plans/${p.id}/status?status=closed`);
      toast(`「<b>${p.name}</b>」已收网归档`);
      await loadPlans(); loadToday();
      if (store.detail && store.detail.id === p.id) loadPlanDetail(p.id);
    } });
}

export const plansActions = {
  planRows() { return store.plans; },
  planCapital(p) {
    return (p.levels || []).reduce((s, l) => s + (l.amount || 0), 0);
  },
  planValuation,
  closePlan,
  statusChip: { active: 'go', paused: 'maybe', broken: 'no', closed: 'wait' },

  open(id, withAi) {
    openPlan(id).then(() => { if (withAi) runPlanAi(); });
  },
  closeDetail() { store.detail = null; store.exitAi = null; },

  // ---- 棋盘与标尺 ----
  boardCells() {
    const d = store.detail;
    if (!d || !d.levels) return [];
    return d.levels.map((l, i) => ({
      no: l.level, state: (d.level_states || [])[i] || 'wait',
      label: CELL_LABEL[(d.level_states || [])[i]] || '待买', buy: l.buy_price,
    }));
  },
  rulerInfo() {
    const d = store.detail;
    if (!d) return null;
    const low = d.levels.length ? d.levels[d.levels.length - 1].buy_price : d.base_price;
    const top = d.base_price;
    const h = (store.todayData && store.todayData.health.find(x => x.plan_id === d.id)) || {};
    const cur = h.cur != null ? h.cur : null;
    const pct = cur == null ? null : Math.max(2, Math.min(98, (cur - low) / ((top - low) || 1) * 100));
    return { low, top, cur, pct };
  },

  // ---- 状态操作 ----
  async setStatus(p, status) {
    await API.patch(`/api/grid/plans/${p.id}/status?status=${status}`);
    toast(status === 'paused' ? '已暂停挂新单' : '已恢复 ACTIVE');
    await loadPlans(); loadToday();
    if (store.detail && store.detail.id === p.id) loadPlanDetail(p.id);
  },
  async delPlan(p, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/grid/plans/${p.id}`);
      store.detail = null;
      await loadPlans(); loadToday();
      toast(`计划 <b>#${p.id} ${p.name}</b> 已删除`, '', {
        label: '撤销', fn: async () => {
          const re = await API.post('/api/grid/plans', {
            name: p.name, symbol: p.symbol, symbol_name: p.symbol_name,
            base_price: p.base_price, grid_step: p.grid_step, grid_count: p.grid_count,
            amount_per_grid: p.amount_per_grid, step_increase: p.step_increase,
            profit_retention: p.profit_retention, note: p.note,
          });
          await loadPlans();
          toast(`已恢复为 <b>#${re.id}</b>`);
        },
      });
    } else {
      btn.dataset.armed = '1';
      btn.textContent = '确认删除?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删除'; }, 3500);
    }
  },

  // ---- 破网处置 ----
  selBrk(action) { store.brkAction = action; },
  async runBrkAction(p) {
    const action = store.brkAction;
    if (!action) { toast('先选择一个处置方式'); return; }
    const h = (store.todayData && store.todayData.health.find(x => x.plan_id === p.id)) || {};
    const body = { action, new_base_price: action === 'extend' ? h.cur : undefined };
    const r = await API.post(`/api/grid/plans/${p.id}/break-action`, body);
    if (action === 'extend') {
      toast(`已向下接网 · 新计划 <b>#${r.new_plan_id}</b>（基准 ${h.cur}），旧计划已标记破网`);
    } else {
      toast(action === 'hold' ? '已标记：装死持有（BROKEN），等价格回网' : '已止损归档（CLOSED）');
    }
    store.brkAction = null;
    await loadPlans(); loadToday();
    openPlan(p.id);
  },

  // ---- AI 计划体检 ----
  runPlanAi,
  // ---- AI 退出研判 ----
  runExitAi,

  // ---- 成交录入 ----
  openTradeModal(p) {
    store.tradeForm = {
      plan_id: p.id, symbol: p.symbol, symbol_name: p.symbol_name,
      trade_date: new Date().toISOString().slice(0, 10),
      direction: 'buy', price: '', shares: '', fee: '', note: '',
    };
    store.modal = 'trade';
  },
  async saveTrade() {
    const f = store.tradeForm;
    if (!f.price || !f.shares) { toast('成交价与份额必填', 'warn'); return; }
    try {
      const t = await API.post('/api/trades', {
        plan_id: f.plan_id, symbol: f.symbol, symbol_name: f.symbol_name,
        trade_date: f.trade_date, direction: f.direction,
        price: parseFloat(f.price), shares: parseFloat(f.shares),
        fee: parseFloat(f.fee || 0), note: f.note || undefined,
        dca_plan_id: f.dca_plan_id || undefined,
      });
      store.modal = null;
      toast(t.grid_level ? `已记录并匹配到 <b>G${t.grid_level}</b>` : '已记录（未匹配到档位）');
      // 来自待办的一键成交：顺手把该待办标记完成
      if (f.note && String(f.note).startsWith('待办成交')) {
        store.dismissedTodos[`${f.plan_id}-${f.direction}-${t.grid_level || ''}`] = true;
      }
      // 定投待办成交：消掉本期定投待办并刷新定投计划
      if (f.dca_plan_id) {
        if (f.dca_period_key) {
          store.dismissedTodos[`dca-${f.dca_plan_id}-${f.dca_period_key}`] = true;
        }
        loadDcaPlans();
      }
      if (f.plan_id) await loadPlanDetail(f.plan_id);
      loadToday();
    } catch (e) { toast(e.message, 'warn'); }
  },
  async delTrade(t, btn) {
    if (btn.dataset.armed) {
      await API.del(`/api/trades/${t.id}`);
      toast('成交已删除');
      if (store.detail) loadPlanDetail(store.detail.id);
      loadToday();
    } else {
      btn.dataset.armed = '1'; btn.textContent = '确认?';
      setTimeout(() => { delete btn.dataset.armed; btn.textContent = '删'; }, 3500);
    }
  },
};

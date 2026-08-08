// 今天：待办 + 预警灯 + 组合概览 + 精选/成交
import * as API from '../api.js';
import { store, toast, switchTab, loadToday, loadPlans, openPlan } from '../store.js';

function todoList() {
  if (!store.todayData) return [];
  return store.todayData.todos.filter(t => !store.dismissedTodos[`${t.plan_id}-${t.direction}-${t.level}`]);
}

function alertList() {
  if (!store.todayData) return [];
  const a = store.todayData.alerts;
  const out = [];
  for (const x of a.broken) out.push({ kind: 'crit', title: '破网', chip: x.name, chipCls: 'no',
    body: `${x.symbol_name || x.symbol} 现价 <b>${x.cur}</b>，已跌破最低档 <b>${x.low}</b>（网外 ${x.beyond_pct}%）。网格已失效，需人工处置。`,
    act: 'brk', plan: x });
  for (const x of a.high) out.push({ kind: 'warnl', title: '高位运行', chip: x.name, chipCls: 'maybe',
    body: `${x.symbol_name || x.symbol} 现价 <b>${x.cur}</b>，高于基准价 <b>${x.top}</b>（+${x.beyond_pct}%）。继续上涨将卖无可卖、买无可买。`,
    act: 'rebase', plan: x });
  for (const x of a.valuation) out.push({ kind: 'warnl', title: '估值越界', chip: x.name, chipCls: 'maybe',
    body: `${x.index_name} 估值分位 <b>${x.valuation_percentile}%</b>，已涨过 50% 警戒线。建议买入侧暂停，只卖不买。`,
    act: 'picks', plan: x });
  // 退出引导：70% 偏高（只卖不买）/ 80% 高估（建议收网）
  for (const x of (a.exit || [])) out.push({
    kind: x.tier === 'exit' ? 'crit' : 'warnl',
    title: x.tier === 'exit' ? '高估·建议收网' : '偏高·只卖不买',
    chip: x.name, chipCls: x.tier === 'exit' ? 'no' : 'maybe',
    body: `${x.index_name} 估值分位 <b>${x.valuation_percentile}%</b>。${x.verdict}。点进计划可「收网退出」或让 AI 做退出研判。`,
    act: 'exit', plan: { plan_id: x.plan_id } });
  return out;
}

// 上移重开（带估值闸门）：估值>50% 时 toast 升级为警告
function rebaseWithGate(planSummary) {
  const run = async () => {
    const plan = await API.get(`/api/grid/plans/${planSummary.plan_id}`);
    await API.post('/api/grid/plans', {
      name: plan.name, symbol: plan.symbol, symbol_name: plan.symbol_name,
      base_price: planSummary.cur, grid_step: plan.grid_step, grid_count: plan.grid_count,
      amount_per_grid: plan.amount_per_grid, step_increase: plan.step_increase,
      profit_retention: plan.profit_retention,
    });
    await API.patch(`/api/grid/plans/${planSummary.plan_id}/status?status=closed`);
    toast(`已上移重开 · 新基准价 <b>${planSummary.cur}</b>（旧计划已归档）`);
    loadPlans(); loadToday();
  };
  const idx = store.readiness.find(r =>
    planSummary.symbol_name && planSummary.symbol_name.includes(r.name.replace(/[ⅠⅡⅢ]$/, '')));
  const pct = idx && idx.valuation_percentile;
  if (pct != null && pct > 50) {
    toast(`⚠️ <b>${idx.name}</b> 当前估值分位 <b>${pct}%</b>（雷达：${idx.verdict}）。估值偏高，此时开网违背低估原则`, 'warn',
      { label: '仍要上移', fn: run });
  } else if (pct != null) {
    toast(`<b>${idx.name}</b> 当前估值分位 <b>${pct}%</b>（雷达：${idx.verdict}），可以上移重开`, '',
      { label: '确认上移', fn: run });
  } else {
    toast('未能关联监控指数 · 请先在机会页确认估值仍在低位', '', { label: '确认上移', fn: run });
  }
}

export const todayActions = {
  todoList,
  alertList,
  rebaseWithGate,
  dismissTodo(t) {
    store.dismissedTodos[`${t.plan_id}-${t.direction}-${t.level}`] = true;
    toast('已标记 · 记得去券商下单');
  },
  // 待办一键成交：预填表单，确认即记录（自动匹配档位）
  completeTodo(t) {
    store.tradeForm = {
      plan_id: t.plan_id, symbol: t.symbol, symbol_name: t.symbol_name,
      trade_date: new Date().toISOString().slice(0, 10),
      direction: t.direction, price: t.price, shares: t.shares,
      fee: '', note: `待办成交 · G${t.level}`,
    };
    store.modal = 'trade';
  },
  alertAction(al) {
    if (al.act === 'picks') { switchTab('picks'); return; }
    if (al.act === 'rebase') { rebaseWithGate(al.plan); return; }
    openPlan(al.plan.plan_id);
  },
  briefKpis() {
    const b = store.todayData ? store.todayData.portfolio : null;
    if (!b || !b.principal) return null;
    return b;
  },
  topPicks() {
    return store.readiness.filter(r => r.level === 'go').slice(0, 3);
  },
  goPlan(id) { openPlan(id); },
  goTab(t) { switchTab(t); },
};

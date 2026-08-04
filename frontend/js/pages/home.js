// 首页：理念带 + 快照 + 估值地图 + 市场快照 + 功能导览
import { store, switchTab } from '../store.js';

function fmtWan(v) {
  if (v == null) return '—';
  return (v / 10000).toFixed(1) + '万';
}

// ---- 估值地图点位计算（模块内共享） ----
function computeVmapDots() {
  return store.readiness
    .filter(r => r.valuation_percentile != null)
    .map(r => ({
      ts_code: r.ts_code, name: r.name, pct: r.valuation_percentile,
      labeled: r.valuation_percentile < 30 || r.valuation_percentile >= 70,
      color: r.valuation_percentile < 30 ? '#15803D' : r.valuation_percentile < 50 ? '#78716C' : r.valuation_percentile < 70 ? '#B45309' : '#DC2626',
    }))
    .sort((a, b) => a.pct - b.pct);
}

export const homeActions = {
  // ---- 快照 KPI ----
  homeKpis() {
    const t = store.todayData, pf = store.portfolioData, rv = store.reviewData;
    const todos = t ? t.todos.length : 0;
    const alerts = t ? (t.alerts.broken.length + t.alerts.high.length + t.alerts.valuation.length) : 0;
    const activePlans = store.plans.filter(p => p.status === 'active').length;
    const pausedPlans = store.plans.filter(p => p.status === 'paused').length;
    const brief = (t && t.portfolio) || {};
    return [
      { k: '总资产', v: pf && pf.total_cost != null ? '¥' + fmtWan((pf.accounts.core.market_value || 0) + (pf.accounts.grid.market_value || 0) + (pf.cash || 0)) : '—',
        f: 'vs 本金 ' + (pf ? fmtWan(pf.principal) : '—'), go: 'portfolio' },
      { k: '今日待办', v: String(todos), f: '临近档位的挂单提醒', go: 'today' },
      { k: '预警', v: String(alerts), f: t ? `破网 ${t.alerts.broken.length} · 高位 ${t.alerts.high.length} · 越界 ${t.alerts.valuation.length}` : '—',
        go: 'today', warm: alerts > 0 },
      { k: '运行计划', v: String(activePlans + pausedPlans), f: `ACTIVE ${activePlans} · PAUSED ${pausedPlans}`, go: 'plans' },
      { k: '套利回合', v: rv ? String(rv.totals.rounds) : '—', f: rv ? '已实现 ¥' + Math.round(rv.totals.realized_pnl) : '—', go: 'review' },
      { k: '满格 / 本金', v: brief.safety_ratio != null ? Math.round(brief.safety_ratio * 100) + '%' : '—',
        f: '安全线 70% 内', go: 'portfolio', good: brief.safety_ratio != null && !brief.safety_warn, bad: !!brief.safety_warn },
    ];
  },

  // ---- 估值地图：分位点位（只标注两端，中部仅标点，上下交错避免重叠） ----
  vmapDots() { return computeVmapDots(); },
  vmapZones() {
    // 分带视图：低估 <30 / 中性 30-70 / 高估 ≥70，带内按分位升序
    const dots = computeVmapDots();
    const zone = (lo, hi, title, cls) => ({
      title, cls, items: dots.filter(d => d.pct >= lo && d.pct < hi),
    });
    return [
      zone(-1, 30, '低估区 · 可开网候选', 'go'),
      zone(30, 70, '中性区 · 等待', 'wait'),
      zone(70, 101, '高估区 · 只卖不买', 'no'),
    ];
  },

  // ---- 今日市场快照：20 日动量最强/最弱 + 估值越界提示 ----
  marketSnapshot() {
    const rows = store.readiness.filter(r => r.ret_20d != null);
    if (!rows.length) return [];
    const sorted = [...rows].sort((a, b) => a.ret_20d - b.ret_20d);
    const weakest = sorted[0], strongest = sorted[sorted.length - 1];
    const over = store.readiness.filter(r => r.valuation_percentile != null && r.valuation_percentile > 50);
    const out = [];
    if (weakest) out.push({ tag: '20日最弱', name: weakest.name, val: weakest.ret_20d + '%',
      note: `分位 ${weakest.valuation_percentile ?? '—'}% · ${weakest.verdict}`, cls: 'g' });
    if (strongest) out.push({ tag: '20日最强', name: strongest.name, val: '+' + strongest.ret_20d + '%',
      note: `分位 ${strongest.valuation_percentile ?? '—'}% · ${strongest.verdict}`, cls: 'r' });
    if (over.length) out.push({ tag: '估值越界', val: `${over.length} 只`,
      name: over.slice(0, 3).map(o => o.name).join('、') + (over.length > 3 ? ` 等 ${over.length} 只` : ''),
      note: '分位越过 50% 否决线，买入侧暂停', cls: 'r' });
    return out;
  },

  goTab(t) { switchTab(t); },
  homeOpenDrawer(tsCode) { store.drawer = tsCode; },

  async ensureHomeData() {},  // 首页数据由 switchTab 统一加载；保留空实现避免模板报错
};

// 全局状态与共享加载器。store 是唯一响应式数据源，页面模块通过动作函数改写它。
import * as API from './api.js';

const { reactive } = window.PetiteVue;

export const store = reactive({
  tab: 'home',
  clock: '', mktOpen: false,

  // 数据切片
  readiness: [], readinessErr: '',
  todayData: null, todayErr: '',
  plans: [],
  detail: null, detailTrades: [], detailLoading: false,
  portfolioData: null, fundFlows: [],
  reviewData: null,
  weekly: null, weeklyLoading: false,
  ai: { enabled: false, model: null },
  settings: null,

  // UI 状态
  drawer: null,            // 指数 ts_code；null=关闭
  drawerAi: null, drawerAiLoading: false,
  gearOpen: false,
  modal: null,             // 'fundflow' | 'trade' | null
  tradeForm: null,         // 录入成交表单上下文（plan_id/symbol 等）
  toastObj: null,          // {msg, type, action:{label,fn}}
  plannerSeed: null,       // 机会页带入选中标的
  recentTrades: [],
  dismissedTodos: {},      // 今日待办本地已确认（页面级，不持久化）
  planAi: null, planAiLoading: false,
  exitAi: null, exitAiLoading: false,
  allocation: null,        // 资金分配建议 /api/portfolio/allocation
  brkAction: null,
  // 规划页
  plannerForm: {
    symbol: '', symbol_name: '', name: '', base_price: '',
    grid_step: 5, grid_count: 10, grid_mode: 'amount',
    amount_per_grid: 10000, shares_per_grid: 10000,
    step_increase: 0, profit_retention: 0,
  },
  etfQuery: '', etfResults: [],
  plannerPreview: null, plannerLoading: false,
  plannerBt: null, btLoading: false,
  plannerOpt: null, optLoading: false,
  plannerOptAi: null, optAiLoading: false,
  plannerLookback: 750,   // 回测窗口（交易日）：250≈1年 750≈3年 1250≈5年
  plannerAnchor: 'window', // 回测锚定口径：window=窗口起点 / cross=当前价位穿越点
  compareRebase: true,    // 回测对比口径：自动上移重开
  // 智能寻品
  discoveryState: null, discoveryResult: null,
  discoveryAi: null, discoveryAiLoading: false,
  // 监控池管理
  watchlist: [], wlManage: false, wlQuery: '', wlResults: [], wlBusy: {},
  // 数据状态与通知
  dataStatus: null,
  notifyEnabled: true,
  flowForm: null, settingsForm: null,
  // 首启向导
  obOpen: false, obStep: 1,
  obToken: '', obTestStat: '未测试', obTesting: false, obTokenOk: false,
  obBfRunning: false, obBfLog: '', obBfDone: false, obAgree: false,
  appVersion: '',          // /api/version，打包版为 tag，源码运行为 dev
  update: null,            // {version, url, notes, date} 发现新版本时非 null
  updatePrompt: false,     // 启动更新弹窗开关
});

/* ---------------- 通用 ---------------- */
let toastTimer = null;
export function toast(msg, type, action) {
  store.toastObj = { msg, type: type || '', action: action || null };
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { store.toastObj = null; }, action ? 6000 : 3400);
}

export function switchTab(name) {
  store.tab = name;
  store.gearOpen = false;
  window.scrollTo({ top: 0 });
  if (name === 'today' || name === 'home') loadToday();
  if (name === 'home') {
    if (!store.portfolioData) loadPortfolio();
    if (!store.reviewData) loadReview();
  }
  if (name === 'picks') { loadReadiness(); loadWatchlist(); }
  if (name === 'plans') { loadPlans(); loadReadiness(); }
  if (name === 'portfolio') loadPortfolio();
  if (name === 'review') loadReview();
  if (name === 'planner') loadReadiness();
  history.replaceState(null, '', '#' + name);
}

/* ---------------- 数据加载 ---------------- */
export async function loadReadiness(force, _retried) {
  if (store.readiness.length && !force) return;
  try {
    store.readiness = await API.get('/api/readiness');
    store.readinessErr = '';
  } catch (e) {
    store.readinessErr = e.message;
    if (!_retried) {  // 启动并发高峰期的瞬时失败重试一次
      await new Promise(r => setTimeout(r, 800));
      return loadReadiness(force, true);
    }
  }
}

export async function loadWatchlist() {
  try { store.watchlist = await API.get('/api/watchlist'); }
  catch { store.watchlist = []; }
}

export async function loadToday() {
  try {
    store.todayData = await API.get('/api/today');
    store.todayErr = '';
  } catch (e) { store.todayErr = e.message; }
  try {
    store.recentTrades = (await API.get('/api/trades')).slice(0, 4);
  } catch { store.recentTrades = []; }
  try { store.dataStatus = await API.get('/api/data-status'); } catch { /* 忽略 */ }
  maybeNotifyTodos();
}

/* 今日有待办时发一次系统通知（每天最多一次，可在设置里关） */
function maybeNotifyTodos() {
  const n = store.todayData ? store.todayData.todos.length : 0;
  if (!n || !store.notifyEnabled) return;
  const day = new Date().toISOString().slice(0, 10);
  if (localStorage.getItem('etfw_notified') === day) return;
  localStorage.setItem('etfw_notified', day);
  API.post('/api/notify', {
    title: 'ETFWorld 网格提醒',
    body: `今日有 ${n} 条网格待办临近触发，记得挂单。`,
  }).catch(() => {});
}

export async function loadPlans() {
  store.plans = await API.get('/api/grid/plans');
}

export async function loadPlanDetail(id) {
  store.detailLoading = true;
  store.exitAi = null;  // 切换计划时清掉上一只的退出研判
  try {
    store.detail = await API.get(`/api/grid/plans/${id}`);
    store.detailTrades = await API.get(`/api/trades?plan_id=${id}`);
  } finally { store.detailLoading = false; }
}

export async function loadPortfolio() {
  store.portfolioData = await API.get('/api/portfolio');
  store.fundFlows = await API.get('/api/portfolio/fund-flows');
  try { store.allocation = await API.get('/api/portfolio/allocation'); }
  catch { store.allocation = null; }  // 雷达数据缺失时建议卡隐藏
}

export async function loadReview() {
  store.reviewData = await API.get('/api/review');
}

export async function loadAiStatus() {
  try {
    const s = await API.get('/api/ai/status');
    store.ai = { enabled: !!s.enabled, model: s.model || null };
  } catch { store.ai = { enabled: false, model: null }; }
}

export async function loadSettings() {
  store.settings = await API.get('/api/settings');
}

/* ---------------- 版本与检查更新 ---------------- */
function _semver(v) {
  const m = String(v || '').replace(/^v/, '').match(/\d+/g);
  return m ? m.map(Number) : null;
}

export async function loadVersion() {
  try {
    const r = await API.get('/api/version');
    store.appVersion = r.version || 'dev';
  } catch { store.appVersion = 'dev'; }
  // 被动检查更新：仅打包版本（dev 跳过）；只读 GitHub 公开 API，失败静默
  const local = _semver(store.appVersion);
  if (!local) return;
  try {
    const rel = await fetch('https://api.github.com/repos/LiuShiYi1027/ETFWorld/releases/latest')
      .then(r => r.ok ? r.json() : null);
    const remote = rel && _semver(rel.tag_name);
    if (remote) {
      for (let i = 0; i < 3; i++) {
        if ((remote[i] || 0) > (local[i] || 0)) {
          store.update = {
            version: rel.tag_name,
            url: rel.html_url,
            notes: (rel.body || '').trim(),
            date: (rel.published_at || '').slice(0, 10),
          };
          // 仿 QingWu：启动即弹窗提醒；「跳过此版本」持久化，「稍后」下次启动再提醒
          if (localStorage.getItem('etfw_skip_version') !== rel.tag_name) {
            store.updatePrompt = true;
          }
          break;
        }
        if ((remote[i] || 0) < (local[i] || 0)) break;
      }
    }
  } catch { /* 离线或被墙都静默 */ }
}

export function dismissUpdate(skip) {
  store.updatePrompt = false;
  if (skip && store.update) {
    localStorage.setItem('etfw_skip_version', store.update.version);
  }
}

/* ---------------- 启动 ---------------- */
export function boot() {
  loadAiStatus();
  loadVersion();
  loadReadiness();
  loadToday();
  loadPlans();
  loadPortfolio();
  // 首启检测：未配置数据源或无数据且未曾完成向导 → 进入首启向导
  API.get('/api/settings').then(s => {
    store.settings = s;
    if ((!s.tushare_configured || !s.has_data) && !localStorage.getItem('etfw_onboarded')) {
      store.obOpen = true;
      store.obStep = 1;
    }
  }).catch(() => {});
  // 开盘状态时钟（A股 周一–五 09:30–11:30 / 13:00–15:00）
  const tick = () => {
    const d = new Date();
    const mins = d.getHours() * 60 + d.getMinutes();
    const day = d.getDay();
    store.mktOpen = day >= 1 && day <= 5 && ((mins >= 570 && mins <= 690) || (mins >= 780 && mins <= 900));
    store.clock = `${String(d.getHours()).padStart(2, '0')}:${String(d.getMinutes()).padStart(2, '0')}`;
  };
  tick(); setInterval(tick, 30000);
  store.notifyEnabled = localStorage.getItem('etfw_notify') !== '0';
  // 深链接：#plans/3、#picks/000300.SH、#onboarding
  const h = location.hash.slice(1);
  if (h === 'onboarding') { store.obOpen = true; store.obStep = 1; return; }
  if (h) {
    const [t, arg] = h.split('/');
    if (['home', 'today', 'picks', 'planner', 'plans', 'portfolio', 'review', 'states'].includes(t)) {
      store.tab = t;
    }
    if (t === 'plans' && arg) openPlan(Number(arg));
    if (t === 'picks' && arg) store.drawer = arg;
  }
}

/* 供页面模块共用的计划详情打开入口 */
export async function openPlan(id) {
  if (store.tab !== 'plans') switchTab('plans');
  await loadPlanDetail(id);
  window.PetiteVue.nextTick(() => {
    const el = document.getElementById('plan-detail');
    if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
  });
}

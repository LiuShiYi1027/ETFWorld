// 智能寻品：全池扫描（后台任务 + 轮询）+ AI 研判不死属性
import * as API from '../api.js';
import { store, toast, switchTab } from '../store.js';

let pollTimer = null;

async function pollScan() {
  try {
    const st = await API.get('/api/discovery/scan');
    store.discoveryState = st;
    if (!st.running) {
      clearInterval(pollTimer);
      pollTimer = null;
      if (st.result) {
        store.discoveryResult = st.result;
        toast(`扫描完成 · 通过 ${st.result.passed} / ${st.result.scanned} 只`);
      } else if (st.error) {
        toast('扫描失败 · ' + st.error, 'warn');
      }
    }
  } catch { /* 轮询失败静默，下轮继续 */ }
}

function discoveryBusy() {
  return !!(store.discoveryState && store.discoveryState.running);
}

export const picksDiscoveryActions = {
  discoveryItems() {
    return store.discoveryResult ? store.discoveryResult.items : [];
  },
  discoveryBusy,
  discoveryProgress() {
    const st = store.discoveryState;
    if (!st || !st.running) return '';
    return `扫描中 ${st.done}/${st.total} · ${st.current}`;
  },
  async runDiscovery() {
    if (discoveryBusy()) return;
    store.discoveryResult = store.discoveryResult || null;
    try {
      await API.post('/api/discovery/scan');
      store.discoveryState = { running: true, done: 0, total: 0, current: '' };
      pollTimer = setInterval(pollScan, 1500);
      pollScan();
    } catch (e) { toast('启动扫描失败 · ' + e.message, 'warn'); }
  },
  async runDiscoveryAi() {
    if (store.discoveryAiLoading) return;
    store.discoveryAiLoading = true;
    store.discoveryAi = null;
    try {
      store.discoveryAi = await API.post('/api/ai/discovery-review?top=10');
    } catch (e) { toast('AI 研判失败 · ' + e.message, 'warn'); }
    finally { store.discoveryAiLoading = false; }
  },
  discoveryAiFor(name) {
    if (!store.discoveryAi) return null;
    return (store.discoveryAi.items || []).find(x => x.name === name) || null;
  },
  discoveryGoPlanner(item) {
    if (item.etf && item.etf.ts_code) {
      store.plannerForm.symbol = item.etf.ts_code;
      store.plannerForm.symbol_name = item.etf.name || item.name;
      store.plannerForm.name = item.name + '网格';
      store.plannerForm.grid_step = item.volatility >= 40 ? 8 : item.volatility >= 25 ? 6 : item.volatility >= 15 ? 4 : 3;
    }
    switchTab('planner');
  },
};

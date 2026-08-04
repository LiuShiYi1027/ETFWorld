// 首启向导：免责 → 数据源 → 回填（真实 API 接线）
import * as API from '../api.js';
import { store, toast, switchTab, loadReadiness } from '../store.js';

export const onboardActions = {
  obGo(n) { store.obStep = n; },
  openOnboard() {
    store.gearOpen = false;
    store.obStep = 1;
    store.obOpen = true;
  },
  closeOnboard(skipToast) {
    store.obOpen = false;
    localStorage.setItem('etfw_onboarded', '1');
    if (skipToast) toast('已跳过向导 · 可随时在右上角 ⚙ 重新进入');
  },

  async obTestToken() {
    const token = (store.obToken || '').trim();
    if (!token) { store.obTestStat = '请先填入 Token'; store.obTokenOk = false; return; }
    store.obTesting = true;
    store.obTestStat = '保存并连接中…';
    try {
      await API.put('/api/settings', { tushare_token: token });
      const r = await API.post('/api/settings/test/tushare');
      store.obTestStat = '✓ ' + (r.message || '连接正常');
      store.obTokenOk = true;
    } catch (e) {
      store.obTestStat = '✗ ' + e.message;
      store.obTokenOk = false;
    } finally { store.obTesting = false; }
  },

  async obBackfill() {
    if (store.obBfRunning) return;
    store.obBfRunning = true;
    store.obBfLog = '正在回填历史数据（约 1-3 分钟，期间请勿关闭）…';
    try {
      const end = new Date().toISOString().slice(0, 10).replace(/-/g, '');
      const start = String(Number(end.slice(0, 4)) - 10) + end.slice(4);  // 近 10 年
      await API.post(`/api/valuation/backfill?start=${start}&end=${end}`);
      store.obBfLog = '✓ 回填完成，估值分位已计算';
      store.obBfDone = true;
      loadReadiness(true);
    } catch (e) {
      store.obBfLog = '✗ 回填失败 · ' + e.message + '（可稍后在设置页重试）';
    } finally { store.obBfRunning = false; }
  },

  obFinish() {
    store.obOpen = false;
    localStorage.setItem('etfw_onboarded', '1');
    switchTab('picks');
    toast('初始化完成 · 机会页已就绪，从 <b>适合开启</b> 的指数开始');
  },
};

// 设置弹层：Tushare 数据源 + AI 服务（任意 OpenAI 兼容端点）
import * as API from '../api.js';
import { store, toast } from '../store.js';

async function persistSettings() {
  const f = store.settingsForm;
  const r = await API.put('/api/settings', {
    tushare_token: f.tushare_token || null,
    tushare_api_url: f.tushare_api_url,
    ai_api_key: f.ai_api_key || null,
    ai_api_url: f.ai_api_url,
    ai_model: f.ai_model,
    clear_tushare_token: !!f.clear_tushare_token,
    clear_ai_api_key: !!f.clear_ai_api_key,
  });
  store.settings = r;
  store.ai = { enabled: r.ai_configured, model: r.ai_model };
  return r;
}

export const settingsActions = {
  async openSettings() {
    store.gearOpen = false;
    try { store.settings = await API.get('/api/settings'); }
    catch (e) { toast(e.message, 'warn'); return; }
    store.settingsForm = {
      tushare_token: '', tushare_api_url: store.settings.tushare_api_url,
      ai_api_key: '', ai_api_url: store.settings.ai_api_url, ai_model: store.settings.ai_model,
      clear_tushare_token: false, clear_ai_api_key: false,
    };
    store.modal = 'settings';
  },
  async saveSettings() {
    try {
      await persistSettings();
      store.modal = null;
      toast('设置已保存并立即生效');
    } catch (e) { toast('保存失败 · ' + e.message, 'warn'); }
  },
  async testProvider(provider, btn) {
    btn.disabled = true;
    const old = btn.textContent;
    btn.textContent = '连接中…';
    try {
      await persistSettings();  // 先保存再测试，与旧版行为一致
      const r = await API.post(`/api/settings/test/${provider}`);
      toast(r.message || '连接正常');
    } catch (e) { toast(e.message, 'warn'); }
    finally { btn.disabled = false; btn.textContent = old; }
  },
};

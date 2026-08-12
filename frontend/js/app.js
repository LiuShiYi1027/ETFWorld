// 应用入口：合并状态与页面动作，挂载 petite-vue
import { store, boot, switchTab, toast, openPlan, loadReadiness, dismissUpdate } from './store.js';
import { homeActions } from './pages/home.js';
import { todayActions } from './pages/today.js';
import { picksActions } from './pages/picks.js';
import { plannerActions } from './pages/planner.js';
import { plansActions } from './pages/plans.js';
import { portfolioActions } from './pages/portfolio.js';
import { reviewActions } from './pages/review.js';
import { dcaActions } from './pages/dca.js';
import { labActions } from './pages/lab.js';
import { settingsActions } from './pages/settings.js';
import { onboardActions } from './pages/onboarding.js';
import { picksDiscoveryActions } from './pages/discovery.js';

const app = Object.assign(
  store,
  { switchTab, toast, openPlan, loadReadiness, dismissUpdate },
  homeActions, todayActions, picksActions, plannerActions,
  reviewActions, plansActions, portfolioActions, settingsActions,
  onboardActions, picksDiscoveryActions, dcaActions, labActions,
  {
    // 模板小工具
    fmt(v, d = 3) {
      if (v == null || isNaN(v)) return '—';
      return Number(v).toLocaleString('zh-CN', { minimumFractionDigits: d, maximumFractionDigits: d });
    },
    toggleGear() { store.gearOpen = !store.gearOpen; },
    closeModal() { store.modal = null; },
  },
);

window.PetiteVue.createApp(app).mount('body');
boot();

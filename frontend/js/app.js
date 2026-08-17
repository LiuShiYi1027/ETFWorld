// 应用入口：合并状态与页面动作，挂载 Vue 3（全局构建版，零构建哲学不变）
import { createApp, watch } from '/vendor/vue.esm-browser.prod.js';
import { store, boot, switchTab, toast, openPlan, loadReadiness, dismissUpdate, fmt } from './store.js';
import { homeActions } from './pages/home.js';
import { todayActions } from './pages/today.js';
import { picksActions } from './pages/picks.js';
import { plannerActions } from './pages/planner.js';
import { labActions } from './pages/lab.js';
import { plansActions } from './pages/plans.js';
import { portfolioActions } from './pages/portfolio.js';
import { reviewActions } from './pages/review.js';
import { settingsActions } from './pages/settings.js';
import { onboardActions } from './pages/onboarding.js';
import { picksDiscoveryActions } from './pages/discovery.js';
import { dcaActions } from './pages/dca.js';
import { rotationActions } from './pages/rotation.js';
import { TodayView } from './pages/today.view.js';
import { PlansView } from './pages/plans.view.js';
import { PlannerView } from './pages/planner.view.js';
import { PicksView } from './pages/picks.view.js';
import { PortfolioView } from './pages/portfolio.view.js';
import { ReviewView } from './pages/review.view.js';
import { LabView } from './pages/lab.view.js';
import { HomeView } from './pages/home.view.js';

const app = createApp({
  // data 直接返回同一个 reactive store 单例 —— 模板裸名访问全部不变
  data: () => store,
  methods: Object.assign(
    { switchTab, toast, openPlan, loadReadiness, dismissUpdate },
    homeActions, todayActions, picksActions, plannerActions, labActions,
    reviewActions, plansActions, portfolioActions, settingsActions,
    onboardActions, picksDiscoveryActions, dcaActions, rotationActions,
    {
      // 模板小工具
      fmt,
      toggleGear() { store.gearOpen = !store.gearOpen; },
      closeModal() { store.modal = null; },
    },
  ),
});

// 原 petite-vue 的 v-effect="if(tab==='planner')applySeed()" 等价物
watch(() => store.tab, t => { if (t === 'planner') plannerActions.applySeed(); });

app.component('today-view', TodayView);
app.component('plans-view', PlansView);
app.component('planner-view', PlannerView);
app.component('picks-view', PicksView);
app.component('portfolio-view', PortfolioView);
app.component('review-view', ReviewView);
app.component('lab-view', LabView);
app.component('home-view', HomeView);
app.mount('#app');
boot();

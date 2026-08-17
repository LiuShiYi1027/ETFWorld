// 首页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, switchTab } from '../store.js';
import { homeActions } from './home.js';
import { picksActions } from './picks.js';

export const HomeView = {
  template: `
<section class="page">
  <div class="hero">
    <div class="phil">低估买入 · 波动套利 · 不死标的 · 机械执行</div>
    <div class="sub">ETFWorld 是你的网格策略工作台：看估值位置 → 找可交易 ETF → 设计网格 → 压力测试 → 记录执行与复盘。<b>不连接券商、不自动下单</b>，数据与密钥都在你自己的电脑上。研究工具，不构成投资建议。</div>
    <div class="cta">
      <button class="btn primary" @click="switchTab('today')">今日待办 →</button>
      <button class="btn" @click="switchTab('planner')">开一张新网格</button>
      <button class="btn" @click="switchTab('portfolio')">查看组合</button>
    </div>
  </div>

  <div class="kpis" style="margin-top:22px;">
    <div class="kpi" v-for="k in homeKpis()" :key="k.k" @click="k.go && switchTab(k.go)" :style="k.go?'cursor:pointer;':''">
      <div class="k">{{ k.k }}</div>
      <div class="v" :class="{good:k.good,bad:k.bad,warm:k.warm}">{{ k.v }}</div>
      <div class="f">{{ k.f }}</div>
    </div>
  </div>

  <div class="card" style="margin-top:4px;">
    <div class="card-h"><div class="t">市场估值地图</div><div class="d">监控指数按 5 年估值分位分布 · 点击点位查看详情</div></div>
    <div class="card-b">
      <div class="vmap">
        <div class="vmap-scale">
          <span class="vmap-dot" v-for="d in vmapDots()" :key="d.ts_code" :style="{left:d.pct+'%',background:d.color}" :title="d.name+' '+d.pct+'%'" @click="openDrawer(d.ts_code)"></span>
        </div>
        <div class="vmap-ticks"><span>0% · 低估区</span><span>20%</span><span>40%</span><span>60%</span><span>80%</span><span>100% · 高估区</span></div>
      </div>
      <div class="vzones">
        <div class="vzone" v-for="z in vmapZones()" :key="z.title">
          <div class="zt"><span class="chip" :class="z.cls">{{ z.title }}</span><span class="zn">{{ z.items.length }} 只</span></div>
          <div class="vchips">
            <span class="vchip" v-for="d in z.items" :key="d.ts_code" @click="openDrawer(d.ts_code)">
              <i :style="{background:d.color}"></i>{{ d.name }} <b>{{ d.pct }}</b>
            </span>
            <span class="vchip-none" v-if="!z.items.length">—</span>
          </div>
        </div>
      </div>
    </div>
    <div class="note">分位 = 当前估值在过去五年中的位置，越低越便宜。绿点为低估区标的；越过 50% 否决线的只卖不买。</div>
  </div>

  <div class="card" v-if="marketSnapshot().length">
    <div class="card-h"><div class="t">今日市场快照</div></div>
    <div class="card-b" style="padding-top:6px;">
      <table><tbody>
        <tr v-for="s in marketSnapshot()" :key="s.tag">
          <td class="l" style="color:var(--muted);width:110px;">{{ s.tag }}</td>
          <td class="l"><span class="sym">{{ s.name }}</span></td>
          <td class="l" :style="{color:s.cls==='g'?'var(--accent)':'var(--red-ink)'}">{{ s.val }}</td>
          <td class="l" style="color:var(--muted);">{{ s.note }}</td>
        </tr>
      </tbody></table>
    </div>
  </div>

  <div class="fcards" style="margin-top:20px;">
    <div class="fcard" @click="switchTab('picks')"><div class="fk">机会</div><div class="ft">现在能开网格吗</div><div class="fd">低估 + 波动 + 不死三要素评分，估值带与 AI 研判，先看再买。</div><div class="fa">进入 →</div></div>
    <div class="fcard" @click="switchTab('planner')"><div class="fk">规划</div><div class="ft">设计你的第一张网</div><div class="fd">参数 → 档位表 → 压力测试 → 回测验证，先算最坏情况再开网。</div><div class="fa">进入 →</div></div>
    <div class="fcard" @click="switchTab('review')"><div class="fk">复盘</div><div class="ft">网格赚了多少钱</div><div class="fd">套利回合、执行纪律、收益构成，AI 周报复述（杂志模式）。</div><div class="fa">进入 →</div></div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...homeActions, openDrawer: picksActions.openDrawer, switchTab },
};

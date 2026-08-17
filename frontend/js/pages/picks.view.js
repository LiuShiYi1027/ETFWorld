// 机会页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, loadReadiness } from '../store.js';
import { picksActions } from './picks.js';
import { picksDiscoveryActions } from './discovery.js';

export const PicksView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>机会</h1>
    <p>雷达 + 估值合并视图：先评分排序，再点行进抽屉看估值带与 AI 研判。结论是研究信号，<b>不构成投资建议</b>。</p>
  </div>
  <div class="card">
    <div class="card-h"><div class="t">监控指数评分</div>
      <div class="d" style="display:flex;align-items:center;gap:10px;">
        <span v-if="readiness.length">共 <b>{{ readiness.length }}</b> 只 · 更新于 <b>{{ readiness[0].trade_date }}</b></span>
        <button class="btn sm" @click="toggleWlManage()">{{ wlManage ? '完成' : '管理监控池' }}</button>
      </div>
    </div>
    <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="radarRows().length">
      <table>
        <thead><tr><th class="l">#</th><th class="l">名称</th><th class="l">类别</th><th>估值分位</th><th>年化波动</th><th>建议格距</th><th>评分</th><th class="l">结论</th><th class="l" v-if="wlManage">管理</th><th class="l" v-else></th></tr></thead>
        <tbody>
          <tr class="clk" v-for="(r,i) in radarRows()" :key="r.ts_code" @click="openDrawer(r.ts_code)">
            <td class="l" style="color:var(--faint);">{{ String(i+1).padStart(2,'0') }}</td>
            <td class="l"><span class="sym">{{ r.name }}<small>{{ r.ts_code }}</small></span></td>
            <td class="l" style="color:var(--muted);">{{ r.category }}</td>
            <td :style="{fontWeight:600,color:r.valuation_percentile<30?'var(--accent)':r.valuation_percentile>50?'var(--red-ink)':'inherit'}">{{ r.valuation_percentile != null ? r.valuation_percentile + '%' : '—' }}</td>
            <td>{{ r.volatility != null ? r.volatility + '%' : '—' }}</td>
            <td>{{ r.suggested_grid ? r.suggested_grid.grid_step + '%' : '—' }}</td>
            <td style="font-weight:700;" :style="{color:r.level==='go'?'var(--accent)':r.level==='no'?'var(--red-ink)':r.level==='maybe'?'var(--amber-ink)':'var(--muted)'}">{{ r.score >= 0 ? r.score : '—' }}</td>
            <td class="l"><span class="chip" :class="chipCls(r.level)">{{ r.verdict }}</span></td>
            <td class="l" v-if="wlManage" @click="$event.stopPropagation();"><button class="btn sm danger" @click="wlRemove(r.ts_code, r.name)">移出</button></td>
            <td class="l" v-else style="color:var(--faint);">→</td>
          </tr>
        </tbody>
      </table>
      <div v-if="wlManage" style="margin-top:12px;">
        <div class="field" style="max-width:340px;">
          <label>添加申万行业指数（搜索行业名）</label>
          <input v-model="wlQuery" @input="wlSearch()" placeholder="如 白酒 / 半导体 / 电力">
        </div>
        <div v-if="wlResults.length" style="margin-top:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden;">
          <div class="row" v-for="w in wlResults" :key="w.ts_code" style="padding:9px 12px;">
            <div style="flex:1;min-width:0;"><div class="nm" style="font-size:13px;">{{ w.name }}</div>
              <div class="sub">{{ w.ts_code }} · {{ w.level === 'L1' ? '一级' : w.level === 'L2' ? '二级' : '三级' }}</div></div>
            <button class="btn sm" v-if="!wlInPool(w.ts_code)" :disabled="wlBusy[w.ts_code]" @click="wlAdd(w)">{{ wlBusy[w.ts_code] ? '加入中…' : '+ 加入监控' }}</button>
            <span class="chip go" v-else>已在池</span>
          </div>
        </div>
      </div>
    </div>
    <div class="empty-note" v-else-if="readinessErr">数据加载失败 · {{ readinessErr }} <button class="btn sm" @click="loadReadiness(true)">重试</button></div>
    <div class="empty-note" v-else>还没有估值数据 · 请先在 ⚙ 设置中配置 Tushare Token 并回填</div>
    <div class="note">评分口径：估值安全垫 70 分 + 波动充足度 30 分；分位 &gt;50% 直接否决。点击任意行展开右侧「指数详情」抽屉。监控池保存在本地数据库，新增指数会自动回填 5 年历史。</div>
  </div>

  <!-- 智能寻品：全池扫描 + AI 研判 -->
  <div class="card">
    <div class="card-h">
      <div class="t">智能寻品 · 申万全行业扫描</div>
      <div class="d" v-if="discoveryResult">扫 {{ discoveryResult.scanned }} 只 · 通过 {{ discoveryResult.passed }} 只 · {{ discoveryResult.finished_at }}</div>
      <div class="d" v-else-if="discoveryBusy()">{{ discoveryProgress() }}</div>
    </div>
    <div class="card-b">
      <div v-if="!discoveryResult && !discoveryBusy()" style="color:var(--muted);font-size:13px;line-height:1.8;">
        在申万一级 + 二级全部行业指数中扫描「低估 + 波动充足 + 有活跃 ETF」的品种，再由 AI 研判行业是否"不死"。
        数据筛负责过滤，AI 负责否决——不是瞎找。
      </div>
      <div class="ai-loading" v-if="discoveryBusy()">{{ discoveryProgress() }}</div>
      <div v-if="discoveryResult && discoveryItems().length">
        <table>
          <thead><tr><th class="l">行业</th><th class="l">层级</th><th>估值分位</th><th>年化波动</th><th>评分</th><th class="l">关联 ETF</th><th class="l">AI 研判</th><th class="l"></th></tr></thead>
          <tbody>
            <tr v-for="d in discoveryItems()" :key="d.ts_code">
              <td class="l"><span class="sym">{{ d.name }}<small>{{ d.ts_code }}</small></span></td>
              <td class="l" style="color:var(--muted);">{{ d.level === 'L1' ? '一级' : '二级' }}</td>
              <td style="color:var(--accent);font-weight:600;">{{ d.valuation_percentile }}%</td>
              <td>{{ d.volatility }}%</td>
              <td style="font-weight:700;color:var(--accent);">{{ d.score }}</td>
              <td class="l" style="color:var(--muted);">{{ d.etf.name }}<span style="color:var(--faint);"> · {{ d.etf.amount_yi }}亿</span></td>
              <td class="l">
                <template v-if="discoveryAiFor(d.name)">
                  <span class="chip" :class="discoveryAiFor(d.name).verdict==='适合'?'go':discoveryAiFor(d.name).verdict==='不适合'?'no':'maybe'"
                        :title="discoveryAiFor(d.name).reason + ' 风险：' + discoveryAiFor(d.name).risk">{{ discoveryAiFor(d.name).verdict }}</span>
                </template>
                <span v-else style="color:var(--faint);">—</span>
              </td>
              <td class="l" style="white-space:nowrap;">
                <button class="btn sm" v-if="!wlInPool(d.ts_code)" :disabled="wlBusy[d.ts_code]" @click="wlAddFromDiscovery(d)">{{ wlBusy[d.ts_code] ? '加入中…' : '+ 监控' }}</button>
                <span class="chip go" v-else>已在池</span>
                <button class="btn sm" @click="discoveryGoPlanner(d)">去规划 →</button>
              </td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-if="discoveryResult && !discoveryItems().length">本轮没有通过三筛的品种——市场上没有便宜又活跃的网格标的，空仓等待也是纪律。</div>
      <div class="btnrow">
        <button class="btn primary" @click="runDiscovery()" :disabled="discoveryBusy()">{{ discoveryResult ? '重新扫描' : '开始扫描' }}</button>
        <button class="btn" style="color:var(--accent);border-color:#86EFAC;" v-if="discoveryResult && discoveryItems().length && ai.enabled" @click="runDiscoveryAi()">✦ AI 研判不死属性</button>
      </div>
      <div class="ai-loading" v-if="discoveryAiLoading">AI 正在逐只研判…</div>
      <div class="note" style="padding:10px 0 0;">筛选口径：5 年综合分位 &lt;50% · 年化波动 ≥12% · 关联 ETF 日成交 ≥1 亿。AI 研判悬停标签可见理由与风险。</div>
    </div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...picksActions, ...picksDiscoveryActions, loadReadiness },
};

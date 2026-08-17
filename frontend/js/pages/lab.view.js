// 实验室页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt } from '../store.js';
import { labActions } from './lab.js';

export const LabView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>实验室</h1>
    <p>同一标的、同一段历史、同一笔资金 —— 让策略用数据对擂，验证满意再拿去执行。</p>
  </div>

  <!-- 卡片 A：单品种策略对擂 -->
  <div class="card">
    <div class="card-h"><div class="t">策略对擂</div><div class="d">持有 / 网格 / 定投 · 同一标的同向对比</div></div>
    <div class="card-b">
      <div class="formgrid" style="grid-template-columns:repeat(3,1fr);">
        <div class="field"><label>搜索 ETF（名称 / 代码）</label><input v-model="labForm.query" @input="labSearch()" placeholder="如 沪深300 / 510300"></div>
        <div class="field"><label>网格：格距% × 格数</label>
          <div style="display:flex;gap:6px;"><input v-model="labForm.grid_step" type="number" step="0.5"><input v-model="labForm.grid_count" type="number"></div></div>
        <div class="field"><label>网格每格金额 / 定投每期金额</label>
          <div style="display:flex;gap:6px;"><input v-model="labForm.amount_per_grid" type="number" step="1000"><input v-model="labForm.base_amount" type="number" step="500"></div></div>
      </div>
      <div v-if="labForm.results.length" style="margin:6px 0;border:1px solid var(--line);border-radius:8px;overflow:hidden;">
        <div class="row" v-for="e in labForm.results" :key="e.ts_code" @click="labPick(e)" style="padding:8px 12px;">
          <div style="flex:1;min-width:0;"><div class="nm" style="font-size:13px;">{{ e.name }}</div><div class="sub">{{ e.ts_code }}</div></div>
          <span style="color:var(--faint);">→</span>
        </div>
      </div>
      <div style="display:flex;align-items:center;gap:14px;flex-wrap:wrap;margin-top:4px;">
        <span style="font-size:12.5px;color:var(--muted);">当前：<b style="color:var(--text);">{{ labForm.symbol ? labForm.symbol_name + ' ' + labForm.symbol : '未选择' }}</b></span>
        <label style="font-size:12.5px;display:flex;gap:4px;align-items:center;"><input type="checkbox" :checked="labForm.strategies.includes('hold')" @change="toggleStrategy('hold')"> 持有</label>
        <label style="font-size:12.5px;display:flex;gap:4px;align-items:center;"><input type="checkbox" :checked="labForm.strategies.includes('grid')" @change="toggleStrategy('grid')"> 网格</label>
        <label style="font-size:12.5px;display:flex;gap:4px;align-items:center;"><input type="checkbox" :checked="labForm.strategies.includes('dca')" @change="toggleStrategy('dca')"> 定投</label>
        <div class="seg">
          <button :class="{on:labForm.lookback===250}" @click="setLabLookback(250)">近1年</button>
          <button :class="{on:labForm.lookback===750}" @click="setLabLookback(750)">近3年</button>
          <button :class="{on:labForm.lookback===1250}" @click="setLabLookback(1250)">近5年</button>
        </div>
        <button class="btn primary" @click="runCompare()" :disabled="labLoading">开始对比</button>
      </div>
    </div>
  </div>

  <div class="card" v-if="labLoading"><div class="card-b"><div class="ai-loading">正在拉取历史行情跑对擂…</div></div></div>
  <div class="card" v-if="labResult">
    <div class="card-h"><div class="t">{{ labResult.symbol_name || labResult.symbol }} · 净值对比（起点 = 0%）</div>
      <button class="btn sm" @click="openNoteSave('single')">存为笔记</button></div>
    <div class="card-b">
      <div id="lab-chart" style="height:300px;"></div>
      <table style="margin-top:8px;">
        <thead><tr><th class="l">策略</th><th>收益率</th><th>最大回撤</th><th>交易/投入期数</th><th>期末账户（10万起）</th></tr></thead>
        <tbody>
          <tr v-for="s in statsRows(labResult)" :key="s.key">
            <td class="l"><span :style="{color:s.color,fontWeight:700}">●</span> {{ s.name }}</td>
            <td :style="{color:s.stats.ret>=0?'var(--accent)':'var(--red-ink)',fontWeight:700}">{{ s.stats.ret>=0?'+':'' }}{{ s.stats.ret }}%</td>
            <td>{{ s.stats.max_dd }}%</td>
            <td>{{ s.stats.trades }}</td>
            <td>{{ fmt(s.stats.final_value,0) }}</td>
          </tr>
        </tbody>
      </table>
      <div class="note" style="padding:8px 0 0;">净值口径：同一 10 万预算，未投入的钱计为账户现金；定投按关联指数时点分位调整金额。未计手续费，历史不代表未来。</div>
    </div>
  </div>

  <!-- 卡片 B：轮动沙盒 -->
  <div class="card">
    <div class="card-h"><div class="t">轮动沙盒</div><div class="d">每个周期满仓持有动量最强者 · 全负动量空仓</div></div>
    <div class="card-b">
      <div style="display:flex;align-items:center;gap:8px;flex-wrap:wrap;margin-bottom:10px;">
        <span class="chip blue" v-for="p in rotForm.pool" :key="p.symbol" style="cursor:pointer;" :title="'点击移除'" @click="rotRemove(p.symbol)">{{ p.symbol_name }} ✕</span>
        <button class="btn sm" @click="rotResetPool()">默认池</button>
      </div>
      <div style="display:flex;gap:10px;align-items:flex-end;flex-wrap:wrap;">
        <div class="field" style="flex:2;min-width:220px;"><label>加入品种（名称 / 代码）</label><input v-model="rotForm.query" @input="rotSearch()" placeholder="搜索后点击加入品种池"></div>
        <div class="field"><label>动量窗口</label>
          <div class="seg"><button :class="{on:rotForm.window===10}" @click="setRotWindow(10)">10日</button><button :class="{on:rotForm.window===20}" @click="setRotWindow(20)">20日</button><button :class="{on:rotForm.window===30}" @click="setRotWindow(30)">30日</button></div></div>
        <div class="field"><label>调仓频率</label>
          <div class="seg"><button :class="{on:rotForm.rebalance==='weekly'}" @click="setRotRebalance('weekly')">每周</button><button :class="{on:rotForm.rebalance==='monthly'}" @click="setRotRebalance('monthly')">每月</button></div></div>
        <div class="field"><label>窗口</label>
          <div class="seg"><button :class="{on:rotForm.lookback===250}" @click="setRotLookback(250)">近1年</button><button :class="{on:rotForm.lookback===750}" @click="setRotLookback(750)">近3年</button><button :class="{on:rotForm.lookback===1250}" @click="setRotLookback(1250)">近5年</button></div></div>
        <button class="btn primary" @click="runRotation()" :disabled="rotLoading">开始回测</button>
      </div>
      <div v-if="rotForm.results.length" style="margin-top:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden;">
        <div class="row" v-for="e in rotForm.results" :key="e.ts_code" @click="rotPick(e)" style="padding:8px 12px;">
          <div style="flex:1;min-width:0;"><div class="nm" style="font-size:13px;">{{ e.name }}</div><div class="sub">{{ e.ts_code }}</div></div>
          <span style="color:var(--faint);">+</span>
        </div>
      </div>
    </div>
  </div>

  <div class="card" v-if="rotLoading"><div class="card-b"><div class="ai-loading">正在回测轮动…</div></div></div>
  <div class="card" v-if="rotResult">
    <div class="card-h"><div class="t">轮动 vs 池内持有 · {{ rotResult.window }}日动量 · {{ rotResult.rebalance==='monthly'?'每月':'每周' }}调仓</div>
      <button class="btn sm" @click="openNoteSave('rotation')">存为笔记</button></div>
    <div class="card-b">
      <div id="rot-chart" style="height:300px;"></div>
      <table style="margin-top:8px;">
        <thead><tr><th class="l">策略</th><th>收益率</th><th>最大回撤</th><th>换仓次数</th><th>期末账户（10万起）</th></tr></thead>
        <tbody>
          <tr v-for="s in statsRows(rotResult)" :key="s.key">
            <td class="l"><span :style="{color:s.color,fontWeight:700}">●</span> {{ s.name }}<span style="color:var(--faint);font-size:11px;" v-if="s.key==='rotation'">（期末持有：{{ s.stats.holding || '空仓' }}）</span></td>
            <td :style="{color:s.stats.ret>=0?'var(--accent)':'var(--red-ink)',fontWeight:700}">{{ s.stats.ret>=0?'+':'' }}{{ s.stats.ret }}%</td>
            <td>{{ s.stats.max_dd }}%</td>
            <td>{{ s.stats.trades }}</td>
            <td>{{ fmt(s.stats.final_value,0) }}</td>
          </tr>
        </tbody>
      </table>
    </div>
  </div>

  <!-- 研究笔记 -->
  <div class="card">
    <div class="card-h"><div class="t">研究笔记</div><div class="d">回测结论的快照 · 共 {{ labNotes.length }} 条</div></div>
    <div class="card-b" style="padding:8px 0 10px;" v-if="labNotes.length">
      <div class="row" v-for="n in labNotes" :key="n.id" @click="replayNote(n)">
        <div style="flex:1;min-width:0;">
          <div class="nm">{{ n.title }}</div>
          <div class="sub">{{ noteStatsLine(n) }}<template v-if="n.note"> · {{ n.note }}</template></div>
        </div>
        <span style="color:var(--faint);font-size:12px;">{{ (n.created_at||'').slice(0,10) }}</span>
        <button class="btn sm danger" @click="$event.stopPropagation();delLabNote(n,$event.currentTarget)">删</button>
      </div>
    </div>
    <div class="empty-note" v-else>还没有研究笔记 · 跑一轮对擂后点「存为笔记」</div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...labActions, fmt },
};

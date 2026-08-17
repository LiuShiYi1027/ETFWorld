// 今天页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt, switchTab } from '../store.js';
import { todayActions } from './today.js';
import { dcaActions } from './dca.js';
import { picksActions } from './picks.js';
import { rotationActions } from './rotation.js';

export const TodayView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>今天</h1>
    <p>先看今天要做什么，再看组合与机会 · {{ clock }}<template v-if="dataStatus && dataStatus.latest_date"> · 数据截至 {{ dataStatus.latest_date }}</template><template v-if="dataStatus && dataStatus.updating"> · 更新中…</template></p>
  </div>

  <div class="card">
    <div class="card-h"><div class="t">今日待办</div><div class="d">距现价 &lt;2% 的档位 · <b>{{ todoList().length }} 条</b></div></div>
    <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="todoList().length">
      <table>
        <thead><tr><th class="l">方向</th><th class="l">标的</th><th>档位价</th><th>份额</th><th>金额</th><th class="l">所属计划</th><th>距触发</th><th class="l"></th></tr></thead>
        <tbody>
          <tr v-for="t in todoList()" :key="t.plan_id+t.direction+t.level">
            <td class="l"><span class="dir" :class="t.direction==='buy'?'buy':'sell'"><i></i>{{ t.direction==='buy'?'买入':'卖出' }}</span></td>
            <td class="l"><span class="sym">{{ t.symbol_name || t.symbol }}<small>{{ t.symbol }}</small></span></td>
            <td>{{ fmt(t.price) }}</td><td>{{ t.shares.toLocaleString('zh-CN') }}</td><td>{{ fmt(t.amount,0) }}</td>
            <td class="l" style="color:var(--muted);">{{ t.plan_name }}</td>
            <td><span class="pill" :class="t.dist_pct<1?'hot':'warm'">{{ t.dist_pct }}%</span></td>
            <td class="l" style="white-space:nowrap;">
              <button class="btn sm" style="color:var(--accent);border-color:#86EFAC;" @click="completeTodo(t)">已成交</button>
              <button class="btn sm" @click="dismissTodo(t)">稍后</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="empty-note" v-else>今日无临近触发的档位 · 网格在安静工作</div>
    <div class="note">网格不自动下单，待办只负责提醒，纪律由你执行。每行：方向 · 标的 · 档位价 × 份额 = 金额 · 所属计划 · 距现价。</div>
  </div>

  <div class="card" v-if="dcaList().length">
    <div class="card-h"><div class="t">定投</div><div class="d">估值增强：低估多投 · 正常少投 · 高估停投 · <b>{{ dcaList().length }} 条</b></div></div>
    <div class="card-b" style="padding:8px 0 10px;">
      <div class="row" v-for="t in dcaList()" :key="t.dca_plan_id+t.period_key">
        <span class="dir buy"><i></i></span>
        <div style="flex:1;min-width:0;">
          <div class="nm">{{ t.name }} <span class="chip" :class="dcaChip(t).cls">{{ dcaChip(t).text }}</span>
            <span style="color:var(--faint);font-size:12px;">{{ t.symbol_name || t.symbol }} · {{ t.period_label }}</span></div>
          <div class="sub">{{ t.label }}<template v-if="t.action==='invest'"> · 基准 {{ fmt(t.base_amount,0) }} → 建议 <b>{{ fmt(t.amount,0) }}</b> 元</template></div>
        </div>
        <div style="white-space:nowrap;" v-if="t.action==='invest'">
          <button class="btn sm" style="color:var(--accent);border-color:#86EFAC;" @click="completeDca(t)">已投</button>
          <button class="btn sm" @click="dismissDca(t)">稍后</button>
        </div>
        <div v-else><button class="btn sm" @click="dismissDca(t)">知道了</button></div>
      </div>
    </div>
    <div class="note">定投不自动扣款，待办只负责提醒。金额 = 基准 × 分位倍数；停投与止盈提示只提醒、不代操作。</div>
  </div>

  <div class="card" v-if="rotList().length">
    <div class="card-h"><div class="t">轮动调仓</div><div class="d">动量最强者满仓 · 全负空仓 · <b>{{ rotList().length }} 条</b></div></div>
    <div class="card-b" style="padding:8px 0 10px;">
      <div class="row" v-for="t in rotList()" :key="t.rotation_plan_id">
        <span class="dir" :class="t.action==='enter'?'buy':'sell'"><i></i></span>
        <div style="flex:1;min-width:0;">
          <div class="nm">{{ t.name }} <span class="chip" :class="t.action==='exit'?'no':t.action==='enter'?'go':'maybe'">{{ rotActionLabel(t) }}</span>
            <span style="color:var(--faint);font-size:12px;">{{ t.period_label }} · {{ t.window }}日动量</span></div>
          <div class="sub">
            <template v-if="t.holding">现持 {{ t.holding.symbol_name }} {{ t.holding.shares.toLocaleString('zh-CN') }} 份 → </template>
            <template v-if="t.target">目标 <b>{{ t.target.symbol_name }}</b>（动量 {{ t.target.momentum }}%）</template>
            <template v-else>池内动量全负，转为空仓</template>
          </div>
        </div>
        <div style="white-space:nowrap;">
          <button class="btn sm danger" v-if="t.holding && !t.sell_done" @click="rotRecord(t,'sell')">记卖出</button>
          <button class="btn sm" style="color:var(--accent);border-color:#86EFAC;" v-if="t.target && !t.buy_done" @click="rotRecord(t,'buy')">记买入</button>
        </div>
      </div>
    </div>
    <div class="note">轮动不自动下单。调仓是双腿动作：先记卖出、再记买入，两腿都完成本期待办才消失。</div>
  </div>

  <div class="alerts" v-if="alertList().length">
    <div class="alert" v-for="al in alertList()" :key="al.title+al.chip" :class="al.kind">
      <div class="at">{{ al.title }}<span class="chip" :class="al.chipCls">{{ al.chip }}</span></div>
      <div class="ab" v-html="al.body"></div>
      <div class="aa"><button class="btn sm" :class="{danger:al.kind==='crit'}" @click="alertAction(al)">
        {{ al.act==='brk'?'处置 ▸':al.act==='rebase'?'↑ 上移重开':'去机会页看看 →' }}</button></div>
    </div>
  </div>

  <div class="kpis" v-if="briefKpis()">
    <div class="kpi"><div class="k">本金</div><div class="v">{{ fmt(briefKpis().principal/10000,1) }}万</div><div class="f">入金 − 出金</div></div>
    <div class="kpi"><div class="k">现金</div><div class="v">{{ fmt(briefKpis().cash/10000,1) }}万</div><div class="f">可用弹药</div></div>
    <div class="kpi"><div class="k">满格 / 本金</div><div class="v" :class="briefKpis().safety_warn?'bad':'good'">{{ briefKpis().safety_ratio!=null?Math.round(briefKpis().safety_ratio*100)+'%':'—' }}</div><div class="f">安全线 70% 内</div></div>
  </div>

  <div class="grid2">
    <div class="card">
      <div class="card-h"><div class="t">机会精选</div><button class="btn sm" @click="switchTab('picks')">全部 →</button></div>
      <div class="card-b" style="padding:8px 0 10px;" v-if="topPicks().length">
        <div class="row" v-for="(p,i) in topPicks()" :key="p.ts_code" @click="switchTab('picks');openDrawer(p.ts_code)">
          <span class="rank">{{ i+1 }}</span>
          <div style="flex:1;min-width:0;"><div class="nm">{{ p.name }} <span class="chip go">{{ p.verdict }}</span></div>
            <div class="sub">估值分位 {{ p.valuation_percentile ?? '—' }}% · 年化波动 {{ p.volatility ?? '—' }}% · 建议格距 {{ p.suggested_grid ? p.suggested_grid.grid_step+'%' : '—' }}</div></div>
          <div class="sc good">{{ p.score }}</div>
        </div>
      </div>
      <div class="empty-note" v-else>今日暂无「适合开启」级机会</div>
    </div>
    <div class="card">
      <div class="card-h"><div class="t">最新成交</div><button class="btn sm" @click="switchTab('plans')">计划 →</button></div>
      <div class="card-b" style="padding:8px 0 10px;" v-if="recentTrades.length">
        <div class="row" v-for="t in recentTrades" :key="t.id">
          <span class="dir" :class="t.direction==='buy'?'buy':'sell'"><i></i></span>
          <div style="flex:1;min-width:0;"><div class="nm">{{ t.symbol_name || t.symbol }} <span style="color:var(--faint);font-size:12px;">{{ t.grid_level ? 'G'+t.grid_level : '' }}</span></div>
            <div class="sub">{{ t.trade_date }} · {{ t.price }} × {{ t.shares.toLocaleString('zh-CN') }}</div></div>
          <div class="amt">{{ fmt(t.amount,0) }}</div>
        </div>
      </div>
      <div class="empty-note" v-else>还没有成交记录</div>
    </div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...todayActions, completeDca: dcaActions.completeDca,
             dismissDca: dcaActions.dismissDca, dcaList: dcaActions.dcaList,
             dcaChip: dcaActions.dcaChip, openDrawer: picksActions.openDrawer,
             rotList: rotationActions.rotList, rotActionLabel: rotationActions.rotActionLabel,
             rotRecord: rotationActions.rotRecord,
             switchTab, fmt },
};

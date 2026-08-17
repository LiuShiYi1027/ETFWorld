// 组合页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt } from '../store.js';
import { portfolioActions } from './portfolio.js';

export const PortfolioView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>组合</h1>
    <p>三账户视图：底仓吃趋势、网格吃震荡、现金是弹药。回答一个问题 —— <b>若全部网格买满，我的钱够不够？</b></p>
    <div class="spacer"></div>
    <button class="btn primary" @click="openFundFlow()">+ 记一笔资金流水</button>
  </div>

  <div class="kpis" v-if="pf()">
    <div class="kpi" v-for="k in pfKpis()" :key="k.k">
      <div class="k">{{ k.k }}</div><div class="v sm" :class="{good:k.good}">{{ k.v }}</div><div class="f">{{ k.f }}</div>
    </div>
  </div>
  <div class="empty-note" v-if="pf() && !pf().principal">还没有资金流水 · 「记一笔资金流水」建立本金口径后，安全线才会工作</div>

  <div class="card" v-if="acctSegments().length">
    <div class="card-h"><div class="t">三账户占比</div><div class="d">按市值</div></div>
    <div class="card-b">
      <div class="acct-bar"><i v-for="s in acctSegments()" :key="s.name" :style="{width:s.pct+'%',background:s.color}" :title="s.name"></i></div>
      <div class="acct-legend">
        <div class="acct-leg" v-for="s in acctSegments()" :key="s.name"><span class="sw" :style="{background:s.color}"></span>{{ s.name }} <span class="pc">{{ s.label }}</span></div>
      </div>
    </div>
  </div>

  <div class="card" v-if="safety()">
    <div class="card-h"><div class="t">安全线 · 满格资金 ÷ 本金</div><div class="d">ACTIVE + PAUSED 满格合计 <b>{{ safety().full }}</b> ÷ 本金 <b>{{ safety().principal }}</b></div></div>
    <div class="card-b">
      <div class="safe-wrap">
        <div class="safe-scale">
          <div class="safe-fill" :style="{width:Math.min(100,safety().ratioPct)+'%'}"></div>
          <div class="safe-warnline" style="left:70%;"></div>
          <div class="safe-warnlab" style="left:70%;">警告线 70%</div>
          <div class="safe-ptr" :style="{left:Math.min(98,safety().ratioPct)+'%'}"><span class="pv">{{ safety().ratioPct }}%</span><div class="pd"></div></div>
        </div>
        <div class="safe-ticks"><span>0%</span><span>25%</span><span>50%</span><span>75%</span><span>100%</span></div>
      </div>
    </div>
    <div class="note">若所有网格按计划买满，需动用本金的 {{ safety().ratioPct }}%；现金 {{ safety().cash }}，缺口 {{ safety().gap }}。{{ safety().warn ? '已越过警告线：新建计划前请先缩减格数或每格金额。' : '处于安全区。' }}</div>
  </div>

  <div class="card" v-if="industrySegments().length">
    <div class="card-h"><div class="t">行业分布</div><div class="d">全部持仓按市值聚合 · 单一行业超 40% 标红</div></div>
    <div class="card-b">
      <div class="acct-bar"><i v-for="s in industrySegments()" :key="s.name" :style="{width:s.pct+'%',background:s.color}" :title="s.name"></i></div>
      <div class="acct-legend">
        <div class="acct-leg" v-for="s in industrySegments()" :key="s.name"><span class="sw" :style="{background:s.color}"></span>{{ s.name }} <span class="pc">{{ s.label }}</span></div>
      </div>
    </div>
    <div class="note" v-if="industryWarn()" style="color:var(--red-ink);">单一行业占比超过 40%：看似分散的多个网格可能押在同一条赛道上，建议开新网前优先补齐其他行业。</div>
    <div class="note" v-else>行业归属按持仓标的关联监控指数判定；未匹配的归入「其他」。</div>
  </div>

  <div class="card" v-if="alloc()">
    <div class="card-h"><div class="t">资金分配建议</div><div class="d">现金水位 × 雷达评分</div></div>
    <div class="card-b">
      <div style="font-weight:700;margin-bottom:4px;" :style="{color:alloc().level==='warn'?'var(--red-ink)':alloc().level==='info'?'var(--accent)':'var(--text)'}">{{ alloc().headline }}</div>
      <div style="font-size:12.5px;color:var(--muted);line-height:1.7;">{{ alloc().detail }}</div>
      <div v-if="alloc().candidates && alloc().candidates.length" style="margin-top:8px;">
        <div class="clk" v-for="c in alloc().candidates" :key="c.ts_code" @click="gotoPick(c.ts_code)"
             style="display:flex;align-items:center;gap:8px;padding:7px 2px;border-top:1px solid var(--line);">
          <span style="font-weight:600;">{{ c.name }}</span>
          <span class="chip go">评分 {{ Math.round(c.score) }}</span>
          <span style="color:var(--faint);font-size:12px;">分位 {{ c.valuation_percentile }}%</span>
          <span style="margin-left:auto;color:var(--faint);font-size:12px;">去机会页 →</span>
        </div>
      </div>
    </div>
  </div>

  <div class="grid2" style="grid-template-columns:1.15fr 1fr;">
    <div class="card">
      <div class="card-h"><div class="t">底仓持仓</div><div class="d">非网格的手工持仓 + 网格留存</div></div>
      <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="coreRows().length">
        <table>
          <thead><tr><th class="l">标的</th><th>份额</th><th>成本</th><th>市值</th><th>浮盈</th></tr></thead>
          <tbody>
            <tr v-for="p in coreRows()" :key="p.symbol">
              <td class="l"><span class="sym">{{ p.symbol_name || p.symbol }}<small>{{ p.symbol }}</small></span></td>
              <td>{{ p.shares.toLocaleString('zh-CN') }}</td><td>{{ fmt(p.avg_cost,3) }}</td>
              <td>{{ p.market_value != null ? fmt(p.market_value,0) : '—' }}</td>
              <td :style="{color:p.unrealized_pnl>=0?'var(--accent)':'var(--red-ink)',fontWeight:600}">{{ p.unrealized_pnl != null ? (p.unrealized_pnl>=0?'+':'')+fmt(p.unrealized_pnl,0) : '—' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-else>没有底仓持仓 · 录入成交时不关联计划即计入底仓</div>
      <div class="note" v-if="retainedRows().length">留存底仓（网格 2.0 零成本份额）：{{ retainedRows().map(r=>(r.symbol_name||r.symbol)+' '+r.shares+'股').join('、') }}</div>
    </div>
    <div class="card">
      <div class="card-h"><div class="t">资金流水</div><div class="d">本金 = Σ入金 − Σ出金</div></div>
      <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="flowRows().length">
        <table>
          <thead><tr><th class="l">日期</th><th class="l">方向</th><th>金额</th><th class="l">备注</th><th class="l"></th></tr></thead>
          <tbody>
            <tr v-for="f in flowRows()" :key="f.id">
              <td class="l">{{ f.flow_date }}</td>
              <td class="l"><span class="dir" :class="f.direction==='deposit'?'buy':'sell'"><i></i>{{ f.direction==='deposit'?'入金':'出金' }}</span></td>
              <td>{{ fmt(f.amount,0) }}</td><td class="l" style="color:var(--muted);">{{ f.note || '' }}</td>
              <td class="l"><button class="btn sm danger" @click="delFlow(f,$event.currentTarget)">删</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-else>还没有资金流水</div>
    </div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...portfolioActions, fmt },
};

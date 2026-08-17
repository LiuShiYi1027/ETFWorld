// 复盘页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt } from '../store.js';
import { reviewActions } from './review.js';
import { plansActions } from './plans.js';

export const ReviewView = {
  template: `
<section class="page">
  <div class="ed">
    <div class="pagehead">
      <h1>复盘</h1>
      <p>网格的钱是一格一格套利攒出来的。本页回答三个问题：赚了多少、纪律执行得怎样、下周怎么办。</p>
      <div class="spacer"></div>
      <button class="btn primary" @click="runWeekly()" v-if="ai.enabled">{{ weekly ? '收起周报' : weeklyLoading ? '生成中…' : '✦ AI 周报复述' }}</button>
    </div>

    <div class="ed-sec">
      <div class="ed-kpis">
        <div class="ed-kpi" v-for="k in rvKpis()" :key="k.k">
          <div class="k">{{ k.k }}</div><div class="v" :class="k.cls">{{ k.v }}</div><div class="f">{{ k.f }}</div>
        </div>
      </div>
    </div>

    <div class="ed-sec" v-if="weekly || weeklyLoading">
      <div class="ed-h"><span class="no">一</span><span class="t">周报复述</span><span class="d">AI 研究助手 · 按周缓存 · 不构成投资建议</span></div>
      <div class="ai-week" v-if="weekly">
        <div class="wt">本周网格周报 <span class="sub">{{ ai.model || 'AI' }} 生成</span></div>
        <div class="wk-sec"><div class="wk-k">本周做了什么</div><div class="wk-p">{{ weekly.done }}</div></div>
        <div class="wk-sec"><div class="wk-k">纪律检查</div><div class="wk-p">{{ weekly.discipline }}</div></div>
        <div class="wk-sec"><div class="wk-k">下周关注</div><div class="wk-p">{{ weekly.next }}</div></div>
        <div class="wk-disc">AI 定位是研究助手：复述数据、提示纪律，不给买卖指令。</div>
      </div>
      <div class="ai-week" v-else><div class="ai-loading">AI 正在撰写周报…</div></div>
    </div>

    <div class="ed-sec">
      <div class="ed-h"><span class="no">二</span><span class="t">分计划复盘</span><span class="d">回合 = 一次完整的买→卖 · 含已归档计划</span></div>
      <div v-if="planRows().length">
        <table>
          <thead><tr><th class="l">计划</th><th>版本</th><th>套利回合</th><th>已实现</th><th>留存</th><th>该买没买</th><th>该卖没卖</th><th class="l">状态</th></tr></thead>
          <tbody>
            <tr class="clk" v-for="p in planRows()" :key="p.plan_id" @click="open(p.plan_id)">
              <td class="l"><span class="sym">{{ p.name }}<small>{{ p.symbol }}</small></span></td>
              <td>{{ p.version }}</td><td>{{ p.rounds }}</td>
              <td :style="{color:p.realized_pnl>=0?'var(--pine)':'var(--seal)',fontWeight:600}">{{ p.realized_pnl>=0?'+':'' }}{{ fmt(p.realized_pnl,0) }}</td>
              <td style="color:var(--amber-ink);">{{ p.retained_shares ? p.retained_shares + ' 股' : '—' }}</td>
              <td :style="p.missed_buy>0?'color:var(--seal);font-weight:600;':''">{{ p.missed_buy }}</td>
              <td :style="p.missed_sell>0?'color:var(--seal);font-weight:600;':''">{{ p.missed_sell }}</td>
              <td class="l"><span class="chip" :class="statusChip[p.status]||'wait'">{{ p.status.toUpperCase() }}</span></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-else>还没有计划可复盘</div>
      <div class="ed-note">纪律判定：日线穿越档位价（±1% 容差）且七个交易日内无对应方向成交，记一次违约。网格收益 = 无数量级的小钱 × 次数 × 纪律——违约次数是比收益率更先看的指标。</div>
    </div>

    <div class="ed-sec" v-if="breakdown().length">
      <div class="ed-h"><span class="no">三</span><span class="t">收益构成</span><span class="d">网格套利 vs 底仓浮盈 vs 留存</span></div>
      <div class="card-b">
        <div class="acct-bar" style="height:26px;"><i v-for="s in breakdown()" :key="s.name" :style="{width:s.pct+'%',background:s.color}" :title="s.name"></i></div>
        <div class="acct-legend">
          <div class="acct-leg" v-for="s in breakdown()" :key="s.name"><span class="sw" :style="{background:s.color}"></span>{{ s.name }} <span class="pc">{{ s.label }}</span></div>
        </div>
      </div>
      <div class="ed-note">底仓吃趋势，网格吃震荡——两者不是竞争关系。上涨市里底仓占比高是正常的，不必因此怀疑网格。</div>
    </div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...reviewActions, open: plansActions.open, fmt },
};

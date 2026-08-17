// 计划页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt, switchTab } from '../store.js';
import { plansActions } from './plans.js';
import { dcaActions } from './dca.js';

export const PlansView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>计划</h1>
    <p>计划 = 参数 + 执行状态 + 成交记录的归档单元。破网计划需人工处置，不会自动恢复。</p>
    <div class="spacer"></div>
    <button class="btn" @click="openDcaCreate()">+ 新建定投计划</button>
    <button class="btn primary" @click="switchTab('planner')">+ 新建网格计划</button>
  </div>
  <div class="card">
    <div class="card-h"><div class="t">计划列表</div><div class="d">共 {{ plans.length }} 个</div></div>
    <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="plans.length">
      <table>
        <thead><tr><th class="l">名称</th><th class="l">标的</th><th>版本</th><th>基准价</th><th>格距</th><th>格数</th><th>满格资金</th><th class="l">状态</th><th class="l">操作</th></tr></thead>
        <tbody>
          <tr class="clk" v-for="p in plans" :key="p.id" @click="open(p.id)">
            <td class="l"><span class="sym">{{ p.name }}</span></td>
            <td class="l">{{ p.symbol_name || '' }} <span style="color:var(--faint);font-size:11px;">{{ p.symbol }}</span>
              <span class="chip" v-if="planValuation(p)" :class="planValuation(p).cls" style="margin-left:4px;" :title="planValuation(p).name + ' 估值分位'">{{ planValuation(p).pct }}%</span></td>
            <td>{{ p.version }}<span class="chip blue" v-if="p.grid_mode==='shares'" style="margin-left:4px;">等份额</span></td><td>{{ p.base_price }}</td><td>{{ p.grid_step }}%</td><td>{{ p.grid_count }}</td>
            <td>{{ fmt(planCapital(p),0) }}</td>
            <td class="l"><span class="chip" :class="statusChip[p.status]||'wait'">{{ p.status.toUpperCase() }}</span></td>
            <td class="l" @click="$event.stopPropagation();">
              <button class="btn sm" @click="open(p.id)">查看</button>
              <button class="btn sm" style="color:var(--accent);border-color:#86EFAC;" @click="open(p.id,true)">AI</button>
              <button class="btn sm danger" @click="delPlan(p,$event.currentTarget)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="empty-note" v-else>还没有网格计划 · 去「规划」创建第一张网格</div>
  </div>

  <!-- 定投计划 -->
  <div class="card">
    <div class="card-h"><div class="t">定投计划</div><div class="d">估值增强：低估多投 · 高估停投 · 共 {{ dcaRows().length }} 个</div></div>
    <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="dcaRows().length">
      <table>
        <thead><tr><th class="l">名称</th><th class="l">标的</th><th>频率</th><th>基准金额</th><th class="l">当前建议</th><th class="l">状态</th><th class="l">操作</th></tr></thead>
        <tbody>
          <tr class="clk" v-for="p in dcaRows()" :key="p.id" @click="openDcaDetail(p)">
            <td class="l"><span class="sym">{{ p.name }}</span></td>
            <td class="l">{{ p.symbol_name || '' }} <span style="color:var(--faint);font-size:11px;">{{ p.symbol }}</span></td>
            <td>{{ dcaFreqLabel(p) }}</td>
            <td>{{ fmt(p.base_amount,0) }}</td>
            <td class="l"><span class="chip" :class="dcaSugChip(p).cls">{{ dcaSugChip(p).text }}</span></td>
            <td class="l"><span class="chip" :class="statusChip[p.status]||'wait'">{{ p.status.toUpperCase() }}</span></td>
            <td class="l" @click="$event.stopPropagation();">
              <button class="btn sm" @click="openDcaDetail(p)">查看</button>
              <button class="btn sm" v-if="p.status==='active'" @click="dcaSetStatus(p,'paused')">暂停</button>
              <button class="btn sm" v-if="p.status==='paused'" @click="dcaSetStatus(p,'active')">恢复</button>
              <button class="btn sm danger" @click="delDcaPlan(p,$event.currentTarget)">删除</button>
            </td>
          </tr>
        </tbody>
      </table>
    </div>
    <div class="empty-note" v-else>还没有定投计划 · 点右上角「+ 新建定投计划」开始低估定投</div>
  </div>

  <!-- 定投详情 -->
  <div id="dca-detail" v-if="dcaDetail">
    <div class="card">
      <div class="card-h">
        <div class="t">定投详情 · {{ dcaDetail.name }} <span class="chip" :class="statusChip[dcaDetail.status]||'wait'" style="margin-left:6px;">{{ dcaDetail.status.toUpperCase() }}</span></div>
        <div class="d">{{ dcaDetail.symbol_name || dcaDetail.symbol }} · {{ dcaFreqLabel(dcaDetail) }}定投 · 基准 {{ fmt(dcaDetail.base_amount,0) }} 元</div>
      </div>
      <div class="card-b">
        <div class="kpis flat" style="grid-template-columns:repeat(4,1fr);margin-bottom:10px;" v-if="dcaDetail.summary">
          <div class="kpi"><div class="k">累计投入</div><div class="v sm">{{ fmt(dcaDetail.summary.total_invested,0) }}</div></div>
          <div class="kpi"><div class="k">已投期数</div><div class="v sm good">{{ dcaDetail.summary.periods_done }}</div></div>
          <div class="kpi"><div class="k">缺席期数</div><div class="v sm" :class="{bad:dcaDetail.summary.periods_missed>0}">{{ dcaDetail.summary.periods_missed }}</div></div>
          <div class="kpi"><div class="k">当前建议</div><div class="v sm">{{ dcaSugChip(dcaDetail).text }}</div></div>
        </div>
        <div class="btnrow">
          <button class="btn" v-if="dcaDetail.status==='active'" @click="dcaSetStatus(dcaDetail,'paused');closeDcaDetail()">暂停</button>
          <button class="btn primary" v-if="dcaDetail.status==='paused'" @click="dcaSetStatus(dcaDetail,'active');closeDcaDetail()">恢复</button>
          <button class="btn danger" v-if="dcaDetail.status!=='closed'" @click="dcaSetStatus(dcaDetail,'closed');closeDcaDetail()">结束定投</button>
          <button class="btn" @click="closeDcaDetail()">收起</button>
        </div>

        <!-- 定投回测：普通 vs 估值增强 -->
        <div style="margin-top:16px;border-top:1px solid var(--line);padding-top:14px;">
          <div style="display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin-bottom:8px;">
            <div style="font-size:12px;font-weight:700;color:var(--muted);">回测 · 普通定投 vs 估值增强</div>
            <div class="seg">
              <button :class="{on:dcaLookback===250}" @click="runDcaBacktest(250)">近1年</button>
              <button :class="{on:dcaLookback===750}" @click="runDcaBacktest(750)">近3年</button>
              <button :class="{on:dcaLookback===1250}" @click="runDcaBacktest(1250)">近5年</button>
            </div>
            <span style="font-size:12px;color:var(--faint);" v-if="dcaBt && dcaBt.index_name">按 {{ dcaBt.index_name }} 的时点分位调整每期金额</span>
            <span style="font-size:12px;color:var(--amber-ink);" v-if="dcaBt && !dcaBt.has_valuation">未关联监控指数 · 增强与普通一致</span>
          </div>
          <div class="ai-loading" v-if="dcaBtLoading">回测计算中…</div>
          <template v-if="dcaBt && dcaBt.n">
            <div id="dca-bt" style="height:230px;"></div>
            <table style="margin-top:8px;">
              <thead><tr><th class="l">口径</th><th>累计投入</th><th>期末市值</th><th>收益率</th><th>已投/总期数</th></tr></thead>
              <tbody>
                <tr>
                  <td class="l">普通定投</td>
                  <td>{{ fmt(dcaBt.plain.total_invested,0) }}</td>
                  <td>{{ fmt(dcaBt.plain.final_value,0) }}</td>
                  <td :style="{color:dcaBt.plain.return_pct>=0?'var(--accent)':'var(--red-ink)',fontWeight:600}">{{ dcaBt.plain.return_pct>=0?'+':'' }}{{ dcaBt.plain.return_pct }}%</td>
                  <td>{{ dcaBt.plain.periods_invested }}/{{ dcaBt.plain.periods }}</td>
                </tr>
                <tr>
                  <td class="l"><b>估值增强定投</b></td>
                  <td>{{ fmt(dcaBt.enhanced.total_invested,0) }}</td>
                  <td>{{ fmt(dcaBt.enhanced.final_value,0) }}</td>
                  <td :style="{color:dcaBt.enhanced.return_pct>=0?'var(--accent)':'var(--red-ink)',fontWeight:700}">{{ dcaBt.enhanced.return_pct>=0?'+':'' }}{{ dcaBt.enhanced.return_pct }}%</td>
                  <td>{{ dcaBt.enhanced.periods_invested }}/{{ dcaBt.enhanced.periods }}（停投 {{ dcaBt.enhanced.paused_periods }} 期）</td>
                </tr>
              </tbody>
            </table>
            <div class="note" style="padding:8px 0 0;">每期首个交易日按收盘价买入，份额整手取整；增强口径按当日往前 5 年的 PE/PB 时点分位调整金额。未计手续费，历史不代表未来。</div>
          </template>
        </div>
      </div>
      <div class="card-h" style="border-top:1px solid var(--line);padding-top:14px;"><div class="t">成交记录</div><div class="d">{{ dcaDetailTrades.length }} 笔</div></div>
      <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="dcaDetailTrades.length">
        <table>
          <thead><tr><th class="l">日期</th><th class="l">方向</th><th>价格</th><th>份额</th><th>金额</th><th class="l">备注</th></tr></thead>
          <tbody>
            <tr v-for="t in dcaDetailTrades" :key="t.id">
              <td class="l">{{ t.trade_date }}</td>
              <td class="l"><span class="dir" :class="t.direction==='buy'?'buy':'sell'"><i></i>{{ t.direction==='buy'?'买入':'卖出' }}</span></td>
              <td>{{ t.price }}</td><td>{{ t.shares.toLocaleString('zh-CN') }}</td><td>{{ fmt(t.amount,0) }}</td>
              <td class="l" style="color:var(--muted);">{{ t.note || '' }}</td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-else>还没有定投成交 · 今天页出现定投待办后点「已投」记录</div>
    </div>
  </div>

  <!-- 计划详情 -->
  <div id="plan-detail" v-if="detail">
    <div class="card">
      <div class="card-h">
        <div class="t">计划详情 · {{ detail.name }} <span class="chip" :class="statusChip[detail.status]||'wait'" style="margin-left:6px;">{{ detail.status.toUpperCase() }}</span></div>
        <div class="d">{{ detail.symbol_name || detail.symbol }} · {{ detail.version }} · {{ detail.grid_mode==='shares' ? '等份额' : '等金额' }} · 基准 {{ detail.base_price }} · {{ detail.grid_step }}% × {{ detail.grid_count }} 格 · 满格 {{ fmt(planCapital(detail),0) }}</div>
      </div>
      <div class="card-b">
        <div style="display:flex;align-items:baseline;justify-content:space-between;margin-bottom:10px;flex-wrap:wrap;gap:8px;">
          <div style="font-size:12px;font-weight:700;color:var(--muted);">档位执行状态棋盘</div>
          <div style="font-size:12px;color:var(--faint);" v-if="rulerInfo() && rulerInfo().cur != null">现价 <b style="color:var(--text);">{{ rulerInfo().cur }}</b></div>
        </div>
        <div class="board">
          <div class="cell" v-for="c in boardCells()" :key="c.no" :class="'st-'+c.state" :title="'买入价 '+c.buy">
            <span class="gno">G{{ c.no }}</span><span class="gst">{{ c.label }}</span>
          </div>
        </div>
        <div class="board-legend">
          <span><i style="border-style:dashed;border-color:#D6D3CB;background:#FCFCFB;"></i>待买</span>
          <span><i style="border-color:#86EFAC;background:var(--accent-soft);"></i>持有</span>
          <span><i style="border-color:#D6D3CB;background:var(--grey-soft);"></i>已卖</span>
          <span><i style="border-color:#FCD34D;background:var(--amber-soft);"></i>留存（零成本）</span>
        </div>
        <div class="ruler" v-if="rulerInfo()">
          <div class="track"></div>
          <div class="ticklab" style="left:0%;transform:none;">最低档 {{ rulerInfo().low }}</div>
          <div class="ticklab" style="left:100%;transform:translateX(-100%);">基准 {{ rulerInfo().top }}</div>
          <div class="cur" v-if="rulerInfo().pct!=null" :style="{left:rulerInfo().pct+'%'}"><span class="curlab">现价 {{ rulerInfo().cur }}</span><div class="pin"></div></div>
        </div>

        <div style="margin-top:16px;" v-if="detail.status==='broken'">
          <div style="font-size:12px;font-weight:700;color:var(--muted);margin-bottom:4px;">破网处置 · 三选一</div>
          <div class="brk-opts show" style="margin-top:6px;">
            <div class="brk-opt" :class="{sel:brkAction==='hold'}" @click="selBrk('hold')"><div class="ot"><span class="rad"></span>装死持有</div><div class="cons">持仓不再买卖，等价格回到网内自动恢复网格。代价：<b>期间无网格收益</b>；好处：不确认亏损、不追加投入。</div></div>
            <div class="brk-opt" :class="{sel:brkAction==='extend'}" @click="selBrk('extend')"><div class="ot"><span class="rad"></span>向下接网</div><div class="cons">以现价为新基准向下补挂新格。摊低成本、延续套利；代价：<b>追加资金</b>、单一品种敞口加大。</div></div>
            <div class="brk-opt" :class="{sel:brkAction==='stop',dangersel:brkAction==='stop'}" @click="selBrk('stop')"><div class="ot"><span class="rad"></span>止损归档</div><div class="cons">按现价卖出全部网格持仓，<b>亏损落地</b>，计划转入已归档。好处：释放占用资金，结束纪律消耗。</div></div>
          </div>
          <div class="btnrow"><button class="btn danger" @click="runBrkAction(detail)">确认处置</button></div>
        </div>

        <div class="btnrow">
          <button class="btn" style="color:var(--accent);border-color:#86EFAC;" @click="runPlanAi()">✦ AI 计划体检</button>
          <button class="btn" style="color:var(--amber-ink);border-color:#FCD34D;" @click="runExitAi()">✦ AI 退出研判</button>
          <button class="btn" @click="openTradeModal(detail)">记一笔成交</button>
          <button class="btn" v-if="detail.status==='active'" @click="setStatus(detail,'paused')">暂停</button>
          <button class="btn primary" v-if="detail.status==='paused'" @click="setStatus(detail,'active')">恢复</button>
          <button class="btn danger" v-if="detail.status==='active'||detail.status==='paused'" @click="closePlan(detail)">收网退出</button>
          <button class="btn" @click="closeDetail()">收起</button>
        </div>
        <div class="ai-loading" v-if="planAiLoading">AI 正在体检…</div>
        <div class="ai-block" :class="{show:!!planAi}" v-if="planAi">
          <div class="ai-t">✦ AI 计划体检 · {{ detail.name }}<span class="chip" :class="planAi.verdict==='适合'?'go':planAi.verdict==='不适合'?'no':'maybe'" style="margin-left:auto;">{{ planAi.verdict }}</span></div>
          <div class="ai-sec"><div class="ai-k">结论</div><div class="ai-p">{{ planAi.oneLine }}</div></div>
          <div class="ai-sec" v-if="planAi.detail"><div class="ai-k">深入解读</div><div class="ai-p">{{ planAi.detail }}</div></div>
          <div class="ai-sec" v-if="planAi.reasons && planAi.reasons.length"><div class="ai-k">关键依据</div><div class="ai-p mute" v-for="r in planAi.reasons" :key="r">· {{ r }}</div></div>
          <div class="ai-sec" v-if="planAi.risks && planAi.risks.length"><div class="ai-k">风险提示</div><div class="ai-p mute" v-for="r in planAi.risks" :key="r">· {{ r }}</div></div>
          <div class="ai-sec" v-if="planAi.paramHint"><div class="ai-k">参数倾向</div><div class="ai-p">{{ planAi.paramHint }}</div></div>
          <div class="ai-disc">计划体检基于参数与压力测试数据，不构成投资建议。按（计划, 数据日期）缓存。</div>
        </div>
        <div class="ai-loading" v-if="exitAiLoading">AI 正在研判退出…</div>
        <div class="ai-block" :class="{show:!!exitAi}" v-if="exitAi">
          <div class="ai-t">✦ AI 退出研判 · {{ detail.name }}<span class="chip" :class="exitAi.verdict==='继续运行'?'go':exitAi.verdict==='建议收网'?'no':'maybe'" style="margin-left:auto;">{{ exitAi.verdict }}</span></div>
          <div class="ai-sec"><div class="ai-k">结论</div><div class="ai-p">{{ exitAi.oneLine }}</div></div>
          <div class="ai-sec" v-if="exitAi.detail"><div class="ai-k">深入解读</div><div class="ai-p">{{ exitAi.detail }}</div></div>
          <div class="ai-sec" v-if="exitAi.reasons && exitAi.reasons.length"><div class="ai-k">关键依据</div><div class="ai-p mute" v-for="r in exitAi.reasons" :key="r">· {{ r }}</div></div>
          <div class="ai-sec" v-if="exitAi.actions && exitAi.actions.length"><div class="ai-k">建议动作</div><div class="ai-p mute" v-for="r in exitAi.actions" :key="r">· {{ r }}</div></div>
          <div class="ai-disc">退出研判基于计划状态、持仓与估值分位，不构成投资建议。按（计划, 数据日期）缓存。</div>
        </div>
      </div>
      <div class="card-h" style="border-top:1px solid var(--line);padding-top:14px;"><div class="t">成交记录</div><div class="d">{{ detailTrades.length }} 笔 · 自动匹配档位</div></div>
      <div class="card-b" style="padding-left:10px;padding-right:10px;" v-if="detailTrades.length">
        <table>
          <thead><tr><th class="l">日期</th><th class="l">方向</th><th class="l">档位</th><th>价格</th><th>份额</th><th>金额</th><th class="l"></th></tr></thead>
          <tbody>
            <tr v-for="t in detailTrades" :key="t.id">
              <td class="l">{{ t.trade_date }}</td>
              <td class="l"><span class="dir" :class="t.direction==='buy'?'buy':'sell'"><i></i>{{ t.direction==='buy'?'买入':'卖出' }}</span></td>
              <td class="l" style="color:var(--muted);">{{ t.grid_level ? 'G'+t.grid_level : '—' }}</td>
              <td>{{ t.price }}</td><td>{{ t.shares.toLocaleString('zh-CN') }}</td><td>{{ fmt(t.amount,0) }}</td>
              <td class="l"><button class="btn sm danger" @click="delTrade(t,$event.currentTarget)">删</button></td>
            </tr>
          </tbody>
        </table>
      </div>
      <div class="empty-note" v-else>该计划还没有成交记录 · 点「记一笔成交」开始执行</div>
    </div>
  </div>
</section>
`,
  data: () => store,
  methods: { ...plansActions, openDcaCreate: dcaActions.openDcaCreate,
             dcaRows: dcaActions.dcaRows, dcaFreqLabel: dcaActions.dcaFreqLabel,
             dcaSugChip: dcaActions.dcaSugChip, openDcaDetail: dcaActions.openDcaDetail,
             dcaSetStatus: dcaActions.dcaSetStatus, delDcaPlan: dcaActions.delDcaPlan,
             runDcaBacktest: dcaActions.runDcaBacktest, closeDcaDetail: dcaActions.closeDcaDetail,
             switchTab, fmt },
};

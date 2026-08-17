// 规划页视图组件（模板从 index.html 平移；data 返回共享 store 单例，裸名访问不变）
import { store, fmt } from '../store.js';
import { plannerActions } from './planner.js';

export const PlannerView = {
  template: `
<section class="page">
  <div class="pagehead">
    <h1>规划</h1>
    <p>定参数 → 看档位表与压力测试 → 回测验证 → 存为计划。买入价 = 基准价 × (1−格距)ⁱ，卖出价 = 买入价 ÷ (1−格距)。</p>
  </div>
  <div class="grid2" style="grid-template-columns:1fr 1.2fr;align-items:start;">
    <div>
      <div class="card">
        <div class="card-h"><div class="t">找标的</div><div class="d">当前：<b>{{ plannerSymbolLabel() }}</b></div></div>
        <div class="card-b">
          <div class="field">
            <label>搜索 ETF（名称 / 代码）</label>
            <input v-model="etfQuery" @input="searchEtf()" placeholder="如 沪深300 / 510300">
          </div>
          <div v-if="etfResults.length" style="margin-top:8px;border:1px solid var(--line);border-radius:8px;overflow:hidden;">
            <div class="row" v-for="e in etfResults" :key="e.ts_code || e.symbol" @click="pickEtf(e)" style="padding:9px 12px;">
              <div style="flex:1;min-width:0;"><div class="nm" style="font-size:13px;">{{ e.name }}</div>
                <div class="sub">{{ e.ts_code || e.symbol }}<span v-if="e.amount"> · 成交额 {{ (e.amount/1e8).toFixed(1) }}亿</span></div></div>
              <span style="color:var(--faint);">→</span>
            </div>
          </div>
        </div>
      </div>
      <div class="card">
        <div class="card-h"><div class="t">参数</div></div>
        <div class="card-b">
          <div class="formgrid">
            <div class="field"><label>计划名称</label><input v-model="plannerForm.name" placeholder="自动按标的命名"></div>
            <div class="field"><label>基准价（第 1 格买入价）</label><input v-model="plannerForm.base_price" type="number" step="0.001"></div>
            <div class="field"><label>格距 %</label><input v-model="plannerForm.grid_step" type="number" step="0.5" min="1" max="20"></div>
            <div class="field"><label>格数</label><input v-model="plannerForm.grid_count" type="number" min="2" max="30"></div>
            <div class="field"><label>投入方式</label>
              <div class="seg" style="display:flex;">
                <button :class="{on:plannerForm.grid_mode==='amount'}" @click="setGridMode('amount')" style="flex:1;">等金额</button>
                <button :class="{on:plannerForm.grid_mode==='shares'}" @click="setGridMode('shares')" style="flex:1;">等份额</button>
              </div>
            </div>
            <div class="field" v-if="plannerForm.grid_mode!=='shares'"><label>每格金额（元）</label><input v-model="plannerForm.amount_per_grid" type="number" step="1000"></div>
            <div class="field" v-if="plannerForm.grid_mode==='shares'"><label>每格份额（股）</label><input v-model="plannerForm.shares_per_grid" type="number" step="100" min="100"></div>
            <div class="field"><label>逐格加码 %<span v-if="plannerForm.grid_mode==='shares'">（份额）</span></label><input v-model="plannerForm.step_increase" type="number" min="0" max="50"></div>
            <div class="field"><label>留利润 %（网格 2.0）</label><input v-model="plannerForm.profit_retention" type="number" min="0" max="90"></div>
          </div>
          <div class="btnrow">
            <button class="btn primary" @click="preview()" :disabled="plannerLoading">重算档位</button>
            <button class="btn" @click="runOptimize()" :disabled="optLoading">参数寻优</button>
            <button class="btn" @click="savePlan()">存为计划</button>
          </div>
        </div>
      </div>
      <div class="card" v-if="plannerPreview">
        <div class="card-h"><div class="t">压力测试</div><div class="d">假设全部档位买满</div></div>
        <div class="card-b">
          <div class="kpis flat" style="grid-template-columns:repeat(2,1fr);margin-bottom:0;">
            <div class="kpi"><div class="k">满格资金</div><div class="v sm warm">{{ fmt(plannerPreview.pressure_test.total_capital,0) }}</div><div class="f">最深 -{{ plannerPreview.pressure_test.max_fall_pct }}%</div></div>
            <div class="kpi"><div class="k">满格浮亏</div><div class="v sm bad">{{ fmt(plannerPreview.pressure_test.max_unrealized_loss,0) }}</div><div class="f">{{ plannerPreview.pressure_test.max_unrealized_loss_pct }}% · 最低 {{ plannerPreview.pressure_test.lowest_price }}</div></div>
          </div>
        </div>
        <div class="note">先算最坏情况再开网：满格资金是你最多要掏的钱，满格浮亏是那一刻的账面。</div>
      </div>
      <div class="card" v-if="plannerOpt">
        <div class="card-h">
          <div class="t">参数寻优</div>
          <div class="d">{{ Math.round(plannerLookback/250) }} 年真实行情 · 按 score = 收益 − 0.45×回撤 排序 · 成交 &lt;{{ plannerOpt.low_activity_trades }} 次标「低活性」</div>
        </div>
        <div class="card-b" style="padding-left:10px;padding-right:10px;">
          <table>
            <thead><tr><th class="l">格距</th><th class="l">格数</th><th>收益</th><th>回撤</th><th>套利</th><th>利用率</th><th>得分</th><th class="l">标记</th></tr></thead>
            <tbody>
              <tr v-for="(c,i) in optimizeRows()" :key="c.step+'-'+c.count"
                  :style="i===0 ? 'background:var(--accent-soft);' : ''">
                <td class="l">{{ c.step }}%</td><td class="l">{{ c.count }} 格</td>
                <td :style="{color:c.ret>=0?'var(--accent)':'var(--red-ink)'}">{{ c.ret }}%</td>
                <td>{{ c.dd }}%</td><td>{{ c.trades }} 次</td><td>{{ c.invested_pct }}%</td>
                <td style="font-weight:700;">{{ c.score }}</td>
                <td class="l">
                  <span class="chip go" v-if="i===0">score 最优</span>
                  <span class="chip maybe" v-if="c.low_activity">低活性</span>
                  <span class="chip blue" v-if="plannerOpt.best_active && c===plannerOpt.best_active">活性最优</span>
                </td>
              </tr>
            </tbody>
          </table>
          <div class="note" style="padding:10px 10px 0;" v-if="plannerOpt.best && plannerOpt.best.low_activity">
            score 最优 <b>{{ plannerOpt.best.step }}% × {{ plannerOpt.best.count }} 格</b>全年仅成交 {{ plannerOpt.best.trades }} 次——更像低频抄底而非网格。
            <template v-if="plannerOpt.best_active">活性最优为 <b>{{ plannerOpt.best_active.step }}% × {{ plannerOpt.best_active.count }} 格</b>（成交 {{ plannerOpt.best_active.trades }} 次，得分 {{ plannerOpt.best_active.score }}）。</template>
          </div>
          <div class="btnrow">
            <button class="btn" style="color:var(--accent);border-color:#86EFAC;" @click="runOptimizeAi()" v-if="ai.enabled">✦ AI 解读寻优</button>
          </div>
          <div class="ai-loading" v-if="optAiLoading">AI 正在解读…</div>
          <div class="ai-block show" v-if="plannerOptAi">
            <div class="ai-t">✦ AI 寻优建议<span class="chip go" style="margin-left:auto;">{{ plannerOptAi.step }}% × {{ plannerOptAi.count }} 格</span></div>
            <div class="ai-sec"><div class="ai-p" style="font-weight:600;">{{ plannerOptAi.oneLine }}</div></div>
            <div class="ai-sec" v-if="plannerOptAi.reasons && plannerOptAi.reasons.length"><div class="ai-k">推荐理由</div><div class="ai-p mute" v-for="r in plannerOptAi.reasons" :key="r">· {{ r }}</div></div>
            <div class="ai-sec" v-if="plannerOptAi.warning"><div class="ai-k">风险提示</div><div class="ai-p mute">{{ plannerOptAi.warning }}</div></div>
            <div class="ai-disc">AI 定位是研究助手：解读矩阵、提示风险，不构成投资建议。数字均来自本地回测。</div>
          </div>
        </div>
      </div>
    </div>
    <div>
      <div class="card" v-if="plannerPreview">
        <div class="card-h"><div class="t">档位表 · {{ plannerPreview.levels.length }} 格</div><div class="d">基准 <b>{{ plannerForm.base_price }}</b> · 格距 <b>{{ plannerForm.grid_step }}%</b> · {{ plannerForm.grid_mode==='shares' ? '等份额' : '等金额' }}</div></div>
        <div class="card-b" style="padding-left:10px;padding-right:10px;">
          <table>
            <thead><tr><th class="l">格</th><th>跌幅</th><th>买入价</th><th>卖出价</th><th>金额</th><th>份额</th><th>卖出份额</th><th>留存</th><th>预期收益</th></tr></thead>
            <tbody>
              <tr v-for="l in plannerPreview.levels" :key="l.level">
                <td class="l" style="color:var(--faint);">G{{ l.level }}</td>
                <td>-{{ l.fall_pct }}%</td><td>{{ l.buy_price }}</td><td>{{ l.sell_price }}</td>
                <td>{{ fmt(l.amount,0) }}</td><td>{{ l.shares.toLocaleString('zh-CN') }}</td>
                <td>{{ l.sell_shares.toLocaleString('zh-CN') }}</td>
                <td style="color:var(--amber-ink);">{{ l.retained_shares || '—' }}</td>
                <td style="color:var(--accent);font-weight:600;">+{{ fmt(l.expected_profit,0) }}</td>
              </tr>
            </tbody>
          </table>
        </div>
        <div class="note">金额按 100 股整手取整；留存 = 留利润不卖、转为长期底仓的份额（网格 2.0）。</div>
      </div>
      <div class="card" v-if="!plannerPreview && !plannerLoading"><div class="empty-note">选择标的并填好参数后，点「重算档位」生成档位表与回测</div></div>
    </div>
  </div>

  <!-- 回测：整行大卡 -->
  <div class="card" v-if="plannerBt && !plannerBt.error">
    <div class="card-h"><div class="t">回测</div>
      <div class="d" style="display:flex;align-items:center;gap:10px;flex-wrap:wrap;">
        <div class="seg">
          <button :class="{on:plannerLookback===250}" @click="setLookback(250)">近1年</button>
          <button :class="{on:plannerLookback===750}" @click="setLookback(750)">近3年</button>
          <button :class="{on:plannerLookback===1250}" @click="setLookback(1250)">近5年</button>
        </div>
        <div class="seg" title="窗口起点=按窗口首日价开网；穿越点=从今天价位最近一次被穿越的那天开网">
          <button :class="{on:plannerAnchor==='window'}" @click="setAnchor('window')">窗口起点</button>
          <button :class="{on:plannerAnchor==='cross'}" @click="setAnchor('cross')">穿越点</button>
        </div>
        <label style="font-size:12px;color:var(--muted);display:flex;align-items:center;gap:6px;" title="价格涨穿网格顶格就以现价重开新网">
          <input type="checkbox" :checked="compareRebase" @change="toggleCompareRebase()">对比「自动上移重开」
        </label>
        <span v-if="plannerAnchor==='cross' && plannerBt.cross_idx!=null">自第 {{ plannerBt.cross_idx + 1 }} 日（今日价位最近被穿越处）起 · </span>
        <span v-else-if="plannerAnchor==='cross'">窗口内未穿越今日价位，按窗口起点 · </span>
        <span>{{ plannerBt.n }} 个交易日 · {{ fmt(plannerBt.base,3) }} → {{ fmt(plannerBt.last,3) }}</span>
      </div>
    </div>
    <div class="card-b">
      <div id="bt-equity" style="height:340px;"></div>
      <div style="display:flex;gap:22px;flex-wrap:wrap;font-size:13px;color:var(--muted);margin-top:10px;">
        <span>网格 <b :style="{color:plannerBt.grid_ret>=0?'var(--accent)':'var(--red-ink)',fontSize:'15px'}">{{ plannerBt.grid_ret }}%</b></span>
        <span v-if="plannerBt.rebase">网格·自动重开 <b :style="{color:plannerBt.rebase.grid_ret>=0?'#2563EB':'var(--red-ink)',fontSize:'15px'}">{{ plannerBt.rebase.grid_ret }}%</b>（重开 {{ plannerBt.rebase.rebases }} 次 · 套利 {{ plannerBt.rebase.trades }} 次）</span>
        <span>持有 <b :style="{color:plannerBt.hold_ret>=0?'var(--accent)':'var(--red-ink)',fontSize:'15px'}">{{ plannerBt.hold_ret }}%</b></span>
        <span>套利 <b style="color:var(--text);font-size:15px;">{{ plannerBt.trades }} 次</b></span>
        <span>回撤 <b style="color:var(--red-ink);font-size:15px;">-{{ plannerBt.max_dd }}%</b></span>
        <span>资金利用率 <b style="color:var(--text);font-size:15px;">{{ plannerBt.invested_pct }}%</b></span>
        <span v-if="plannerBt.retained_shares>0">留存底仓 <b style="color:var(--amber-ink);font-size:15px;">{{ plannerBt.retained_shares }} 股</b></span>
      </div>
      <div class="alert warnl" style="margin-top:14px;" v-if="plannerBt.trades<=2">
        <div class="ab">这段行情网格几乎不成交——<b>涨上去就买不到了</b>。单边上涨里网格跑不赢持有是结构性的，不是参数错了。建议配合<b>底仓 + 留利润</b>。</div>
      </div>
      <div class="alert warnl" style="margin-top:14px;" v-if="plannerBt.rebase && plannerBt.rebase.grid_ret < plannerBt.grid_ret">
        <div class="ab">这段行情里「自动上移重开」套利更多（{{ plannerBt.rebase.trades }} vs {{ plannerBt.trades }} 次）但收益反而更低——<b>重开赚次数、亏位置</b>。网格的收益来自锚点位置，不来自动作频率；重开请过估值闸门。</div>
      </div>
      <div class="alert crit" style="margin-top:14px;" v-if="plannerBt.broken_idx!==null&&plannerBt.broken_idx!==undefined">
        <div class="ab">第 <b>{{ plannerBt.broken_idx+1 }}</b> 个交易日曾<b>跌破最后一格（破网）</b>，此后无格可买、满仓持有待涨。想覆盖更深跌幅可增加格数或加大每格金额。</div>
      </div>
      <div v-if="btRounds().length" style="margin-top:14px;">
        <div style="font-size:12px;font-weight:700;color:var(--muted);margin-bottom:6px;">回合明细（最近 {{ btRounds().length }} 个）</div>
        <table>
          <thead><tr><th class="l">档位</th><th class="l">买入</th><th class="l">卖出</th><th>价差</th></tr></thead>
          <tbody>
            <tr v-for="r in btRounds()" :key="r.sellDate+r.level">
              <td class="l">G{{ r.level }}</td>
              <td class="l" style="color:var(--muted);">{{ r.buyDate }} · {{ r.buy }}</td>
              <td class="l" style="color:var(--muted);">{{ r.sellDate }} · {{ r.sell }}</td>
              <td style="color:var(--accent);font-weight:600;">+{{ r.pct }}%</td>
            </tr>
          </tbody>
        </table>
      </div>
    </div>
    <div class="note">基于真实历史价格的模拟，未计手续费与滑点；仅用于理解策略特性，不代表未来收益。三角标记为静态口径的买卖点。</div>
  </div>
  <div class="card" v-if="btLoading"><div class="card-b"><div class="ai-loading">正在拉取历史行情回测…</div></div></div>
</section>
`,
  data: () => store,
  methods: { ...plannerActions, fmt },
};

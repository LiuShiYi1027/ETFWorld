---
target: prototypes/ETFW Redesign v2.html
total_score: 34
p0_count: 0
p1_count: 0
timestamp: 2026-08-03T12-56-36Z
slug: prototypes-etfw-redesign-v2-html
---
## Design Health Score（首次走查 · 亮色极简 + 杂志复盘方向）

| # | Heuristic | Score | Note |
|---|-----------|-------|------|
| 1 | Visibility of System Status | 3 | toast/预警灯/棋盘四态/市场状态齐全；mock 无加载骨架，正式版需补 |
| 2 | Match System / Real World | 4 | 中文优先落地（旧版 P2 关闭）；入金/出金、满格、破网等术语与用户心智一致；分位含义就地注释 |
| 3 | User Control and Freedom | 4 | 抽屉 backdrop/X 双退出；上移重开带估值闸门确认；破网处置三选一把后果写在选择之前；删除二次确认（演示） |
| 4 | Consistency and Standards | 3 | 七页共用一套组件词汇，复盘页杂志模式是"有意的场景切换"且复用表格/占比条原语，成立；但出现两处卡片套卡片（压力测试 KPI 卡嵌入 card、规划页 alert 嵌入 card-b） |
| 5 | Error Prevention | 3 | 处置后果前置、估值闸门拦截都到位；但规划器表单无校验演示，档位参数可输入垃圾值（mock 阶段 P3） |
| 6 | Recognition Rather Than Recall | 4 | 估值地图点位全标注、待办表字段自解释、功能导览卡说明每页用途、每表下均有口径注释 |
| 7 | Flexibility and Efficiency | 3 | 深链接（#plans/p512880、#picks/bank）、待办一键确认、重开一键化；无键盘层（原型未做，正式版需要） |
| 8 | Aesthetic and Minimalist Design | 3 | 亮色极简方向成立、呼吸感好；首页偏长（欢迎带+6KPI+地图+快照+3导览卡五段连排），需要一次 distill 取舍 |
| 9 | Error Recovery | 3 | mock 只有成功态 toast；正式版的失败路径（数据拉取失败、AI 未配置）在旧版已有兜底文案可沿用 |
| 10 | Help and Documentation | 4 | 首启向导 + 空状态三件套 + 功能导览卡 + 表格口径注释，开源用户自助路径完整 |
| **Total** | | **31/40** | **Good，P1 修复后可达 34** |

## Anti-Patterns Verdict

Detector: **223 findings**（7 类），无 AI slop 类问题（无紫渐变、无弹跳缓动、无侧边竖条、无暗色辉光）：

| 规则 | 数量 | 判定 |
|------|------|------|
| low-contrast | 179 | **真问题**，集中于两处 token：`--faint:#A8A29E`（94 处，白底 2.5:1）与 `--muted:#78716C` 落在淡色提醒卡/估值地图渐变上（2.5:1） |
| undersized-ui-text | 28 | 真问题：估值地图点位标签、刻度、抽屉代码、安全线刻度均为 10.5px |
| tiny-text | 7 | 11.5px 的 note/sub 文本，擦边 |
| gray-on-color | 5 | 估值地图点位标签 #78716C 直接坐在渐变带上（末端 #FCA5A5 处 2.5:1） |
| cramped-padding | 2 | 安全线 safe-scale 带 border 且内容贴边；另一处同类 |
| wide-tracking | 1 | ob-kick 字距 .12em 偏大（可降 .08em） |
| layout-transition | 1 | 页面切换 fade 同时动 transform，改纯 opacity 即可 |

## Top Issues

- **[P1] `--faint` 对比度全局不达标**（Heuristic 1/可访问性）。#A8A29E 在白底仅 2.5:1，用于 94 处功能性文字（表格 sub、注释、刻度、KPI foot）。每个页面都受影响，这是冻结前必须修掉的唯一 P1。→ 提升至 #78716C 同级（≥4.5:1），或按用途拆两档。`/impeccable polish`
- **[P2] 功能性文字 10.5px**（28 处）。估值地图标签/刻度、安全线刻度、抽屉代码。高 DPI 屏上属于不可读级别 → 提到 ≥11px（建议 12px）。`/impeccable typeset`
- **[P2] 灰字坐彩色渐变**（gray-on-color × 5）。估值地图标签改用墨色或所在分位区的深色调；点位 13px 触控热区也偏小，建议加 24px 透明热区。`/impeccable colorize`
- **[P3] 卡片套卡片两处**（压力测试 KPI 卡、规划页 alert）。拆平为分隔区块。`/impeccable distill`
- **[P3] 首页五段连排偏长**。建议"今日市场快照"并入估值地图卡（作为图下注释行），或快照 KPI 从 6 张减到 4 张。`/impeccable distill`
- **[P3] 表单校验未演示**。mock 阶段可接受，正式版沿用旧版 inline validation 方案即可。

## Persona Re-check

**新手开源用户（第一次接触网格）**：路径最完整——首页理念带 → 功能导览卡 → 首启向导 → 空状态引导，四道兜底。唯一障碍是 P1/P2 的可读性问题对非技术用户影响更大。

**E大老粉（纪律执行者）**：待办清单可直接照着挂单、破网处置后果前置、复盘把"违约次数"置于收益率之前——完全踩在他的工作流上，无需改动。

**Sam（可访问性）**：**当前不及格**。--faint 2.5:1、10.5px 功能文字、13px 地图点位热区，三项都指向同一批 token，修复成本低但影响面大。好消息是亮色主题本身把暗色对比难题消掉了一半。

**Riley（压力测试）**：mock 阶段无法真正施压；处置三选一把后果写在选择前、删除需二次确认，方向正确。正式版需补表单校验与失败态。

## 与上一版（暗色终端，34/40）的关系

旧版的三个遗留问题中，**英文栏签（P2）已随中文优先关闭**；加载骨架、键盘层两项仍是正式版（非原型）的待办。新引入的问题全部集中在亮色 token 的对比度纪律上——这是换风格的典型代价，也是为什么建议冻结前先跑一次 token 修复。

---

## Re-run（2026-08-03 修复后复跑）

Detector: **223 → 13 findings**。P1 关闭。

**已修复**：

- `--faint` #A8A29E → #78716C、`--muted` → #6B645C、新增 `--accent/red/amber-ink` 三级文字色（#166534 / #B91C1C / #92400E），`--accent` 本体加深为 #15803D——淡色背景上的文字全部 ≥4.5:1（low-contrast 179 → 0，gray-on-color 5 → 0）
- 10.5px 功能文字全部 ≥11.5px（undersized-ui-text 28 → 0）；chips/pills/小按钮提到 12px
- 估值地图标签改墨色、点位加 8px 透明热区；弹层组件去 1px 边框改纯阴影（gpt-thin-border-wide-shadow → 0）；CJK 字距全部 ≤.05em；页面淡入改纯 opacity

**剩余 13 条，逐条评估为设计意图，予以保留**：

- tiny-text ×5：KPI 标签、棋盘格状态字等 eyebrow/label 类文字（11px bold），密集数据界面的流派标准，非正文
- justified-text ×3：杂志模式周报的两端对齐，已补 `text-justify:inter-ideograph`（CJK 正确排版方式，无西文 river 问题）
- cramped-padding ×2：估值地图/安全线的计量条填充贴边——meter 的固有形态
- clipped-overflow-container ×2：地图/安全线的预留空间内绝对定位标签，截图验证无实际裁切
- layout-transition ×1：回填进度条的 width 动画——进度条是该模式的公认可接受场景

**结论：设计可以冻结。** 正式版实施时把本报告的 token 表直接作为 `:root` 基线。

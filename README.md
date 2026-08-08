<h1 align="center">ETFWorld</h1>
<p align="center">本地优先的 ETF 网格策略工作台 · 把「计划 → 执行 → 复盘」放进一个桌面应用</p>
<p align="center">
  <a href="https://github.com/LiuShiYi1027/ETFWorld/actions/workflows/test.yml"><img src="https://github.com/LiuShiYi1027/ETFWorld/actions/workflows/test.yml/badge.svg" alt="CI"></a>
  <a href="https://github.com/LiuShiYi1027/ETFWorld/releases/latest"><img src="https://img.shields.io/github/v/release/LiuShiYi1027/ETFWorld" alt="Release"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-green" alt="License"></a>
</p>

<p align="center"><img src="docs/screenshots/home.png" alt="ETFWorld 首页" width="1080"></p>

## 特性

- **网格 1.0 / 2.0**：逐格低买高卖，留利润机制把每次套利沉淀为零成本底仓
- **执行感知**：录入成交自动匹配档位，档位棋盘实时呈现待买/持有/已卖/留存
- **组合安全线**：底仓/网格/现金三账户，满格资金 ÷ 本金一眼看清"买满还差多少钱"
- **估值闸门**：低估才开网，分位越过 50% 直接否决；上移重开前先过估值检查
- **破网有预案**：跌破最低档后三选一——装死持有 / 向下接网 / 止损归档，代价写在选择之前
- **AI 研究助手**（可选）：任意 OpenAI 兼容端点，指数研判 / 计划体检 / 组合周报，按天缓存
- **本地优先**：数据与密钥全在本机，不连券商、不自动下单、不上传任何信息

## 理念来源

本项目的方法论来自博主 **E大（ETF拯救世界）** 在其公众号「**长赢指数投资**」多年公开分享的指数投资与网格交易理念。落到这个工具里的有四个：

- **低估才开网**：估值分位是开网格的闸门，分位过高直接否决；
- **不死品种**：只在一辈子不死的指数上做网格（监控池全部为宽基与长牛行业指数）；
- **留利润**：网格 2.0，每次卖出留一份利润份额，长期攒出零成本底仓；
- **先算最坏情况**：开网前先算满格资金与最深浮亏，破网有预案。

作者本人是 E 大的读者。做这个项目的原因很朴素：理念都懂，但执行散在行情软件、Excel 和记忆力之间——于是把"计划 → 执行 → 复盘"做成了一个本地工具。

**本项目与 E 大本人、其公众号及任何社区不存在官方关联**；理念归原作者，工具的实现、数据与结论由本项目自行负责。

## 安装

| 平台 | 下载 | 说明 |
|------|------|------|
| macOS（Apple Silicon） | [ETFWorld-macos-arm64.dmg](https://github.com/LiuShiYi1027/ETFWorld/releases/latest) | 已签名公证的版本直接打开；未签名构建首次需在 Finder 右键"打开" |
| Windows | [ETFWorld-windows-x64.exe](https://github.com/LiuShiYi1027/ETFWorld/releases/latest) | SmartScreen 提示选"仍要运行" |
| 源码 | `git clone` 后 `./start.sh` | Python 3.10+，访问 127.0.0.1:8000 |

首次启动进入三步向导：阅读免责 → 填入 Tushare Token → 回填历史数据。数据保存在 `~/Library/Application Support/ETFWorld/`（macOS）或 `%APPDATA%\ETFWorld\`（Windows），升级不覆盖。

> ETFWorld 仅用于学习与辅助研究，不构成任何投资建议。市场有风险，盈亏自负。

## 文档

- [使用文档](docs/使用文档.md) — 逐页功能指南、命令行、API 参考、常见问题
- [AGENTS.md](AGENTS.md) — 项目结构与贡献约定
- [.impeccable/critique/](.impeccable/critique/) — 设计走查档案

## 致谢

- 博主 **E大（ETF拯救世界）** 与公众号「长赢指数投资」——本项目全部方法论的来源
- [Tushare](https://tushare.pro) — 估值与行情数据
- [petite-vue](https://github.com/vuejs/petite-vue) · [ECharts](https://echarts.apache.org) · [FastAPI](https://fastapi.tiangolo.com) · [pywebview](https://pywebview.flowrl.com)

## License

[MIT License](LICENSE)

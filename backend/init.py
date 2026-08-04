"""
ETFWorld 命令行工具

用法（在项目根目录执行）:
    python -m backend.init --init-db                          # 初始化数据库
    python -m backend.init --update                           # 更新最新估值数据
    python -m backend.init --backfill --start 20200101 --end 20241231  # 回填历史
    python -m backend.init --show                             # 查看当前估值概览
"""
import argparse
import logging

logging.basicConfig(level=logging.INFO,
                    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s')


def main():
    parser = argparse.ArgumentParser(description='ETFWorld 初始化工具')
    parser.add_argument('--init-db', action='store_true', help='初始化数据库')
    parser.add_argument('--update', action='store_true', help='更新最新估值数据')
    parser.add_argument('--backfill', action='store_true', help='回填历史数据')
    parser.add_argument('--start', type=str, help='开始日期 YYYYMMDD')
    parser.add_argument('--end', type=str, help='结束日期 YYYYMMDD')
    parser.add_argument('--show', action='store_true', help='查看当前估值')
    parser.add_argument('--sw-search', type=str, metavar='关键词',
                        help='搜索申万行业并显示最新估值')
    parser.add_argument('--sw-list', type=str, metavar='LEVEL',
                        help='列出申万行业(L1/L2/L3)')
    parser.add_argument('--readiness', action='store_true',
                        help='评估所有指数的网格就绪度（排序）')
    parser.add_argument('--etf-search', type=str, metavar='关键词',
                        help='搜索可交易ETF（按成交额排序）')
    args = parser.parse_args()

    from backend.utils.db import init_db
    from backend.services.valuation_service import ValuationService

    if args.init_db:
        init_db()
        ValuationService().init_index_info()
        print('数据库初始化完成')
        return

    if args.sw_search:
        from backend.services.sw_service import SWService
        rows = SWService().search_with_valuation(args.sw_search)
        if not rows:
            print(f'未找到包含「{args.sw_search}」的申万行业')
            return
        print(f"{'代码':14s}{'名称':14s}{'级别':4s}{'PE':>8s}{'PB':>8s}")
        for r in rows:
            pe = r.get('pe'); pb = r.get('pb')
            print(f"{r['ts_code']:14s}{r['name']:14s}{r['level']:4s}"
                  f"{(pe if pe is not None else '-'):>8}{(pb if pb is not None else '-'):>8}")
        return

    if args.sw_list:
        from backend.services.sw_service import SWService
        rows = SWService().list_industries(args.sw_list)
        print(f'申万{args.sw_list.upper()}行业，共 {len(rows)} 个:')
        for r in rows:
            print(f"  {r['ts_code']:14s} {r['name']}")
        return

    if args.etf_search:
        from backend.services.etf_service import ETFService
        rows = ETFService().search(args.etf_search)
        if not rows:
            print(f'未找到与「{args.etf_search}」相关的ETF')
            return
        print(f"{'代码':12s}{'名称':24s}{'成交额(亿)':>10s}{'现价':>8s}  跟踪基准")
        for r in rows:
            amt = f"{r['amount_yi']:.2f}" if r.get('amount_yi') else '-'
            px = f"{r['close']:.3f}" if r.get('close') else '-'
            print(f"{r['ts_code']:12s}{(r['name'] or '')[:22]:24s}{amt:>10}{px:>8}  {r.get('benchmark') or ''}")
        return

    if args.readiness:
        from backend.services.readiness_service import ReadinessService
        rows = ReadinessService().assess_all()
        icon = {'go': '🟢', 'maybe': '🟡', 'wait': '⚪', 'no': '🔴', 'unknown': '❓'}
        print(f"{'':2s}{'分':>4s} {'名称':12s}{'估值分位':>8s}{'波动%':>7s}  {'结论'}")
        for r in rows:
            vp = r.get('valuation_percentile')
            vps = f"{vp:.0f}%" if vp is not None else '-'
            vol = f"{r['volatility']:.0f}" if r['volatility'] is not None else '-'
            print(f"{icon.get(r['level'],'')} {r['score']:>4} {r['name']:12s}"
                  f"{vps:>8}{vol:>7}  {r['verdict']}")
        return

    svc = ValuationService()

    if args.update:
        result = svc.update_latest()
        print(f'更新结果: {result}')
    elif args.backfill:
        if not args.start or not args.end:
            parser.error('--backfill 需要 --start 与 --end')
        result = svc.backfill(args.start, args.end)
        print(f'回填结果: {result}')
        n = svc.calc_percentiles()
        print(f'分位点已计算: {n} 条')
    elif args.show:
        for item in svc.get_overview():
            p5 = item['percentiles'].get('5y') or {}
            zone = (item['zone'] or {}).get('label', '-')
            print(f"{item['name']:8s} {item['trade_date'] or '-':12s} "
                  f"PE={item['pe_ttm'] or '-'} PB={item['pb'] or '-'} "
                  f"PE分位(5y)={p5.get('pe', '-')} [{zone}]")
    else:
        parser.print_help()


if __name__ == '__main__':
    main()

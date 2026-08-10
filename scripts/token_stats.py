#!/usr/bin/env python3
"""统计本机 Kimi Code 会话日志的 token 用量（按项目目录）。

读取 ~/.kimi-code/sessions/ 下各项目的 wire.jsonl，按 usage 记录汇总
（相邻重复日志去重）。用法：
    python3 scripts/token_stats.py              # 全部项目
    python3 scripts/token_stats.py etfworld     # 只看名称含 etfworld 的项目
"""
import glob
import json
import os
import sys

SESSIONS = os.path.expanduser('~/.kimi-code/sessions')


def scan(project_filter=None):
    pattern = os.path.join(SESSIONS, 'wd_*', 'session_*', 'agents', '*', 'wire.jsonl')
    per_project = {}
    for f in sorted(glob.glob(pattern)):
        project = f.split('wd_', 1)[1].split('/session_', 1)[0]
        project = project.rsplit('_', 1)[0]  # 去掉目录名尾的哈希
        if project_filter and project_filter.lower() not in project.lower():
            continue
        acc = per_project.setdefault(project, {
            'requests': 0, 'inputOther': 0, 'output': 0,
            'inputCacheRead': 0, 'inputCacheCreation': 0})
        prev = None
        for line in open(f, encoding='utf-8'):
            try:
                d = json.loads(line)
            except Exception:
                continue

            def find_usage(obj):
                if isinstance(obj, dict):
                    u = obj.get('usage')
                    if isinstance(u, dict) and 'output' in u:
                        return u
                    for v in obj.values():
                        r = find_usage(v)
                        if r:
                            return r
                return None

            u = find_usage(d)
            if not u:
                continue
            t = (u.get('inputOther', 0), u.get('output', 0),
                 u.get('inputCacheRead', 0), u.get('inputCacheCreation', 0))
            if t == prev:  # 同一响应的重复日志
                continue
            prev = t
            acc['requests'] += 1
            acc['inputOther'] += t[0]
            acc['output'] += t[1]
            acc['inputCacheRead'] += t[2]
            acc['inputCacheCreation'] += t[3]
    return per_project


def main():
    flt = sys.argv[1] if len(sys.argv) > 1 else None
    stats = scan(flt)
    if not stats:
        sys.exit('没有找到会话日志' + (f'（过滤: {flt}）' if flt else ''))
    g_req = g_total = 0
    print(f'{"项目":<24}{"请求数":>8}{"输入":>14}{"输出":>12}{"缓存读":>16}{"合计":>16}')
    print('─' * 92)
    for name, a in sorted(stats.items(), key=lambda kv: -sum(kv[1][k] for k in
                                  ('inputOther', 'output', 'inputCacheRead', 'inputCacheCreation'))):
        total = a['inputOther'] + a['output'] + a['inputCacheRead'] + a['inputCacheCreation']
        g_req += a['requests']
        g_total += total
        print(f"{name:<24}{a['requests']:>8}{a['inputOther']:>14,}{a['output']:>12,}"
              f"{a['inputCacheRead']:>16,}{total:>16,}")
    print('─' * 92)
    print(f'{"合计":<24}{g_req:>8}{"":>14}{"":>12}{"":>16}{g_total:>16,}')
    print(f'\n总计 {g_req} 次请求，{g_total:,} Tokens（约 {g_total / 1e8:.1f} 亿）')
    print('口径：本机 ~/.kimi-code 会话 wire 日志逐条统计，相邻重复记录去重；'
          '含输入/输出/缓存读取，不含已清理的历史会话。')


if __name__ == '__main__':
    main()

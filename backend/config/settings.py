"""
ETFWorld 配置文件
"""
import os
from pathlib import Path

try:
    from dotenv import dotenv_values, load_dotenv
    # 优先读 backend/.env（推荐放这），再兼容项目根 .env / 启动目录；先加载者优先
    load_dotenv(Path(__file__).resolve().parent.parent / '.env')
    load_dotenv()
except ImportError:
    pass

# 项目根目录
BASE_DIR = Path(__file__).resolve().parent.parent.parent

# .env.example 里的占位符——若用户没改就当作"没填"，避免占位符顶掉可用默认
_PLACEHOLDERS = {'你的Tushare_Token', '请填入你的DeepSeek_Key', '请填入你的AI_Key', 'your_password', ''}


def _env(name, default=''):
    """读环境变量；空值或占位符视为未设置，回落到 default"""
    val = (os.getenv(name) or '').strip()
    return val if val and val not in _PLACEHOLDERS else default


# Tushare配置
TUSHARE_TOKEN = _env('TUSHARE_TOKEN', '')
# ⭐️ 自定义数据源地址，缺失会导致 "Token 不对"
TUSHARE_API_URL = _env('TUSHARE_API_URL', 'https://ttx.dailyfetch.top/')

# AI 研判配置：任意兼容 OpenAI Chat Completions 协议的服务（DeepSeek / Kimi / …）
# （只在后端读取，绝不下发前端）
# 【请你填】AI_API_KEY 留空时 AI 研判不可用，前端自动回退到规则结论
# 旧变量名 DEEPSEEK_* 仍兼容读取，新配置请用 AI_*
AI_API_URL = _env('AI_API_URL') or _env('DEEPSEEK_API_URL', 'https://api.deepseek.com/chat/completions')
AI_API_KEY = _env('AI_API_KEY') or _env('DEEPSEEK_API_KEY', '')
AI_MODEL = _env('AI_MODEL') or _env('DEEPSEEK_MODEL', 'deepseek-chat')


def env_file_path() -> Path:
    custom = os.getenv('ETFWORLD_ENV_PATH')
    return Path(custom).expanduser() if custom else Path(__file__).resolve().parent.parent / '.env'


def save_runtime_settings(values):
    """Persist desktop settings without ever exposing existing secrets to the UI."""
    path = env_file_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    current = dict(dotenv_values(path)) if path.exists() else {}
    current.update({key: str(value) for key, value in values.items() if value is not None})
    lines = [f'{key}={value}' for key, value in current.items() if value is not None]
    tmp = path.with_suffix(path.suffix + '.tmp')
    tmp.write_text('\n'.join(lines) + '\n', encoding='utf-8')
    os.chmod(tmp, 0o600)
    tmp.replace(path)
    for key, value in current.items():
        if value is not None:
            os.environ[key] = str(value)

    global TUSHARE_TOKEN, TUSHARE_API_URL
    global AI_API_URL, AI_API_KEY, AI_MODEL
    TUSHARE_TOKEN = _env('TUSHARE_TOKEN', '')
    TUSHARE_API_URL = _env('TUSHARE_API_URL', 'https://ttx.dailyfetch.top/')
    AI_API_URL = _env('AI_API_URL') or _env('DEEPSEEK_API_URL', 'https://api.deepseek.com/chat/completions')
    AI_API_KEY = _env('AI_API_KEY') or _env('DEEPSEEK_API_KEY', '')
    AI_MODEL = _env('AI_MODEL') or _env('DEEPSEEK_MODEL', 'deepseek-chat')

# 数据库：默认SQLite（零配置），设置 DATABASE_URL 可切换到PostgreSQL等
DATABASE_URL = os.getenv(
    'DATABASE_URL',
    f"sqlite:///{BASE_DIR / 'etfworld.db'}"
)

# 支持的指数列表
# source: 'index' 走 index_dailybasic（宽基），'sw' 走 sw_daily（申万行业）
SUPPORTED_INDICES = [
    # 宽基（index_dailybasic）
    {'ts_code': '000001.SH', 'name': '上证综指', 'category': '宽基', 'source': 'index'},
    {'ts_code': '000016.SH', 'name': '上证50', 'category': '宽基', 'source': 'index'},
    {'ts_code': '000300.SH', 'name': '沪深300', 'category': '宽基', 'source': 'index'},
    {'ts_code': '000905.SH', 'name': '中证500', 'category': '宽基', 'source': 'index'},
    {'ts_code': '399001.SZ', 'name': '深证成指', 'category': '宽基', 'source': 'index'},
    {'ts_code': '399005.SZ', 'name': '中小板指', 'category': '宽基', 'source': 'index'},
    {'ts_code': '399006.SZ', 'name': '创业板指', 'category': '宽基', 'source': 'index'},
    {'ts_code': '399016.SZ', 'name': '深证红利', 'category': '红利', 'source': 'index'},

    # 申万一级行业 - "不死行业"（sw_daily）
    {'ts_code': '801780.SI', 'name': '银行', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801790.SI', 'name': '非银金融', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801150.SI', 'name': '医药生物', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801120.SI', 'name': '食品饮料', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801740.SI', 'name': '国防军工', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801080.SI', 'name': '电子', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801750.SI', 'name': '计算机', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801160.SI', 'name': '公用事业', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801960.SI', 'name': '石油石化', 'category': '行业一级', 'source': 'sw'},
    {'ts_code': '801110.SI', 'name': '家用电器', 'category': '行业一级', 'source': 'sw'},

    # 申万二级行业 - 更精准的网格标的（sw_daily）
    {'ts_code': '801193.SI', 'name': '证券Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801194.SI', 'name': '保险Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801125.SI', 'name': '白酒Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801081.SI', 'name': '半导体', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801153.SI', 'name': '医疗器械', 'category': '行业二级', 'source': 'sw'},
    # 银行细分
    {'ts_code': '801782.SI', 'name': '国有大型银行Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801783.SI', 'name': '股份制银行Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801784.SI', 'name': '城商行Ⅱ', 'category': '行业二级', 'source': 'sw'},
    # 医药细分
    {'ts_code': '801151.SI', 'name': '化学制药', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801155.SI', 'name': '中药Ⅱ', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801156.SI', 'name': '医疗服务', 'category': '行业二级', 'source': 'sw'},
    # 消费细分
    {'ts_code': '801127.SI', 'name': '饮料乳品', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801124.SI', 'name': '食品加工', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801128.SI', 'name': '休闲食品', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801111.SI', 'name': '白色家电', 'category': '行业二级', 'source': 'sw'},
    # 科技（高波动，网格友好）
    {'ts_code': '801085.SI', 'name': '消费电子', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801104.SI', 'name': '软件开发', 'category': '行业二级', 'source': 'sw'},
    # 公用事业（不死）
    {'ts_code': '801161.SI', 'name': '电力', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801163.SI', 'name': '燃气Ⅱ', 'category': '行业二级', 'source': 'sw'},
    # 强周期
    {'ts_code': '801735.SI', 'name': '光伏设备', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801737.SI', 'name': '电池', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801077.SI', 'name': '工程机械', 'category': '行业二级', 'source': 'sw'},
    {'ts_code': '801992.SI', 'name': '航运港口', 'category': '行业二级', 'source': 'sw'},
]

# 分位点统计周期（年数，None表示全部历史）
PERCENTILE_PERIODS = {'3y': 3, '5y': 5, '10y': 10, 'all': None}

# 估值区间划分（基于PE分位点）
VALUATION_ZONES = [
    (0, 20, '低估', '#22c55e'),
    (20, 40, '偏低', '#84cc16'),
    (40, 60, '适中', '#eab308'),
    (60, 80, '偏高', '#f97316'),
    (80, 100, '高估', '#ef4444'),
]

# 退出引导阈值（综合分位 = PE/PB 分位平均，readiness 口径）。
# 与买入侧 50% 否决线（readiness_service.VALUATION_VETO）构成 50/70/80 三层递进：
# >50 暂停买入；≥70 只卖不买（对齐首页估值地图高估线）；≥80 建议收网（对齐上方 VALUATION_ZONES 高估线）
EXIT_WARN_PCT = 70.0
EXIT_EXIT_PCT = 80.0

# 行业集中度警告线：单一行业占持仓总市值比例超过该值时提示
CONCENTRATION_WARN_PCT = 40.0

# 资金分配建议：单只新网格计划满格资金占本金的比例上限
MAX_PLAN_CAPITAL_PCT = 15.0

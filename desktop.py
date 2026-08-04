"""
ETFWorld 桌面版启动入口

把后端 (FastAPI/uvicorn) 跑在本地随机端口的后台线程里，再用 pywebview 开一个
原生窗口指向它。双击即用 —— 无需部署、无需手动起服务。

  开发运行:  python desktop.py
  打包 .app: pyinstaller ETFWorld.spec   (见 README)

数据与密钥都存在用户目录，App 本体可只读：
  macOS:   ~/Library/Application Support/ETFWorld/
  Windows: %APPDATA%\\ETFWorld\\
"""
import os
import shutil
import socket
import sys
import threading
import time
import urllib.request
from pathlib import Path


# ---------- 1. 数据 / 配置目录（用户可写）----------
def app_data_dir() -> Path:
    custom_dir = os.environ.get('ETFWORLD_DATA_DIR')
    if custom_dir:
        base = Path(custom_dir).expanduser()
    elif sys.platform == 'darwin':
        base = Path.home() / 'Library' / 'Application Support' / 'ETFWorld'
    elif sys.platform.startswith('win'):
        base = Path(os.environ.get('APPDATA', Path.home())) / 'ETFWorld'
    else:
        base = Path(os.environ.get('XDG_DATA_HOME', Path.home() / '.local' / 'share')) / 'ETFWorld'
    base.mkdir(parents=True, exist_ok=True)
    return base


def _base_dir() -> Path:
    """打包后(_MEIPASS)或源码运行时的资源根目录"""
    if getattr(sys, 'frozen', False):
        return Path(sys._MEIPASS)  # type: ignore[attr-defined]
    return Path(__file__).resolve().parent


DATA_DIR = app_data_dir()
DB_PATH = DATA_DIR / 'etfworld.db'
ENV_PATH = DATA_DIR / '.env'
os.environ.setdefault('ETFWORLD_ENV_PATH', str(ENV_PATH))

# 首次运行：用随包的已回填库做种子（之后用户的计划/交易都写到用户目录这份）
SEED_DB = _base_dir() / 'etfworld.db'
if not DB_PATH.exists() and SEED_DB.exists():
    shutil.copy2(SEED_DB, DB_PATH)

# 数据库指向用户目录（必须在导入后端之前设置）
os.environ.setdefault('DATABASE_URL', f'sqlite:///{DB_PATH}')

# 源码版首次启动时迁移现有配置。打包时不携带 backend/.env，避免泄露密钥。
_source_env = _base_dir() / 'backend' / '.env'
if not ENV_PATH.exists() and not getattr(sys, 'frozen', False) and _source_env.exists():
    shutil.copy2(_source_env, ENV_PATH)

# 密钥优先读用户目录 ~/.../ETFWorld/.env（方便用户改 key 而不动 App 本体）
if ENV_PATH.exists():
    try:
        from dotenv import load_dotenv
        load_dotenv(ENV_PATH, override=True)
    except ImportError:
        pass


# ---------- 2. 选一个空闲端口 ----------
def free_port() -> int:
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind(('127.0.0.1', 0))
    port = s.getsockname()[1]
    s.close()
    return port


PORT = free_port()
URL = f'http://127.0.0.1:{PORT}'
_server = None
_instance_socket = None


# ---------- 3. 后台线程跑 uvicorn ----------
def create_server():
    import uvicorn
    config = uvicorn.Config(
        'backend.api.main:app',
        host='127.0.0.1',
        port=PORT,
        log_level='warning',
        access_log=False,
    )
    return uvicorn.Server(config)


def run_server():
    global _server
    _server = create_server()
    _server.run()


def wait_ready(timeout: float = 25) -> bool:
    start = time.time()
    while time.time() - start < timeout:
        try:
            with urllib.request.urlopen(URL + '/api/health', timeout=1) as response:
                if response.status == 200:
                    return True
        except Exception:
            time.sleep(0.2)
    return False


def acquire_instance_lock() -> bool:
    """用本机端口锁避免重复打开两个写同一数据库的进程。"""
    global _instance_socket
    lock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        lock.bind(('127.0.0.1', 47821))
        lock.listen(1)
    except OSError:
        lock.close()
        return False
    _instance_socket = lock
    return True


def show_error(message: str):
    try:
        import webview
        webview.create_window('ETFWorld', html=f'<h3 style="font-family:sans-serif;padding:24px">{message}</h3>', width=520, height=220)
        webview.start()
    except Exception:
        print(message, file=sys.stderr)


def stop_server():
    if _server is not None:
        _server.should_exit = True


def main():
    if not acquire_instance_lock():
        show_error('ETFWorld 已经在运行。')
        return 0

    threading.Thread(target=run_server, daemon=True).start()
    if not wait_ready():
        show_error(f'本地服务启动失败，请查看日志目录：{DATA_DIR}')
        return 1
    import webview
    window = webview.create_window(
        'ETFWorld · 网格策略终端',
        URL,
        width=1440, height=900, min_size=(1024, 680),
        confirm_close=False,
    )
    window.events.closed += stop_server
    webview.start()  # 阻塞在主线程，关闭窗口即退出（daemon 线程随之结束）
    stop_server()
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

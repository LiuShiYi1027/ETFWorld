"""应用级路径解析：数据目录 / 日志文件位置（桌面版与 web 源码版共用）"""
import os
import sys
from pathlib import Path


def app_data_dir() -> Path:
    """用户可写的数据目录：ETFWORLD_DATA_DIR 环境变量优先，否则按平台约定。

    macOS ~/Library/Application Support/ETFWorld，Windows %APPDATA%\\ETFWorld，
    Linux $XDG_DATA_HOME/ETFWorld。desktop.py 启动时会设置该环境变量，
    保证打包版与后端解析到同一目录。
    """
    custom = os.environ.get('ETFWORLD_DATA_DIR')
    if custom:
        return Path(custom).expanduser()
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'ETFWorld'
    if sys.platform.startswith('win'):
        return Path(os.environ.get('APPDATA', str(Path.home()))) / 'ETFWorld'
    return Path(os.environ.get('XDG_DATA_HOME', str(Path.home() / '.local' / 'share'))) / 'ETFWorld'


def log_file_path() -> Path:
    """日志文件位置（与数据库同目录，方便用户一次性打包反馈）"""
    return app_data_dir() / 'etfworld.log'

"""pytest 共享配置：把所有后端数据库操作隔离到临时库。

pytest 先加载 conftest 再加载测试模块，因此这里顶层设置 DATABASE_URL 后，
测试模块里 `import backend...` 时 backend.utils.db 的引擎才会绑定到临时库，
不会碰项目根目录的真实 etfworld.db。
"""
import os
import tempfile

_TEST_DIR = tempfile.mkdtemp(prefix='etfw_test_')
_TEST_DB = os.path.join(_TEST_DIR, 'test.db')
os.environ['DATABASE_URL'] = f'sqlite:///{_TEST_DB}'
os.environ['ETFWORLD_DATA_DIR'] = _TEST_DIR  # 日志等文件也隔离到临时目录

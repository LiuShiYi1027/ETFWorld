"""
数据库工具：引擎、会话、初始化
"""
import logging
from contextlib import contextmanager

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.config.settings import DATABASE_URL
from backend.models.database import Base

logger = logging.getLogger(__name__)

engine = create_engine(
    DATABASE_URL,
    echo=False,
    connect_args={'check_same_thread': False} if DATABASE_URL.startswith('sqlite') else {},
)

SessionLocal = sessionmaker(bind=engine, autoflush=False, autocommit=False,
                            expire_on_commit=False)


def init_db():
    """创建所有表，并跑增量结构迁移（老库补列、新库直接盖章版本）"""
    Base.metadata.create_all(engine)
    from backend.utils.migrate import run_migrations
    run_migrations(engine)
    logger.info("数据库表创建完成: %s", DATABASE_URL)


@contextmanager
def get_session():
    """事务性会话上下文"""
    session = SessionLocal()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


def get_db():
    """FastAPI依赖用的会话生成器"""
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()

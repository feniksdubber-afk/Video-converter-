"""
dubbing/database/migrations/env.py

XAVFSIZLIK TO'SIG'I: bu env.py faqat `DUBBING_DATABASE_URL` (yoki test
rejimida `DUBBING_TEST_DATABASE_URL`) manzili "dubbing" so'zini o'z ichiga
olgan database nomiga ega bo'lgandagina ishlashga ruxsat beradi. Bu
tasodifan boshqa (masalan, AfsonaMovieBot yoki mavjud botning) bazasiga
qarshi migratsiya ishga tushirilishining oldini olish uchun qo'shilgan
qo'shimcha himoya qatlami — asosiy himoya, albatta, DUBBING_DATABASE_URL'ning
har doim alohida database'ga ishora qilishidir.
"""

import os
import sys
from logging.config import fileConfig

from alembic import context
from sqlalchemy import engine_from_config, pool

# dubbing paketini import qila olish uchun loyiha ildizini sys.path'ga qo'shamiz
sys.path.insert(0, os.path.abspath(os.path.join(os.path.dirname(__file__), "..", "..", "..")))

from dubbing.config import DUBBING_DATABASE_URL, DUBBING_TEST_DATABASE_URL  # noqa: E402

config = context.config

if config.config_file_name is not None:
    fileConfig(config.config_file_name)

target_metadata = None  # raw-SQL migratsiyalar ishlatiladi, ORM metadata yo'q


def _resolve_dsn() -> str:
    dsn = os.environ.get("DUBBING_MIGRATION_DSN") or DUBBING_DATABASE_URL
    if "dubbing" not in dsn:
        raise RuntimeError(
            "XAVFSIZLIK TO'XTATILDI: DUBBING_MIGRATION_DSN/DUBBING_DATABASE_URL "
            "'dubbing' so'zini o'z ichiga olmagan bazaga ishora qilmoqda. "
            "Bu migratsiya faqat dubbing'ga tegishli bazaga qarshi ishlashi "
            "mumkin. Mavjud bot yoki AfsonaMovieBot bazasiga tegilmaydi."
        )
    return dsn


def run_migrations_offline() -> None:
    url = _resolve_dsn()
    context.configure(
        url=url,
        target_metadata=target_metadata,
        literal_binds=True,
        dialect_opts={"paramstyle": "named"},
    )
    with context.begin_transaction():
        context.run_migrations()


def run_migrations_online() -> None:
    dsn = _resolve_dsn()
    # asyncpg DSN'ni SQLAlchemy sync driver (psycopg2 emas — bu yerda faqat
    # migratsiya ishga tushirish uchun oddiy "postgresql://" sxemasi
    # yetarli, chunki Alembic o'zi sync ishlaydi)
    configuration = config.get_section(config.config_ini_section) or {}
    configuration["sqlalchemy.url"] = dsn
    connectable = engine_from_config(
        configuration,
        prefix="sqlalchemy.",
        poolclass=pool.NullPool,
    )

    with connectable.connect() as connection:
        context.configure(connection=connection, target_metadata=target_metadata)
        with context.begin_transaction():
            context.run_migrations()


if context.is_offline_mode():
    run_migrations_offline()
else:
    run_migrations_online()

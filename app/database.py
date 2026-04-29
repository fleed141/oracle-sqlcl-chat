from sqlalchemy.ext.asyncio import create_async_engine, async_sessionmaker, AsyncSession
from sqlalchemy.orm import DeclarativeBase

DATABASE_URL = "sqlite+aiosqlite:///./memory/settings.sqlite"

engine = create_async_engine(DATABASE_URL, echo=False)
async_session = async_sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)


class Base(DeclarativeBase):
    pass


async def get_db() -> AsyncSession:
    async with async_session() as session:
        yield session


async def init_db() -> None:
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

    async with engine.connect() as conn:

        def _migrate(sync_conn):
            result = sync_conn.exec_driver_sql("PRAGMA table_info(connections)")
            existing = {row[1] for row in result}
            if "status" not in existing:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE connections ADD COLUMN status VARCHAR NOT NULL DEFAULT 'unknown'"
                )
            if "status_message" not in existing:
                sync_conn.exec_driver_sql(
                    "ALTER TABLE connections ADD COLUMN status_message VARCHAR NOT NULL DEFAULT ''"
                )
            sync_conn.commit()

        await conn.run_sync(_migrate)
        await conn.commit()

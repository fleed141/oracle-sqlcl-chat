from sqlalchemy import select, delete, update
from sqlalchemy.dialects.sqlite import insert as sqlite_insert
from sqlalchemy.orm import Mapped, mapped_column
from loguru import logger

from app.database import Base, async_session, init_db as _init_db


class AgentSetting(Base):
    __tablename__ = "agent_settings"

    key: Mapped[str] = mapped_column(primary_key=True)
    value: Mapped[str] = mapped_column(nullable=False)


class Connection(Base):
    __tablename__ = "connections"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(unique=True, nullable=False)
    connection_string: Mapped[str] = mapped_column(nullable=False)
    status: Mapped[str] = mapped_column(default="unknown", nullable=False)
    status_message: Mapped[str] = mapped_column(default="", nullable=False)


class LLMConfig(Base):
    __tablename__ = "llm_configs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    label: Mapped[str] = mapped_column(nullable=False)
    provider: Mapped[str] = mapped_column(nullable=False)
    model: Mapped[str] = mapped_column(nullable=False)
    api_key: Mapped[str] = mapped_column(nullable=False)
    is_active: Mapped[bool] = mapped_column(default=False, nullable=False)


async def init_db() -> None:
    await _init_db()


async def get_all_settings() -> dict:
    async with async_session() as session:
        result = await session.execute(select(AgentSetting))
        rows = result.scalars().all()
        return {row.key: row.value for row in rows}


async def get_setting(key: str) -> str | None:
    async with async_session() as session:
        result = await session.execute(
            select(AgentSetting.value).where(AgentSetting.key == key)
        )
        return result.scalar_one_or_none()


async def set_setting(key: str, value: str) -> None:
    async with async_session() as session:
        stmt = (
            sqlite_insert(AgentSetting)
            .values(key=key, value=value)
            .on_conflict_do_update(
                index_elements=["key"],
                set_=dict(value=value),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def delete_setting(key: str) -> None:
    async with async_session() as session:
        await session.execute(delete(AgentSetting).where(AgentSetting.key == key))
        await session.commit()


async def get_connections() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(
            select(Connection).order_by(Connection.id)
        )
        rows = result.scalars().all()
        return [
            {
                "id": row.id,
                "name": row.name,
                "connection_string": row.connection_string,
                "status": row.status,
                "status_message": row.status_message,
            }
            for row in rows
        ]


async def add_connection(name: str, connection_string: str) -> int:
    async with async_session() as session:
        stmt = (
            sqlite_insert(Connection)
            .values(name=name, connection_string=connection_string)
            .on_conflict_do_update(
                index_elements=["name"],
                set_=dict(connection_string=connection_string),
            )
        )
        result = await session.execute(stmt)
        await session.commit()
        return result.lastrowid


async def update_connection_status(
    name: str, status: str, status_message: str = ""
) -> None:
    async with async_session() as session:
        stmt = (
            sqlite_insert(Connection)
            .values(name=name, connection_string="", status=status, status_message=status_message)
            .on_conflict_do_update(
                index_elements=["name"],
                set_=dict(status=status, status_message=status_message),
            )
        )
        await session.execute(stmt)
        await session.commit()


async def remove_connection(conn_id: int) -> None:
    async with async_session() as session:
        await session.execute(delete(Connection).where(Connection.id == conn_id))
        await session.commit()


async def seed_defaults(env_settings) -> None:
    existing = await get_all_settings()

    provider, model, api_key = _detect_llm_config(env_settings)

    defaults = {
        "SYSTEM_PROMPT": env_settings.SYSTEM_PROMPT,
        "FILESYSTEM_PROMPT": env_settings.FILESYSTEM_PROMPT,
        "LANGUAGE": env_settings.LANGUAGE,
        "LLM_PROVIDER": provider,
        "LLM_MODEL": model,
        "LLM_API_KEY": api_key,
        "SQLCL_PATH": env_settings.SQLCL_PATH,
    }
    for key, value in defaults.items():
        if key not in existing:
            await set_setting(key, value)
            logger.info(f"Seeded default setting: {key}")

    connections = await get_connections()
    if not connections:
        from app.config import parse_connection_string

        parsed = parse_connection_string(env_settings.SQLCL_CONNECTIONS)
        for conn in parsed:
            await add_connection(conn["nombre"], conn["cadena"])
            logger.info(f"Seeded default connection: {conn['nombre']}")

    configs = await get_llm_configs()
    env_providers = _detect_all_llm_configs(env_settings)
    env_provider_names = {p[1] for p in env_providers}  # provider key: google, openai, etc.

    if not configs:
        for i, (label, provider, model, api_key) in enumerate(env_providers):
            cid = await add_llm_config(label, provider, model, api_key)
            if i == 0:
                await set_active_llm_config(cid)
            logger.info(f"Seeded LLM config: {label} ({provider}/{model})")
    else:
        for label, provider, model, api_key in env_providers:
            existing = next((c for c in configs if c["provider"] == provider), None)
            if existing:
                if existing["model"] != model or existing["api_key"] != api_key:
                    await update_llm_config(existing["id"], model=model, api_key=api_key)
                    logger.info(f"Synced LLM config: {label} ({provider}/{model})")
            else:
                await add_llm_config(label, provider, model, api_key)
                logger.info(f"Added new LLM config: {label} ({provider}/{model})")
        for c in configs:
            if c["provider"] not in env_provider_names:
                await delete_llm_config(c["id"])
                logger.info(f"Removed LLM config: {c['label']} (no longer in env)")


def _detect_all_llm_configs(env_settings) -> list[tuple[str, str, str, str]]:
    configs = []
    if env_settings.GOOGLE_API_KEY and env_settings.GOOGLE_GEMINI_MODEL:
        configs.append(("Google Gemini", "google", env_settings.GOOGLE_GEMINI_MODEL, env_settings.GOOGLE_API_KEY))
    if env_settings.OPENAI_API_KEY and env_settings.OPENAI_MODEL:
        configs.append(("OpenAI", "openai", env_settings.OPENAI_MODEL, env_settings.OPENAI_API_KEY))
    if env_settings.ANTHROPIC_API_KEY and env_settings.ANTHROPIC_MODEL:
        configs.append(("Anthropic", "anthropic", env_settings.ANTHROPIC_MODEL, env_settings.ANTHROPIC_API_KEY))
    if env_settings.DEEPSEEK_API_KEY and env_settings.DEEPSEEK_MODEL:
        configs.append(("DeepSeek", "deepseek", env_settings.DEEPSEEK_MODEL, env_settings.DEEPSEEK_API_KEY))
    if env_settings.OPENROUTER_API_KEY and env_settings.OPENROUTER_MODEL:
        configs.append(("OpenRouter", "openrouter", env_settings.OPENROUTER_MODEL, env_settings.OPENROUTER_API_KEY))
    return configs


def _detect_llm_config(env_settings) -> tuple[str, str, str]:
    if env_settings.GOOGLE_API_KEY:
        return "google", env_settings.GOOGLE_GEMINI_MODEL, env_settings.GOOGLE_API_KEY
    elif env_settings.DEEPSEEK_API_KEY:
        return "deepseek", env_settings.DEEPSEEK_MODEL, env_settings.DEEPSEEK_API_KEY
    elif env_settings.OPENROUTER_API_KEY:
        return "openrouter", env_settings.OPENROUTER_MODEL, env_settings.OPENROUTER_API_KEY
    elif env_settings.ANTHROPIC_API_KEY:
        return "anthropic", env_settings.ANTHROPIC_MODEL, env_settings.ANTHROPIC_API_KEY
    elif env_settings.OPENAI_API_KEY:
        return "openai", env_settings.OPENAI_MODEL, env_settings.OPENAI_API_KEY
    return "", "", ""


async def get_llm_configs() -> list[dict]:
    async with async_session() as session:
        result = await session.execute(select(LLMConfig).order_by(LLMConfig.id))
        rows = result.scalars().all()
        return [
            {"id": r.id, "label": r.label, "provider": r.provider, "model": r.model, "api_key": r.api_key, "is_active": r.is_active}
            for r in rows
        ]


async def get_active_llm_config() -> dict | None:
    async with async_session() as session:
        result = await session.execute(
            select(LLMConfig).where(LLMConfig.is_active == True)
        )
        row = result.scalar_one_or_none()
        if row:
            return {"id": row.id, "label": row.label, "provider": row.provider, "model": row.model, "api_key": row.api_key, "is_active": row.is_active}
        result = await session.execute(select(LLMConfig).order_by(LLMConfig.id).limit(1))
        row = result.scalar_one_or_none()
        if row:
            return {"id": row.id, "label": row.label, "provider": row.provider, "model": row.model, "api_key": row.api_key, "is_active": row.is_active}
        return None


async def add_llm_config(label: str, provider: str, model: str, api_key: str) -> int:
    async with async_session() as session:
        config = LLMConfig(label=label, provider=provider, model=model, api_key=api_key, is_active=False)
        session.add(config)
        await session.commit()
        await session.refresh(config)
        return config.id


async def update_llm_config(config_id: int, **kwargs) -> None:
    async with async_session() as session:
        result = await session.execute(select(LLMConfig).where(LLMConfig.id == config_id))
        config = result.scalar_one_or_none()
        if config:
            for key, value in kwargs.items():
                setattr(config, key, value)
            await session.commit()


async def set_active_llm_config(config_id: int) -> None:
    async with async_session() as session:
        await session.execute(
            update(LLMConfig).values(is_active=False)
        )
        result = await session.execute(select(LLMConfig).where(LLMConfig.id == config_id))
        config = result.scalar_one_or_none()
        if config:
            config.is_active = True
            await session.commit()


async def delete_llm_config(config_id: int) -> None:
    async with async_session() as session:
        result = await session.execute(select(LLMConfig).where(LLMConfig.id == config_id))
        config = result.scalar_one_or_none()
        if config:
            await session.delete(config)
            await session.commit()


async def sync_sqlcl_connections() -> None:
    import asyncio
    from app.config import (
        sqlcl_list_connections,
        sqlcl_show_connection,
        sqlcl_test_connection,
    )

    loop = asyncio.get_running_loop()
    sqlcl_names = await loop.run_in_executor(None, sqlcl_list_connections)
    logger.info(f"SQLcl saved connections found: {sqlcl_names}")

    for name in sqlcl_names:
        info = await loop.run_in_executor(None, sqlcl_show_connection, name)
        if info:
            user = info.get("user", "")
            cs = info["connect_string"]
            conn_str = f"{user}@{cs}" if user else cs

            existing = await get_connections()
            existing_names = {c["name"] for c in existing}
            if name not in existing_names:
                await add_connection(name, conn_str)
                logger.info(f"Discovered new connection: {name}")

        await update_connection_status(name, "testing", "")
        ok, message = await loop.run_in_executor(None, sqlcl_test_connection, name)
        if ok:
            await update_connection_status(name, "ok", message)
            logger.info(f"Connection '{name}' test: OK")
        else:
            await update_connection_status(name, "failed", message)
            logger.warning(f"Connection '{name}' test: FAILED — {message}")

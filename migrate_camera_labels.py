import asyncio
from backend.database import engine, Base
from backend import models

async def migrate():
    async with engine.begin() as conn:
        await conn.run_sync(lambda sync_conn: Base.metadata.tables["camera_last_label"].drop(sync_conn, checkfirst=True))
        await conn.run_sync(Base.metadata.create_all)
    print("✔ camera_last_label recreated with the new schema")

asyncio.run(migrate())
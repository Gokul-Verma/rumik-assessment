from motor.motor_asyncio import AsyncIOMotorClient, AsyncIOMotorDatabase

from app.config import settings

_client: AsyncIOMotorClient | None = None
_db: AsyncIOMotorDatabase | None = None


async def get_mongo_client() -> AsyncIOMotorClient:
    global _client
    if _client is None:
        _client = AsyncIOMotorClient(settings.mongo_uri)
    return _client


async def get_db(db_name: str | None = None) -> AsyncIOMotorDatabase:
    global _db
    client = await get_mongo_client()
    name = db_name or settings.mongo_db
    if _db is None or db_name is not None:
        _db = client[name]
    return _db


async def close_mongo():
    global _client, _db
    if _client:
        _client.close()
        _client = None
        _db = None

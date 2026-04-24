import os
from neo4j import AsyncGraphDatabase, AsyncDriver
from dotenv import load_dotenv

load_dotenv()

_driver: AsyncDriver | None = None

def get_driver() -> AsyncDriver:
    global _driver
    if _driver is None:
        uri = os.getenv("NEO4J_URI", "bolt://localhost:7687")
        user = os.getenv("NEO4J_USER", "neo4j")
        password = os.getenv("NEO4J_PASSWORD", "changeme")
        _driver = AsyncGraphDatabase.driver(uri, auth=(user, password))
    return _driver

async def close_driver():
    global _driver
    if _driver is not None:
        await _driver.close()
        _driver = None

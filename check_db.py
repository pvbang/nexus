import asyncio
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import create_async_engine, AsyncSession
from sqlalchemy.orm import sessionmaker

# connect directly to the postgres db FROM INSIDE DOCKER
DATABASE_URL = 'postgresql+asyncpg://nexus:nexus_pass@postgres:5432/nexus_db'

async def main():
    engine = create_async_engine(DATABASE_URL)
    session_maker = sessionmaker(engine, class_=AsyncSession, expire_on_commit=False)
    async with session_maker() as session:
        result = await session.execute(text("SELECT id, event_type, data FROM task_events WHERE event_type IN ('tool_start', 'tool_done', 'agent_thinking') ORDER BY created_at DESC LIMIT 5"))
        rows = result.fetchall()
        for idx, r in enumerate(rows):
            print(f"Event {idx}: {r.event_type}")
            if 'params' in r.data:
                print('  Has params field:', bool(r.data['params']), type(r.data['params']))
            else:
                print('  Missing params field!')
            if 'output' in r.data:
                print('  Has output field:', bool(r.data['output']), type(r.data['output']))
                if r.data['output']:
                    print('  Output length:', len(str(r.data['output'])))
            else:
                print('  Missing output field!')
            if 'content' in r.data:
                print('  Has content field:', bool(r.data['content']))
            print("  ---")

asyncio.run(main())

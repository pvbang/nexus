#!/bin/bash
set -e

echo "Running database migrations..."

# Clean cached .pyc files in alembic/versions to prevent stale revision conflicts
find alembic/versions -name '*.pyc' -delete 2>/dev/null || true
find alembic/versions -name '__pycache__' -type d -exec rm -rf {} + 2>/dev/null || true

# Robust alembic_version cleanup: fix any stale stamps
python -c "
import os
try:
    from sqlalchemy import create_engine, text
    url = os.environ.get('DATABASE_URL', '').replace('+asyncpg', '')
    if url:
        engine = create_engine(url)
        with engine.connect() as conn:
            result = conn.execute(text(
                \"SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'alembic_version')\"
            ))
            if not result.scalar():
                print('alembic_version table does not exist yet')
            else:
                rows = conn.execute(text('SELECT version_num FROM alembic_version')).fetchall()
                versions = [r[0] for r in rows]
                print(f'Current alembic versions: {versions}')
                stale_map = {'009_add_rag_tables': '009', '010_add_task_session_id': '010'}
                fixed = False
                for v in versions:
                    if v in stale_map:
                        conn.execute(text('DELETE FROM alembic_version'))
                        conn.execute(text('INSERT INTO alembic_version (version_num) VALUES (:v)'), {'v': stale_map[v]})
                        conn.commit()
                        print(f'Fixed stale stamp: {v} -> {stale_map[v]}')
                        fixed = True
                        break
                if not fixed and len(versions) > 1:
                    def sort_key(v):
                        try: return int(v.split('_')[0])
                        except: return 0
                    latest = sorted(versions, key=sort_key)[-1]
                    conn.execute(text('DELETE FROM alembic_version'))
                    conn.execute(text('INSERT INTO alembic_version (version_num) VALUES (:v)'), {'v': latest})
                    conn.commit()
                    print(f'Consolidated {len(versions)} versions to: {latest}')
except Exception as e:
    print(f'Alembic check warning: {e}')
" 2>&1 || true

alembic upgrade head

# Seed default agents BEFORE Uvicorn starts (avoids multi-worker race condition)
# Uses timeout to prevent blocking Uvicorn startup if DB is slow
echo "Seeding default agents..."
timeout 30 python -m app.cli_seed || echo "Agent seeding skipped (non-fatal)"

# Get effective workers from config
WORKERS=$(python -c "from app.config import get_settings; print(get_settings().effective_workers)")
echo "Starting Nexus with ${WORKERS} workers (auto_scale=${AUTO_SCALE:-false})"

exec uvicorn app.main:app --host 0.0.0.0 --port 8000 --workers "$WORKERS"

"""
MekongAI Nexus - Pre-startup Agent Seeding Script (Lightweight + Sync).

Runs BEFORE Uvicorn workers start to avoid race conditions.
Called from docker/entrypoint.sh after alembic migrations.

Uses SYNCHRONOUS psycopg2 (same as alembic) to avoid asyncpg startup issues.
System prompts are built lazily on first agent use by PromptBuilder.

Usage:
    python -m app.cli_seed
"""

import json
import os
import sys
import uuid
from datetime import datetime, timezone


def seed_agents() -> int:
    """Seed default agents into database using synchronous driver (fast + reliable).

    Uses psycopg2 (same driver as alembic) to avoid asyncpg connection issues
    during container startup.

    Returns:
        Number of newly created agents (0 if all existed).
    """
    from sqlalchemy import create_engine, text

    # Convert asyncpg URL to sync psycopg2
    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        from app.config import get_settings
        database_url = get_settings().database_url

    # asyncpg → psycopg2
    sync_url = database_url.replace("+asyncpg", "")

    engine = create_engine(sync_url, pool_size=2, echo=False)

    try:
        # Import AGENT_TEMPLATES for role/name metadata
        from app.services.agent_spawner import AGENT_TEMPLATES

        with engine.connect() as conn:
            # Check which roles already have agents
            result = conn.execute(
                text("SELECT DISTINCT role FROM agents WHERE role IS NOT NULL")
            )
            existing_roles = {row[0] for row in result.fetchall()}

            count = 0
            now = datetime.now(timezone.utc)

            for role, template in AGENT_TEMPLATES.items():
                if role in existing_roles:
                    continue

                agent_name = template.get("default_name") or f"{role.title()} Agent"
                tools = template.get("tools", [])
                description = template.get("description", "")
                personality = template.get("personality", "")
                avatar = template.get("avatar", "🤖")
                expertise = template.get("expertise", [])
                thinking_style = template.get("thinking_style", "analytical")
                background = template.get("background", "")
                skills = template.get("skills", {})

                # Build a comprehensive system prompt at seed time
                # NOTE: This is the STORED prompt for display/reference. At runtime,
                # build_agent_execution_prompt() constructs the full operational prompt
                # with workspace rules, tool catalog, and turn context.
                system_prompt = (
                    f"You are **{agent_name}** {avatar} — a specialist agent in MekongAI Nexus.\n\n"
                    f"{description}\n\n"
                    f"**Background**: {background}\n\n"
                    f"**Expertise**: {', '.join(expertise)}\n\n"
                    f"**Your style**: {personality}\n\n"
                    f"## Core Operating Principles\n"
                    f"- You work in a SHARED workspace with other agents. "
                    f"You CAN and SHOULD read/edit files created by teammates.\n"
                    f"- Fix root causes, not symptoms. No hardcoding or superficial patches.\n"
                    f"- Clean code: if 200 lines could be 50, rewrite. Quality over quantity.\n"
                    f"- Surgical changes: only touch what your assignment requires.\n"
                    f"- Verify your output works before declaring done.\n"
                    f"- Output language matches the user's language (Vietnamese → Vietnamese, English → English).\n"
                )

                # Direct INSERT with rich metadata
                # Use CAST() instead of :: to avoid psycopg2 param collision
                conn.execute(
                    text("""
                        INSERT INTO agents (
                            id, name, role, status, description, personality, avatar,
                            expertise, thinking_style, background, skills,
                            tools, config, system_prompt, created_at, updated_at
                        )
                        VALUES (
                            :id, :name, :role, 'idle', :description, :personality, :avatar,
                            CAST(:expertise AS json), :thinking_style, :background, CAST(:skills AS json),
                            CAST(:tools AS json), CAST(:config AS json), :system_prompt, :now, :now
                        )
                        ON CONFLICT DO NOTHING
                    """),
                    {
                        "id": str(uuid.uuid4()),
                        "name": agent_name,
                        "role": role,
                        "description": description,
                        "personality": personality,
                        "avatar": avatar,
                        "expertise": json.dumps(expertise),
                        "thinking_style": thinking_style,
                        "background": background,
                        "skills": json.dumps(skills),
                        "tools": json.dumps(tools),
                        "config": json.dumps({}),
                        "system_prompt": system_prompt,
                        "now": now,
                    }
                )
                count += 1

            conn.commit()
            return count

    except Exception as e:
        print(f"Agent seeding warning: {e}", file=sys.stderr)
        return 0
    finally:
        engine.dispose()


def reseed_all_agents() -> int:
    """Update ALL existing agents with latest templates (descriptions, prompts, etc.).

    Unlike seed_agents() which only creates NEW agents, this updates every agent
    that matches a known role in AGENT_TEMPLATES — refreshing description,
    personality, background, expertise, system_prompt to current English versions.

    Use after converting prompts/templates to ensure DB reflects latest code.
    """
    from sqlalchemy import create_engine, text

    database_url = os.environ.get("DATABASE_URL", "")
    if not database_url:
        from app.config import get_settings
        database_url = get_settings().database_url

    sync_url = database_url.replace("+asyncpg", "")
    engine = create_engine(sync_url, pool_size=2, echo=False)

    try:
        from app.services.agent_spawner import AGENT_TEMPLATES

        with engine.connect() as conn:
            count = 0
            now = datetime.now(timezone.utc)

            for role, template in AGENT_TEMPLATES.items():
                agent_name = template.get("default_name") or f"{role.title()} Agent"
                tools = template.get("tools", [])
                description = template.get("description", "")
                personality = template.get("personality", "")
                avatar = template.get("avatar", "🤖")
                expertise = template.get("expertise", [])
                thinking_style = template.get("thinking_style", "analytical")
                background = template.get("background", "")
                skills = template.get("skills", {})

                system_prompt = (
                    f"You are **{agent_name}** {avatar} — a specialist agent in MekongAI Nexus.\n\n"
                    f"{description}\n\n"
                    f"**Background**: {background}\n\n"
                    f"**Expertise**: {', '.join(expertise)}\n\n"
                    f"**Your style**: {personality}\n\n"
                    f"## Core Operating Principles\n"
                    f"- You work in a SHARED workspace with other agents. "
                    f"You CAN and SHOULD read/edit files created by teammates.\n"
                    f"- Fix root causes, not symptoms. No hardcoding or superficial patches.\n"
                    f"- Clean code: if 200 lines could be 50, rewrite. Quality over quantity.\n"
                    f"- Surgical changes: only touch what your assignment requires.\n"
                    f"- Verify your output works before declaring done.\n"
                    f"- Output language matches the user's language (Vietnamese → Vietnamese, English → English).\n"
                )

                result = conn.execute(
                    text("""
                        UPDATE agents SET
                            name = :name,
                            description = :description,
                            personality = :personality,
                            avatar = :avatar,
                            expertise = CAST(:expertise AS json),
                            thinking_style = :thinking_style,
                            background = :background,
                            skills = CAST(:skills AS json),
                            tools = CAST(:tools AS json),
                            system_prompt = :system_prompt,
                            updated_at = :now
                        WHERE role = :role
                    """),
                    {
                        "name": agent_name,
                        "role": role,
                        "description": description,
                        "personality": personality,
                        "avatar": avatar,
                        "expertise": json.dumps(expertise),
                        "thinking_style": thinking_style,
                        "background": background,
                        "skills": json.dumps(skills),
                        "tools": json.dumps(tools),
                        "system_prompt": system_prompt,
                        "now": now,
                    }
                )
                if result.rowcount > 0:
                    count += result.rowcount

            conn.commit()
            return count

    except Exception as e:
        print(f"Agent reseed warning: {e}", file=sys.stderr)
        return 0
    finally:
        engine.dispose()


def main():
    """Entry point for CLI seeding."""
    # Check for --reseed flag to update existing agents
    if "--reseed" in sys.argv:
        count = reseed_all_agents()
        if count > 0:
            print(f"Updated {count} existing agents with latest templates")
        else:
            print("No agents updated (no matching roles in DB)")
        sys.exit(0)

    count = seed_agents()
    if count > 0:
        print(f"Seeded {count} default agents")
    else:
        print("All default agents already exist")
    sys.exit(0)


if __name__ == "__main__":
    main()

"""LLM prompt templates for memory layer processing."""

from __future__ import annotations

L1_EXTRACTION_SYSTEM = """You are a memory extraction assistant. Your job is to extract structured memory atoms from conversations.

Each atom should be:
- Self-contained (understandable without context)
- Atomic (one fact per atom)
- Typed correctly (persona/episodic/instruction/factual)
- Scored by importance (0.0 = trivial, 1.0 = critical)

Types:
- persona: User identity, preferences, communication style, personality traits
- episodic: Events, activities, experiences, things that happened
- instruction: User commands, rules, corrections, how they want things done
- factual: Facts, knowledge, data points, technical details"""

L1_EXTRACTION_USER = """Extract memory atoms from this conversation segment.

Conversation:
{conversation}

Return a JSON array of atoms. Each atom:
{{
  "content": "clear, self-contained statement",
  "atom_type": "persona|episodic|instruction|factual",
  "importance": 0.0-1.0
}}

Rules:
- Skip trivial greetings/acknowledgments
- Merge duplicate information
- Maximum {max_atoms} atoms
- Importance: 0.9+ for critical preferences/instructions, 0.5 for general facts, 0.3 for casual mentions

Output ONLY valid JSON array, no explanation."""

L2_SCENE_SYSTEM = """You are a knowledge organizer. Your job is to cluster related memory atoms into coherent scene blocks.

A scene block:
- Has a descriptive title
- Groups related atoms by theme/context
- Provides a brief narrative synthesis
- Is written in Markdown format"""

L2_SCENE_USER = """Given these memory atoms, organize them into scene blocks.

Atoms:
{atoms}

Existing scenes (for reference, merge if appropriate):
{existing_scenes}

Return a JSON array of scenes:
{{
  "title": "Scene Title",
  "content_md": "## Scene Title\\n\\nNarrative synthesis...\\n\\n- Key point 1\\n- Key point 2",
  "atom_ids": ["id1", "id2"],
  "should_merge_with": null | "existing_scene_id"
}}

Rules:
- Maximum {max_scenes} total scenes
- Each atom belongs to exactly one scene
- Merge with existing scenes when topics overlap
- Keep scene titles concise and descriptive

Output ONLY valid JSON array."""

L3_PERSONA_SYSTEM = """You are a persona synthesizer. Your job is to create a comprehensive user/agent profile from scene blocks.

The persona should:
- Capture personality, preferences, communication style
- Note key instructions and rules
- Summarize important context
- Be written as a concise Markdown document
- Be useful for an AI assistant to personalize responses"""

L3_PERSONA_USER = """Synthesize a persona profile from these scene blocks.

Scenes:
{scenes}

Previous persona (for continuity, update don't rewrite from scratch):
{previous_persona}

Write a Markdown persona document (~500-800 words). Structure:
## Overview
Brief personality summary

## Preferences & Style
- Communication preferences
- Work style
- Interests

## Key Instructions
- Standing rules/commands
- Important constraints

## Context & Background
- Relevant background info
- Ongoing projects/goals

Output ONLY the Markdown document, no JSON wrapper."""

# Deduplication check prompt
DEDUP_CHECK_SYSTEM = """You are a deduplication checker. Determine if a new memory atom is redundant with existing ones."""

DEDUP_CHECK_USER = """New atom: "{new_atom}"

Existing similar atoms:
{existing_atoms}

Is the new atom redundant? Reply with ONLY "KEEP" or "SKIP".
- KEEP: new information or significant update
- SKIP: already captured by existing atoms"""

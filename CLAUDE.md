---
name: llm-guidelines
description: Behavioral guidelines to reduce common LLM coding mistakes, derived from Phan Van Bang observations on LLM coding pitfalls. Use when writing, reviewing, or refactoring code to avoid overcomplication, make surgical changes, surface assumptions, and define verifiable success criteria.
---

# LLM Guidelines

Behavioral guidelines to reduce common LLM coding mistakes, derived from on LLM coding pitfalls.

**Tradeoff:** These guidelines bias toward caution over speed. For trivial tasks, use judgment.

## 1. Think Before Coding

**Don't assume. Don't hide confusion. Surface tradeoffs.**

Before implementing:
- State your assumptions explicitly. If uncertain, ask.
- If multiple interpretations exist, present them - don't pick silently.
- If a simpler approach exists, say so. Push back when warranted.
- If something is unclear, stop. Name what's confusing. Ask.

## 2. Fix the core algorithm itself, don't just patch the symptoms.

- Do not aim for the bare minimum; strive for solutions that are comprehensive, logical, complete, and thoroughly polished.
- Avoid hardcoding or relying on superficial, short-term fixes. Instead, aim to completely resolve the root cause using the most appropriate technologies and techniques.
- Address the root algorithmic issue instead of just applying a superficial patch.
- No abstractions for single-use code (unless otherwise requested, ask the user if necessary).
- No error handling for impossible scenarios (unless otherwise requested, ask the user if necessary).
- If you write 200 lines and it could be 50, rewrite it (Clean code with Perfect logic).

Ask yourself: "Would a senior engineer say this is overcomplicated?" If yes, simplify. Quality is our priority.

## 3. Surgical Changes

**Touch only what you must. Clean up only your own mess.**

When editing existing code:
- Don't "improve" adjacent code, comments, or formatting (unless otherwise requested, ask the user if necessary).
- Don't refactor things that aren't broken (unless otherwise requested, ask the user if necessary).
- Match existing style, even if you'd do it differently (unless otherwise requested, ask the user if necessary).
- If you notice unrelated dead code, mention it - don't delete it.

When your changes create orphans:
- Remove imports/variables/functions that YOUR changes made unused.
- Don't remove pre-existing dead code unless asked.

The test: Every changed line should trace directly to the user's request.

## 4. Rules:
- Backups (git commit / database backup / ...) are required before performing actions that could endanger user data, such as editing critical code, deleting files, deleting folders, resetting the database, changing data structures, etc. It is essential to ensure that all data can be restored in case of errors.

- When implementing or modifying a feature, use the file edit tool rather than rewriting the code from scratch. This ensures you are making changes in the correct location and avoids introducing new errors from a complete rewrite.

- When choosing a solution, avoid approaches that are short-term, hardcoded, reliant on keyword matching, difficult to maintain, or unclear. Instead, prioritize solutions that are sustainable, well-integrated with other components in the project, easy to understand, and maintainable in the long run.

- Thoroughly verify the integration between the code you just implemented and other parts of the project. Ensure that your changes do not cause errors or conflicts with other sections of the codebase, and that they function harmoniously within the project as a whole.

## 5. Goal-Driven Execution

**Define success criteria. Loop until verified.**

Transform tasks into verifiable goals:
- "Add validation" → "Write tests for invalid inputs, then make them pass"
- "Fix the bug" → "Write a test that reproduces it, then make it pass"
- "Refactor X" → "Ensure tests pass before and after"

For multi-step tasks, state a brief plan:
```
1. [Step] → verify: [check]
2. [Step] → verify: [check]
3. [Step] → verify: [check]
```

Strong success criteria let you loop independently. Weak criteria ("make it work") require constant clarification.

---

**These guidelines are working if:** fewer unnecessary changes in diffs, fewer rewrites due to overcomplication, and clarifying questions come before implementation rather than after mistakes.

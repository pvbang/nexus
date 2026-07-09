# Bounty Pipeline — Autonomous Money Maker

Orchestrate the full bounty hunting pipeline with strict verification gates.

## Input
- `$ARGUMENTS` — One of:
  - A specific bounty URL to execute
  - "scan" — find and qualify new targets
  - "monitor" — check status of all submitted PRs
  - "status" — show pipeline summary from ledger
  - Empty — run full autonomous cycle

## Pipeline Architecture

```
┌─────────┐    ┌──────────┐    ┌─────────┐    ┌──────────┐    ┌─────────┐
│  SCOUT  │───▶│ QUALIFY  │───▶│ EXECUTE │───▶│ SUBMIT   │───▶│ MONITOR │
│ (find)  │    │ (verify) │    │ (code)  │    │ (PR+claim)│   │ (watch) │
└─────────┘    └──────────┘    └─────────┘    └──────────┘    └─────────┘
                    │ FAIL           │ test fail      │ rejected
                    ▼                ▼                ▼
                  SKIP          FIX+RETRY        NEXT TARGET
```

## Phase 1: SCOUT (find opportunities)

Delegate to `bounty_scout` agent:
1. Call `search_bounties` (platform: all, skills: [python, typescript, rust])
   - Covers: Superteam, GitHub, Algora, Huntr, Immunefi, CodeHawks, HackenProof, BOSS.dev
2. Call `github_browse_bounties` (language: python, typescript)
3. Web search for: "algora bounties open", "huntr ai bounties", "codehawks active contests 2026"
4. Check Devpost hackathons: "devpost.com/hackathons?themes[]=Artificial+Intelligence"
5. Register ALL found targets in `earnings_ledger` (action: add)

### Platform Priority (by ROI for our skills):
1. **Algora/GitHub** — TS/Rust OSS bounties, quick wins $50-500
2. **Huntr** — AI/ML security, LOW competition, Python A+
3. **Superteam Earn** — Rust/Solana, $100-5K
4. **CodeHawks First Flights** — Entry audit contests, $3K-80K
5. **Immunefi** — Web3 critical bugs, Rust programs, $1K-15M
6. **HackenProof** — Filter by Python/Rust, 315 programs
7. **BOSS.dev** — Very low competition OSS bounties
8. **Devpost hackathons** — AI agent hackathons $20K-70K

## Phase 2: QUALIFY (strict verification gate)

Delegate to `bounty_qualifier` agent. For EACH target from Phase 1:
1. Call `verify_bounty_target` with the issue URL
2. **GATE**: Only targets with verdict = "PASS" proceed
3. Update ledger: PASS → status "QUALIFIED", FAIL → status "REJECTED"
4. Rank qualified targets by ROI (reward_usd / estimated_hours)
5. Select TOP 1-3 targets to execute

## Phase 3: EXECUTE (code the solution)

Delegate to `bounty_executor` agent for each selected target:
1. Read full issue spec + repo CONTRIBUTING.md
2. Fork repo → verify fork exists via API
3. Create branch, implement solution, write tests
4. Run project's test suite → must pass
5. Push to fork → verify push via API
6. Update ledger: status "EXECUTING"

**ANTI-HALLUCINATION CHECKPOINT:**
After executor claims done, VERIFY:
- `github_api GET /repos/pvbang/{repo}` → must return 200
- `github_api GET /repos/pvbang/{repo}/branches/{branch}` → must exist
- If verification fails → DO NOT proceed to submit

## Phase 4: SUBMIT (create PR + claim)

Delegate to `bounty_executor` or handle directly:
1. Create PR via `github_create_pr` with:
   - Clear title referencing the issue
   - Body explaining the solution
   - Wallet address for payment
   - "Fixes #N" to auto-link
2. Call `prepare_bounty_submission` to record
3. Post claim comment on the issue (if not locked)
4. Update ledger: status "SUBMITTED", pr_url set

**ANTI-HALLUCINATION CHECKPOINT:**
After PR creation, VERIFY:
- Parse the returned PR URL
- `github_api GET /repos/{owner}/{repo}/pulls/{number}` → must return valid data
- If fails → mark as FAILED, do not count

## Phase 5: MONITOR (watch until resolution)

**NOTE: A background cron (`bounty_pr_monitor`) automatically polls all active PRs
every 15 minutes and sends Telegram alerts when action is needed (CI fail, changes
requested, comments, merged, closed). Check Telegram first before manually polling.**

When running manually or responding to an alert, delegate to `bounty_monitor` agent:
1. Call `monitor_pr_status` for EVERY PR in SUBMITTED/MONITORING status
2. Handle each case:
   - **CI Failing**: Extract failure → dispatch executor to fix → push → re-check
   - **Changes Requested**: Extract feedback → dispatch executor → push fix
   - **Review Comments**: Use `respond_pr_review` to acknowledge
   - **Stale (>7 days)**: Post polite ping
   - **Merged**: Update ledger → MERGED, track payment
   - **Closed**: Update ledger → REJECTED, note reason
3. Update ledger status for each PR
4. Report summary via `send_telegram`

## Full Autonomous Cycle

When no arguments provided, run this loop:
1. **Monitor first** — check all existing PRs (fix any issues, respond to reviews)
2. **Scout** — find new opportunities (bounties + hackathons + freelance + competitions)
3. **Qualify** — verify targets
4. **Execute** — code top qualified target
5. **Submit** — create PR / submit to hackathon / apply to gig
6. **Report** — send Telegram summary
7. **Update ledger** — earnings_ledger action: summary
8. **If idle** (all PRs waiting, no new bounties) — pivot to secondary strategies:
   - Browse Devpost for hackathons with deadlines < 14 days
   - Browse Topcoder/Kaggle for active challenges
   - Search for paid technical writing opportunities
   - Look for freelance micro-gigs on Contra/Topcoder
   - Build small utility tools that can be monetized

## Safety Rules
- NEVER commit private keys or seed phrases
- Only use public wallet addresses from developer_profile.json
- Always run tests before pushing
- Always verify via API before claiming success
- If an issue is LOCKED → skip immediately
- If competition > 10 comments → skip unless reward > $500

## Wallet Selection
- Solana ecosystem (Superteam) → platform_wallet `A337Br3xkdRHwwKqDBQzbG1Zwtz1PqNHm5FUkwzGDxpA`
- EVM ecosystem (Algora, Immunefi, Cantina, Sherlock, CodeHawks, HackenProof, Huntr, HackerOne, Bugcrowd, BOSS.dev) → `wallets.ethereum` 0xYOUR_EVM_WALLET
- USDT TRC-20 → `wallets.tron`
- PayPal (for platforms supporting it) → phanvanbang.dev@gmail.com

## Reporting
After each cycle, send Telegram message with:
- New targets found (count)
- Targets qualified (count)
- PRs submitted this cycle
- PR status updates (CI fixes, reviews addressed)
- Current pipeline value ($)
- Total earned to date ($)

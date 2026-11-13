# QuizBotPro — Production Rollback & Disaster Recovery Runbook

This runbook defines the authoritative operational procedures for rolling back application code, restoring database consistency, and recovering from production incidents for QuizBotPro.

---

## 1. Incident Classification & Recovery Paths

| Incident Type | Symptoms | Immediate Recovery Action | Recovery Time Target |
| :--- | :--- | :--- | :--- |
| **P0: Application Crash Loop** | Render container restarts continuously; exit code != 0 | Render Dashboard Rollback or Redeploy previous SHA | < 3 minutes |
| **P0: Bot Unresponsive / Conflict** | Telegram `/start` does not reply; 409 Conflict | Restart Render service; ensure single polling instance | < 2 minutes |
| **P0: Database Identity Rejection** | `DatabaseIdentityMismatchError` in startup logs | Verify Render `EXPECTED_DATABASE_IDENTITY` & Neon endpoint | < 5 minutes |
| **P1: Deployment SHA Mismatch** | `/health` reports old or unexpected commit SHA | Trigger Deploy Hook with explicit `&ref=<EXPECTED_SHA>` | < 4 minutes |
| **P1: Failed Health Checks** | HTTP 502/503 from Render; `/health` times out | Check application port binding; Rollback Render build | < 3 minutes |
| **P1: Schema Incompatibility** | SQL syntax error on query execution | Roll back application; assess additive schema fix | < 5 minutes |
| **P0: Data Corruption / Deletion** | Accidental table drop or malformed batch update | Neon Point-in-Time Recovery (PITR) to pre-incident second | 5–15 minutes |

---

## 2. Fast-Path Rollback Procedures

### Method A: One-Click Rollback via Render Dashboard (Fastest)

1. Open the [Render Dashboard](https://dashboard.render.com).
2. Select the production service: **`quizbotpro`**.
3. In the left navigation, click **Deploys**.
4. Locate the **last known healthy deploy** (marked with a green checkmark prior to the incident).
5. Click the **three dots (`...`)** menu on that deployment row.
6. Select **Rollback to this deploy**.
7. Render immediately restarts the container with the exact container image / artifact of that verified deploy.

### Method B: Exact-SHA Redeployment via Deploy Hook

If the dashboard is inaccessible or automated rollback is required via CLI:

```bash
# Set target to the last known good commit SHA (e.g. 68a550af22709b3299db9edd5249c11b4a812272)
TARGET_SHA="<LAST_KNOWN_GOOD_SHA>"

# Trigger Render production deployment hook with explicit ref
curl -fsS -X POST "${RENDER_PRODUCTION_DEPLOY_HOOK}&ref=${TARGET_SHA}"
```

### Method C: Post-Rollback Verification

Immediately verify the service has recovered and is serving the expected commit:

```bash
# 1. Probe the production health endpoint
curl -fsS https://quizbotpro.onrender.com/health

# Expected response format:
# {"status": "healthy", "commit": "<LAST_KNOWN_GOOD_SHA>", "environment": "production"}

# 2. Run automated verification script:
python .github/verify_deployment.py "<LAST_KNOWN_GOOD_SHA>" "https://quizbotpro.onrender.com/health" 300 10
```

---

## 3. Database Safety & Migration Rollback Strategy

### The Expand-and-Contract Architecture

QuizBotPro strictly enforces the **Expand-and-Contract** (additive) schema evolution pattern:
1. **Never drop or rename active columns or tables.**
2. When introducing new fields, create them as nullable or with safe defaults:
   - *Phase 1 (Expand)*: Add new column (both old and new app versions continue to function).
   - *Phase 2 (Migrate)*: Write data to new column.
   - *Phase 3 (Contract)*: Only after multiple release cycles and full deprecation, retire old fields.

Because all schema modifications are additive:
- **Application rollbacks require NO database rollbacks.** Older application versions safely ignore newly added columns.
- Rollback risk is decoupled from database storage.

### Point-in-Time Recovery (PITR) for Catastrophic Data Incidents

> [!WARNING]
> **Operational Assumption & Retention Window Verification**:
> Point-in-Time Recovery strictly depends on the active WAL change history retention window configured in the Neon Console (**Settings > Postgres**). Neon limits retention by plan tier (Free: up to 6h; Launch: up to 7d; Scale: up to 30d; default on paid plans is 1d). Do not assume maximum retention is active. Before attempting PITR, operators must verify in the Neon Console that `current_time - target_timestamp` is within the project's configured retention window.

If unrecoverable data corruption or unintended record deletion occurs:
1. Identify the exact UTC timestamp 1 minute prior to corruption (e.g. `2026-11-13T14:20:00Z`).
2. Create an instant recovery branch in Neon Console or CLI:
   ```bash
   neon branches create \
     --project-id <neon-project-id> \
     --parent production \
     --name recovery-incident-$(date +%Y%m%d) \
     --point-in-time "2026-11-13T14:20:00Z"
   ```
3. Verify recovered row counts via read-only query.
4. Update `DATABASE_URL` and `EXPECTED_DATABASE_IDENTITY` in Render Dashboard.
5. Restart the production service.

---

## 4. Telegram Polling Concurrency Safeguards

QuizBotPro runs in **long-polling** mode (`run_polling`). Telegram strictly permits only **one active polling connection per bot token**:

- **Symptom of duplicate polling instances**:
  `telegram.error.Conflict: Terminated by other getUpdates request; make sure that only one bot instance is running`
- **Resolution**:
  1. If two Render containers are temporarily active during deployment overlap, the concurrency group `production-deployment` ensures deployments are serialized.
  2. If an orphaned container persists, navigate to Render Dashboard > **Settings** > **Suspend Service**, wait 10 seconds, then **Resume Service**.
  3. Probing the bot: Send `/start` in Telegram to `@my_pro_quiz_bot` (or production bot username) to confirm responsive single-instance polling.

---

## 5. Incident Escalation & Post-Mortem Checklist

Following every production rollback:
1. **Preserve Logs**: Export Render deployment and application logs for the failed deployment before restarting or triggering new builds.
2. **Tag Incident in Git**:
   ```bash
   git tag -a incident-YYYYMMDD -m "Incident: rolled back from <FAILED_SHA> to <RECOVERED_SHA>"
   ```
3. **Notify Stakeholders**: Update team channel with incident duration, root cause category, and resolution status.
4. **Conduct Blameless Post-Mortem**: Document why CI/CD quality gates did not catch the issue and implement corresponding regression tests.


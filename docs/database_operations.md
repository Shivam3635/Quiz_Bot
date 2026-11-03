# QuizBotPro — Database Operations, Migration & Disaster Recovery Guide

## 1. Architecture Overview & Isolation Boundaries

QuizBotPro relies on **Neon Serverless PostgreSQL** for transactional persistence, using synchronous **SQLAlchemy 2.0** and **psycopg2**.

### Environment Isolation Policy
- **Production (`main` branch)**:
  - Dedicated Neon database endpoint and project branch (`production`).
  - Strict TLS (`sslmode=verify-full`, `channel_binding=require`, explicit trusted root CA bundle).
  - Driver-level hostname identity verification against `EXPECTED_DATABASE_IDENTITY`.
- **Staging (`develop` branch)**:
  - Completely separate Neon project/branch and isolated credentials.
  - Fail-closed isolation preventing cross-environment data leakage.
- **Testing (`pytest` / CI)**:
  - 100% in-memory SQLite isolation (`sqlite:///:memory:`).
  - Explicitly decoupled via `tests/conftest.py` to prevent any connection to live staging or production databases.

---

## 2. Schema Lifecycle & Migration Strategy

### Current Mechanism
QuizBotPro manages database schema during startup via `app.database.database.init_db()`:
1. Performs mandatory host and driver identity verification (`verify_database_identity`).
2. Invokes `Base.metadata.create_all(bind=conn)` on the verified connection.
3. Executes idempotent, additive column checks (e.g. SQLite legacy column migration).

### Safe Forward Migration Principles
1. **Additive Changes First**: Never drop or rename columns in an active release. Always add new nullable columns or columns with defaults first.
2. **Branch-First Pre-Testing**: Before running migrations on production, spawn an ephemeral Neon branch from `production`:
   ```bash
   neon branches create --project-id <project-id> --parent production --name migration-test-dryrun
   ```
   Execute the migration and verification script against the branch connection string with `sslmode=verify-full`.
3. **Future Alembic Integration**:
   When transitioning to full versioned migrations via Alembic:
   - Ensure all migration operations run inside explicit transactional blocks (`with op.batch_alter_table(...)`).
   - Every revision must provide both `upgrade()` and `downgrade()` functions.
   - Run `alembic upgrade head` as a Render pre-deploy command before launching the bot process.

---

## 3. Neon Point-in-Time Recovery (PITR) & Instant Branching

Neon maintains a continuous write-ahead log (WAL) and copy-on-write storage architecture, enabling instantaneous branching and Point-in-Time Recovery (PITR) within the project's history retention window.

### History Retention Windows & Plan Limits

Point-in-Time Recovery (Instant Restore) depends strictly on the availability of WAL change history. It is essential to distinguish between two distinct concepts:

1. **Plan Maximum Retention**:
   The upper limit of history retention permitted by the project's Neon subscription tier. This is a plan-level capability ceiling, not necessarily the active operational setting for any specific project.
2. **Configured Retention Window**:
   The actual duration for which change history is retained for a specific Neon project (configured in the Neon Console under **Settings > Postgres** or via the API parameter `history_retention_seconds`). It can be set anywhere from 0 up to the plan maximum (defaulting to 1 day on paid plans, or 6 hours on Free). Operators must never assume a project has the maximum retention configured.

> [!WARNING]
> **Actual project retention: Not verified — check the Neon Console.**
> Do not assume the QuizBotPro staging or production database is currently operating with maximum retention. The active window must be verified directly in the Neon Console before relying on PITR.

#### Verified Neon Plan Retention Limits

The following values are verified against current official Neon documentation: [Neon Pricing](https://neon.com/pricing) and [Neon PITR Documentation](https://neon.com/docs/introduction/point-in-time-restore).

| Neon plan | Maximum available restore window | Notes |
| :--- | :--- | :--- |
| **Free** | 6 hours (or 1 GB of changes, whichever comes first) | Default is 6 hours; capped at 1 GB of changes (whichever comes first). Included at no cost. Cannot be configured beyond 6 hours. Setting the window to 0 disables PITR. |
| **Launch** | Up to 7 days | Default is 1 day. Configurable from 0 up to 7 days. Change history is billed under instant restore storage at $0.20 per GB-month based on data change volume. |
| **Scale** | Up to 30 days | Default is 1 day. Configurable from 0 up to 30 days. Change history is billed under instant restore storage at $0.20 per GB-month based on data change volume. |
| **Enterprise** | Contract-dependent | Contract-dependent. Tailored retention windows, custom SLAs, and dedicated agreements arranged via enterprise contract with Neon. |

*Storage & Billing Conditions*:
- History storage is consumed by Write-Ahead Log (WAL) records retained across the selected history window.
- On paid plans, history storage is billed at **$0.20 per GB-month** based on the volume of data changes within that window.
- Setting the history window to 0 minimizes storage charges but completely disables Instant Restore and Time Travel queries.
- For retention requirements beyond 30 days, projects must use scheduled branch snapshots or automated off-site logical backups (`pg_dump`).

---

### Operational Pre-Recovery Checklist

Before relying on PITR or initiating recovery during an incident or maintenance event, an operator must:
1. **Check the project's current Neon plan**: Determine whether the project is on the Free, Launch, Scale, or Enterprise tier to understand the maximum available history window.
2. **Check the configured restore-history window in the Neon Console**: Navigate to **Settings > Postgres** (or **Project settings > Storage**) in the Neon Console to verify the actual active retention duration.
3. **Confirm that the desired recovery timestamp falls within that window**: Verify that `current_time - target_recovery_timestamp` is strictly less than the configured retention window. Data changes prior to the configured window are permanently pruned from the WAL and cannot be recovered via PITR.
4. **Verify the restored branch before switching application connections**:
   Spin up the recovery branch and connect with strict TLS enforcement before updating application routing. The connection must enforce:
   - `sslmode=verify-full`
   - Explicit CA certificate validation (`sslrootcert`)
   - SCRAM-SHA-256 with `channel_binding=require`
   - Hostname verification against `EXPECTED_DATABASE_IDENTITY`
   *Never weaken TLS or downgrade to `sslmode=require` during recovery.*

---

### Operational Recovery Targets & Scope

> [!IMPORTANT]
> The figures below represent **operational planning targets and estimates**, not contractual platform guarantees. Actual RPO and RTO depend upon the configured Neon plan, active WAL retention window, and operational execution (including data validation and container redeployment). They must be verified via periodic recovery drills.

- **Estimated Target Recovery Point Objective (RPO)**:
  - Planning Target: **< 5 seconds**.
  - Feasibility: Powered by continuous WAL streaming to Neon storage nodes. However, transactions cannot be recovered beyond the configured retention window or if uncommitted at incident time.
- **Estimated Target Recovery Time Objective (RTO)**:
  - Branch Instantiation: Under 60 seconds (instant copy-on-write branch creation).
  - **Full Application Recovery**: **5–15 minutes**.
  - End-to-End Scope: Database branch creation itself occurs in seconds via copy-on-write pointers. However, total application recovery time must account for:
    1. Incident detection, diagnosis, and identification of the target UTC timestamp.
    2. CLI or Console recovery branch provisioning.
    3. Strict TLS read-only schema and data validation on the new branch.
    4. Updating `DATABASE_URL` and `EXPECTED_DATABASE_IDENTITY` in the Render dashboard.
    5. Render container redeployment/restart, health checks, and startup database verification.

---

### PITR Restoration Procedure via Neon CLI / Console

If catastrophic data corruption or accidental table deletion occurs at `2026-11-03 14:30:00 UTC`:

#### Step 1: Identify the exact target timestamp
Determine the last known healthy UTC timestamp prior to the incident (e.g., `2026-11-03T14:29:55Z`). Ensure this timestamp falls within the project's active retention window.

#### Step 2: Create a recovery branch from the point in time
```bash
neon branches create \
  --project-id <neon-project-id> \
  --parent production \
  --name recovery-pitr-20261103 \
  --point-in-time "2026-11-03T14:29:55Z"
```
*Note: This creates an independent, fully isolated PostgreSQL database instance in seconds without duplicating physical data.*

#### Step 3: Validate the recovered data (Strict TLS Verification)
Connect to the recovery branch and verify table integrity. **Never bypass certificate verification during recovery.** Enforce `sslmode=verify-full`, specify a trusted CA bundle, and require SCRAM channel binding:

```bash
psql "postgresql://<user>:<password>@<recovery-endpoint>.neon.tech/<dbname>?sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt&channel_binding=require" \
  -c "SELECT count(*) FROM quiz_sets; SELECT count(*) FROM users;"
```
*(On Windows or custom systems, set `sslrootcert` to the system certificate bundle or Python certifi path).*

#### Step 4: Promote or Switch Service Endpoint
To direct production traffic to the recovered branch:
1. Retrieve the new endpoint hostname:
   ```bash
   neon endpoints list --project-id <neon-project-id>
   ```
2. Update the environment variables in the Render Production dashboard:
   - `DATABASE_URL`: Update with the new branch connection string (including `sslmode=verify-full` and `channel_binding=require`).
   - `EXPECTED_DATABASE_IDENTITY`: Update to match the new recovery endpoint hostname (e.g., `ep-recovery-123456.us-east-2.aws.neon.tech`). **If this is not updated, application startup will fail closed with `DatabaseIdentityMismatchError`.**
3. Trigger a service restart on Render. The bot will start up, verify the new identity, and resume normal operations.

---

## 4. Off-Site Logical Backups & Restoration Runbook

While Neon provides built-in PITR, maintaining off-site logical backups is required for complete cloud disaster recovery (e.g. cross-provider failover or long-term compliance archives).

### Automated Logical Backup Procedure (`pg_dump`)

Execute a clean schema + data dump enforcing strict TLS:
```bash
pg_dump \
  --dbname="${PRODUCTION_DATABASE_URL}&sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt&channel_binding=require" \
  --format=custom \
  --compress=9 \
  --no-owner \
  --no-privileges \
  --file="backup_quizbotpro_$(date +%Y%m%d_%H%M%S).dump"
```

### Off-Site Storage
- Encrypt backups using AES-256 (`gpg --symmetric`).
- Upload to versioned, immutable object storage (AWS S3 / Cloudflare R2 / GCS) with a 30-day lifecycle retention rule.

### Full Database Disaster Recovery Runbook (`pg_restore`)

If a completely new database instance must be provisioned from a backup dump:

#### Step 1: Provision a fresh database instance
Create a new project or database branch in Neon.

#### Step 2: Restore schema and data
```bash
pg_restore \
  --dbname="${NEW_DATABASE_URL}&sslmode=verify-full&sslrootcert=/etc/ssl/certs/ca-certificates.crt&channel_binding=require" \
  --clean \
  --if-exists \
  --no-owner \
  --no-privileges \
  --verbose \
  "backup_quizbotpro_<timestamp>.dump"
```

#### Step 3: Verify Integrity & Sequences
```sql
SELECT table_name FROM information_schema.tables WHERE table_schema = 'public';
SELECT setval('users_id_seq', (SELECT MAX(id) FROM users));
SELECT setval('quiz_sets_id_seq', (SELECT MAX(id) FROM quiz_sets));
SELECT setval('questions_id_seq', (SELECT MAX(id) FROM questions));
SELECT setval('options_id_seq', (SELECT MAX(id) FROM options));
SELECT setval('publish_jobs_id_seq', (SELECT MAX(id) FROM publish_jobs));
```

#### Step 4: Update Production Configuration
Update `DATABASE_URL` and `EXPECTED_DATABASE_IDENTITY` in Render environment settings to match the newly restored database.

---

## 5. Security & Isolation Checklist

Before any database operation or deployment:
- [ ] Working branch is `develop` (for staging) or `main` (for production). Never mix branches.
- [ ] No database credentials, passwords, or tokens committed to git.
- [ ] Strict TLS active: `sslmode=verify-full`, `channel_binding=require`, and valid `sslrootcert`. Never downgrade to `sslmode=require`.
- [ ] Hostname in `DATABASE_URL` matches `EXPECTED_DATABASE_IDENTITY`.
- [ ] In-memory test suite passes 100% without accessing any remote database (`pytest -q`).
- [ ] PITR recovery drill executed to validate end-to-end RTO/RPO targets.

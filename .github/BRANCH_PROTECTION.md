# QuizBotPro — GitHub Branch Protection & Release Ruleset Specification

This document provides the canonical branch protection and release gate specification for the QuizBotPro repository (`Shivam3635/Quiz_Bot`).

---

## 1. Branch Strategy & Environments Overview

| Branch | Lifecycle Role | Target Environment | Deployment Mode | Quality Gates |
| :--- | :--- | :--- | :--- | :--- |
| `develop` | Ongoing integration & feature development | **Staging** (`quizbotpro-staging`) | Automated upon push & passing CI | Linting, Formatting, pip-audit, Pytest (358 tests) |
| `main` | Production releases | **Production** (`quizbotpro`) | Gated: PR merge → CI → Environment Manual Approval → Exact-SHA Deploy | All CI quality gates + Human Reviewer Sign-off |

---

## 2. Production Branch Ruleset (`refs/heads/main`)

Navigate to **GitHub Repository > Settings > Rules > Rulesets** (or **Branches > Add branch protection rule**).

### Ruleset Configuration
- **Ruleset Name**: `production-release-safeguards`
- **Enforcement Status**: `Active`
- **Target Branches**: `Include default branch` (`main`)

### Key Controls
1. **Restrict Deletions**:
   - `Prevent branch deletion`: **ENABLED**
2. **Block Force Pushes**:
   - `Block force pushes`: **ENABLED** (prevents history rewriting and lost audit trails).
3. **Require a Pull Request Before Merging**:
   - `Require pull request before merging`: **ENABLED**
   - `Required approvals`: **1**
   - `Dismiss stale pull request approvals when new commits are pushed`: **ENABLED** (invalidates approvals if PR branch changes).
   - `Require review from Code Owners`: Recommended.
4. **Require Status Checks to Pass Before Merging**:
   - `Require status checks to pass`: **ENABLED**
   - `Require branches to be up to date before merging`: **ENABLED** (guarantees tests ran against latest base).
   - **Required Status Checks**:
     - `Continuous Integration & Quality Gates` (Job from `.github/workflows/test-staging.yml`)
5. **Require Linear History**:
   - `Require linear history`: **ENABLED** (Squash or rebase merging preferred).
6. **Bypass List**:
   - Restrict bypass permissions to Organization Owners or emergency break-glass roles only.

---

## 3. Development Branch Ruleset (`refs/heads/develop`)

### Ruleset Configuration
- **Ruleset Name**: `develop-integration-safeguards`
- **Enforcement Status**: `Active`
- **Target Branches**: `Include: develop`

### Key Controls
1. **Restrict Deletions**:
   - `Prevent branch deletion`: **ENABLED**
2. **Block Force Pushes**:
   - `Block force pushes`: **ENABLED**
3. **Require Status Checks to Pass Before Merging** (for PRs into develop):
   - `Require status checks to pass`: **ENABLED**
   - **Required Status Checks**:
     - `Continuous Integration & Quality Gates`

---

## 4. Protected GitHub Environment (`production`)

Navigate to **GitHub Repository > Settings > Environments > New environment**:

- **Environment Name**: `production`
- **Configure Deployment Protection Rules**:
  1. **Required Reviewers**:
     - Check `Required reviewers`.
     - Add repository maintainer / release manager.
     - *Effect*: When `deploy-production.yml` runs, GitHub Actions pauses at the `deploy-production` job until a reviewer explicitly approves the deployment.
  2. **Deployment Branches**:
     - Select `Selected branches`.
     - Add rule: `main` (strictly blocks any non-main deployment to production).
- **Environment Secrets**:
  - Store `RENDER_PRODUCTION_DEPLOY_HOOK` in this environment's secrets so it is inaccessible to all other branches and jobs.
- **Environment Variables**:
  - `PRODUCTION_HEALTH_URL`: e.g. `https://quizbotpro.onrender.com/health`
  - `PRODUCTION_SERVICE_URL`: e.g. `https://quizbotpro.onrender.com`

---

## 5. End-to-End Pull Request & Release Workflow

```text
[ Feature Branch ]
       │
       ▼ (Pull Request)
   [ develop ]  ────────► Automatic CI passes ──► Staging auto-deploys & verifies
       │
       ▼ (Release PR: develop -> main)
 [ PR to main ] ────────► Pre-merge CI runs against main
       │
       ▼ (Peer Review & Approval)
   Merged to main
       │
       ▼
 [ deploy-production.yml ]
       │
       ├──► 1. production-ci (Full 358-test suite re-runs on merged SHA)
       │
       └──► 2. deploy-production (Pauses at environment: production)
                   │
                   ▼ (Human Operator Reviews & Approves in GitHub Actions UI)
             Trigger Render Production Deploy Hook (ref=<EXACT_SHA>)
                   │
                   ▼
             Verify Production /health endpoint (exact SHA & healthy status)
                   │
                   ▼
             Release Complete (Fail-closed if any check fails)
```


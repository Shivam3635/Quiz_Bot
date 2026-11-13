#!/usr/bin/env python3
"""QuizBotPro Deployment & Application Readiness Verification Script.

Polls the service readiness or health endpoint to verify that a deployed service is:
1. Responding with HTTP 200.
2. Reporting explicit application readiness (ready: true or status: ready/healthy).
3. Reporting the exact target Git commit SHA.
4. Running in the expected environment (staging vs production).

Usage:
    python .github/verify_deployment.py <TARGET_SHA> [HEALTH_URL] [TIMEOUT_SECONDS] [POLL_INTERVAL] [EXPECTED_ENV]
"""

import json
import os
import sys
import time
import urllib.error
import urllib.request


def verify_deployment(
    target_sha: str,
    health_url: str = "https://quizbotpro-staging.onrender.com/ready",
    timeout_seconds: int = 300,
    poll_interval: int = 10,
    expected_env: str | None = None,
) -> bool:
    """Poll the target readiness endpoint until the deployed service is ready, matched, and healthy.

    Args:
        target_sha: Expected Git commit SHA (40-char or abbreviated).
        health_url: Target URL for the JSON readiness/health check.
        timeout_seconds: Maximum time in seconds to wait before failing.
        poll_interval: Interval in seconds between polls.
        expected_env: Expected environment name ('staging', 'production', etc.).

    Returns:
        True if verification succeeded, False if timed out or permanent mismatch.
    """
    start_time = time.time()
    clean_target = target_sha.strip()

    print("=" * 70)
    print("QUIZBOTPRO DEPLOYMENT & READINESS VERIFICATION")
    print("=" * 70)
    print(f"Target SHA          : {clean_target}")
    print(f"Readiness/Health URL: {health_url}")
    print(f"Expected Environment: {expected_env or 'any'}")
    print(f"Timeout (sec)       : {timeout_seconds}s")
    print(f"Poll Interval       : {poll_interval}s")
    print("-" * 70)

    while True:
        elapsed = int(time.time() - start_time)
        if elapsed > timeout_seconds:
            print(f"\n[FAIL] Deployment verification timed out after {elapsed}s.")
            print(f"Target service did NOT achieve readiness for target commit '{clean_target}'.")
            return False

        try:
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "QuizBotPro-CI-Deployment-Verifier/2.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.status
                body = response.read().decode("utf-8")

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    print(
                        f"[{elapsed:3d}s] HTTP {status_code} - Non-JSON response received. Waiting..."
                    )
                    time.sleep(poll_interval)
                    continue

                live_status = payload.get("status")
                live_ready = payload.get("ready")
                live_commit = str(payload.get("commit", "")).strip()
                live_env = str(payload.get("environment", "unknown")).strip()

                print(
                    f"[{elapsed:3d}s] HTTP {status_code} | Service: {live_status} | "
                    f"Ready: {live_ready} | Env: {live_env} | Live Commit: {live_commit}"
                )

                # 1. Readiness status validation
                is_ready = (live_ready is True) or (live_status in ("ready", "healthy"))
                if not is_ready:
                    print(
                        f"[{elapsed:3d}s] Service responded HTTP {status_code} but reports unready "
                        f"({live_status}). Retrying..."
                    )
                    time.sleep(poll_interval)
                    continue

                # 2. Environment isolation validation
                if expected_env and expected_env.strip():
                    clean_expected = expected_env.strip().lower()
                    if live_env.lower() != clean_expected:
                        print(
                            f"\n[FAIL] Environment mismatch! Expected '{expected_env}', but live "
                            f"service reported '{live_env}'."
                        )
                        print("Permanent mismatch: aborting deployment verification.")
                        return False

                # 3. Match full SHA or prefix (in case hosting environment truncates commit)
                sha_matches = (
                    live_commit == clean_target
                    or (len(clean_target) >= 7 and live_commit.startswith(clean_target[:7]))
                    or (len(live_commit) >= 7 and clean_target.startswith(live_commit[:7]))
                )

                if not sha_matches:
                    print(
                        f"[{elapsed:3d}s] Commit mismatch: live commit '{live_commit}' != target "
                        f"'{clean_target}'. Waiting for deploy to complete..."
                    )
                    time.sleep(poll_interval)
                    continue

                # All gates passed
                print("-" * 70)
                print(f"[SUCCESS] Service verified ready and healthy on commit: {live_commit}")
                print(f"[SUCCESS] Environment verified: {live_env}")
                print(f"[SUCCESS] Verification completed in {elapsed}s.")
                print("=" * 70)
                return True

        except urllib.error.HTTPError as exc:
            if exc.code == 503:
                reason = "initializing"
                try:
                    err_body = exc.read().decode("utf-8")
                    err_payload = json.loads(err_body)
                    reason = err_payload.get("reason", "initializing")
                except (json.JSONDecodeError, UnicodeDecodeError, AttributeError):
                    pass
                print(
                    f"[{elapsed:3d}s] HTTP 503 (Not Ready) - Application status: {reason}. Retrying..."
                )
            else:
                print(
                    f"[{elapsed:3d}s] HTTP {exc.code} ({exc.reason}) - Server restarting or starting up..."
                )
        except urllib.error.URLError as exc:
            print(
                f"[{elapsed:3d}s] Connection failed ({exc.reason}) - Server restarting or starting up..."
            )
        except Exception as exc:  # noqa: BLE001
            print(f"[{elapsed:3d}s] Transient error: {exc}")

        time.sleep(poll_interval)


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print(
            "Usage: python .github/verify_deployment.py <TARGET_SHA> [HEALTH_URL] [TIMEOUT_SECONDS] [POLL_INTERVAL] [EXPECTED_ENV]"
        )
        sys.exit(2)

    target_sha = sys.argv[1].strip()
    health_url = (
        sys.argv[2].strip()
        if len(sys.argv) > 2 and sys.argv[2].strip()
        else "https://quizbotpro-staging.onrender.com/ready"
    )
    timeout_seconds = int(sys.argv[3]) if len(sys.argv) > 3 and sys.argv[3].isdigit() else 300
    poll_interval = int(sys.argv[4]) if len(sys.argv) > 4 and sys.argv[4].isdigit() else 10
    expected_env = (
        sys.argv[5].strip()
        if len(sys.argv) > 5 and sys.argv[5].strip()
        else os.environ.get("EXPECTED_ENVIRONMENT")
    )

    success = verify_deployment(
        target_sha=target_sha,
        health_url=health_url,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
        expected_env=expected_env,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()

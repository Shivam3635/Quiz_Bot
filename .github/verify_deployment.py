#!/usr/bin/env python3
"""QuizBotPro Deployment Verification Script.

Polls the staging health endpoint to verify that a deployed service is healthy
and reporting the exact expected Git commit SHA.

Usage:
    python .github/verify_deployment.py <TARGET_SHA> [HEALTH_URL] [TIMEOUT_SECONDS] [POLL_INTERVAL]
"""

import json
import sys
import time
import urllib.error
import urllib.request


def verify_deployment(
    target_sha: str,
    health_url: str = "https://quizbotpro-staging.onrender.com/health",
    timeout_seconds: int = 300,
    poll_interval: int = 10,
) -> bool:
    """Poll the target health endpoint until the deployed commit matches target_sha.

    Args:
        target_sha: Expected Git commit SHA (40-char or abbreviated).
        health_url: Target URL for the JSON health/version check.
        timeout_seconds: Maximum time in seconds to wait before failing.
        poll_interval: Interval in seconds between health checks.

    Returns:
        True if verification succeeded, False if timed out.
    """
    start_time = time.time()
    clean_target = target_sha.strip()

    print("=" * 70)
    print("QUIZBOTPRO STAGING DEPLOYMENT VERIFICATION")
    print("=" * 70)
    print(f"Target SHA      : {clean_target}")
    print(f"Health Endpoint : {health_url}")
    print(f"Timeout (sec)   : {timeout_seconds}s")
    print(f"Poll Interval   : {poll_interval}s")
    print("-" * 70)

    while True:
        elapsed = int(time.time() - start_time)
        if elapsed > timeout_seconds:
            print(f"\n[FAIL] Deployment verification timed out after {elapsed}s.")
            print(f"Target service did NOT report expected commit '{clean_target}'.")
            return False

        try:
            req = urllib.request.Request(
                health_url,
                headers={"User-Agent": "QuizBotPro-CI-Deployment-Verifier/1.0"},
            )
            with urllib.request.urlopen(req, timeout=10) as response:
                status_code = response.status
                body = response.read().decode("utf-8")

                try:
                    payload = json.loads(body)
                except json.JSONDecodeError:
                    print(f"[{elapsed:3d}s] HTTP {status_code} - Non-JSON response received. Waiting...")
                    time.sleep(poll_interval)
                    continue

                live_status = payload.get("status")
                live_commit = str(payload.get("commit", "")).strip()
                live_env = payload.get("environment", "unknown")

                print(
                    f"[{elapsed:3d}s] HTTP {status_code} | Service: {live_status} | "
                    f"Env: {live_env} | Live Commit: {live_commit}"
                )

                # Match full SHA or prefix (in case hosting environment truncates commit)
                sha_matches = (
                    live_commit == clean_target
                    or (len(clean_target) >= 7 and live_commit.startswith(clean_target[:7]))
                    or (len(live_commit) >= 7 and clean_target.startswith(live_commit[:7]))
                )

                if status_code == 200 and live_status == "healthy" and sha_matches:
                    print("-" * 70)
                    print(f"[SUCCESS] Staging service verified healthy on commit: {live_commit}")
                    print(f"[SUCCESS] Verification completed in {elapsed}s.")
                    print("=" * 70)
                    return True

        except urllib.error.HTTPError as exc:
            print(f"[{elapsed:3d}s] HTTP {exc.code} ({exc.reason}) - Server restarting or starting up...")
        except urllib.error.URLError as exc:
            print(f"[{elapsed:3d}s] Connection failed ({exc.reason}) - Server restarting or starting up...")
        except Exception as exc:  # noqa: BLE001
            print(f"[{elapsed:3d}s] Transient error: {exc}")

        time.sleep(poll_interval)


def main() -> None:
    if len(sys.argv) < 2 or not sys.argv[1].strip():
        print("Usage: python .github/verify_deployment.py <TARGET_SHA> [HEALTH_URL] [TIMEOUT_SECONDS] [POLL_INTERVAL]")
        sys.exit(2)

    target_sha = sys.argv[1].strip()
    health_url = sys.argv[2] if len(sys.argv) > 2 else "https://quizbotpro-staging.onrender.com/health"
    timeout_seconds = int(sys.argv[3]) if len(sys.argv) > 3 else 300
    poll_interval = int(sys.argv[4]) if len(sys.argv) > 4 else 10

    success = verify_deployment(
        target_sha=target_sha,
        health_url=health_url,
        timeout_seconds=timeout_seconds,
        poll_interval=poll_interval,
    )

    if not success:
        sys.exit(1)


if __name__ == "__main__":
    main()


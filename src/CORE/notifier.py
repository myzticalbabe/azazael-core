# coding: utf-8
"""CORE/notifier.py — Alert notification system for Azazael Observability.

Sends Slack webhook notifications when:
    - Health check status degrades (overall becomes "down" or "degraded")
    - Drift alerts fire (critical severity)
    - Clock trust degrades (a peer skews past the threshold, or its stamps are
      refused for being outside the drift bound — audit §5 item 5)
    - Circuit breakers trip or accumulate dead letters

Usage:
    # Send a one-shot alert
    python -m CORE.notifier --check

    # Start watcher that periodically checks and alerts
    python -m CORE.notifier --watch --interval 60

    # Start watcher with Prometheus endpoint + web dashboard
    python -m CORE.notifier --watch --web-dashboard --prometheus

Configuration via environment variables:
    SLACK_WEBHOOK_URL=https://hooks.slack.com/services/...
    AZAZAEL_NOTIFIER_INTERVAL=60  (default check interval in seconds)
"""

# NOTE (2026-09-15): this assignment used to sit *inside* the module docstring
# above, which made it dead text — the module had no `__all__` at all, so
# `from CORE.notifier import *` exported nothing and `notifier.__all__` raised
# AttributeError. Found by a test that asserted `check_clock_and_alert` was on
# the notifier's public surface; the docstring was already opening before it.
__all__ = [
    "check_breakers_and_alert",
    "check_clock_and_alert",
    "check_drift_and_alert",
    "check_health_and_alert",
    "get_webhook_url",
    "main",
    "run_check",
    "send_slack_alert",
    "start_watcher",
]

import json
import os
import time
import argparse
import urllib.request
import urllib.error
from typing import Any, List
from datetime import datetime

#: Swallows recorded when the failure ledger is unavailable (it is a stdlib-only
#: leaf, so this fallback should never fire -- if it does, the ledger is missing).

try:
    from CORE.failure_ledger import note_failure
except ImportError:                               # never break the host
    def note_failure(where: str, exc: BaseException | None = None) -> None:
        pass  # ledger missing; evidence-vs-silence tradeoff capped here


def get_webhook_url() -> (str | None):
    """Get Slack webhook URL from environment variable."""
    return os.environ.get("SLACK_WEBHOOK_URL") or os.environ.get("AZAZAEL_SLACK_WEBHOOK")


def send_slack_alert(
    message: str,
    webhook_url: (str | None) = None,
    title: str = "Azazael Alert",
) -> bool:
    """Send an alert to Slack via webhook.

    Args:
        message: The alert message body
        webhook_url: Slack webhook URL. Falls back to SLACK_WEBHOOK_URL env var.
        title: The alert title/fallback text

    Returns:
        True if sent successfully, False otherwise
    """
    url = webhook_url or get_webhook_url()
    if not url:
        print("  [NOTIFIER] No Slack webhook URL configured. set SLACK_WEBHOOK_URL env var.")
        return False

    payload = {
        "blocks": [
            {
                "type": "header",
                "text": {"type": "plain_text", "text": title},
            },
            {
                "type": "section",
                "text": {"type": "mrkdwn", "text": message},
            },
            {
                "type": "context",
                "elements": [
                    {
                        "type": "mrkdwn",
                        "text": f"Azazael Notifier | {datetime.now().strftime('%Y-%m-%d %H:%M:%S UTC')}",
                    }
                ],
            },
        ],
    }

    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(
        url,
        data=data,
        headers={"Content-Type": "application/json"},
        method="POST",
    )

    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            if resp.status == 200:
                print(f"  [NOTIFIER] Alert sent to Slack: {title}")
                return True
            else:
                print(f"  [NOTIFIER] Slack returned HTTP {resp.status}: {resp.read().decode()}")
                return False
    except urllib.error.HTTPError as e:
        print(f"  [NOTIFIER] Slack HTTP error: {e.code} {e.reason}")
        return False
    except urllib.error.URLError as e:
        print(f"  [NOTIFIER] Slack connection error: {e.reason}")
        return False
    except Exception as e:
        print(f"  [NOTIFIER] Slack send error: {e}")
        return False


def check_health_and_alert(
    webhook_url: (str | None) = None,
    last_state: (dict[str, Any] | None) = None,
) -> (dict[str, Any] | None):
    """Check health and send alerts if status has changed or is critical.

    Args:
        webhook_url: Optional Slack webhook URL
        last_state: Previous check state for change detection

    Returns:
        Current state dict for next comparison, or None on error
    """
    try:
        from CORE.metrics import health_check
        report = health_check.report()
    except Exception as e:
        print(f"  [NOTIFIER] Health check failed: {e}")
        return last_state

    overall = report.get("overall", "unknown")
    state = {
        "overall": overall,
        "down_components": [
            name for name, c in report.get("components", {}).items()
            if c.get("status") == "down"
        ],
        "degraded_components": [
            name for name, c in report.get("components", {}).items()
            if c.get("status") == "degraded"
        ],
    }

    previous_overall = (last_state or {}).get("overall", "ok")

    # Alert on degradation to down
    if overall == "down":
        comps = ", ".join(state["down_components"]) or "unknown"
        msg = f":red_circle: *Health Status is DOWN*\n>Affected components: {comps}"
        if previous_overall != "down":
            send_slack_alert(msg, webhook_url, title="Health Degraded to DOWN")
        else:
            send_slack_alert(msg, webhook_url, title="Health Still DOWN (repeating)")

    # Alert on degradation to degraded
    elif overall == "degraded":
        comps = ", ".join(state["degraded_components"]) or "unknown"
        msg = f":warning: *Health Status is DEGRADED*\n>Affected components: {comps}"
        if previous_overall != "degraded":
            send_slack_alert(msg, webhook_url, title="Health Degraded to DEGRADED")
        else:
            send_slack_alert(msg, webhook_url, title="Health Still DEGRADED (repeating)")

    # Alert on recovery
    elif overall == "ok" and previous_overall in ("down", "degraded"):
        msg = ":white_check_mark: *Health Restored to OK*"
        send_slack_alert(msg, webhook_url, title="Health Restored")
    else:
        print(
            f"  [NOTIFIER] Health OK ({report.get('counts', {}).get('ok', 0)}/"
            f"{len(report.get('components', {}))} components)"
        )

    return state


def check_drift_and_alert(webhook_url: (str | None) = None) -> int:
    """Check drift alerts and send notifications for critical issues.

    Args:
        webhook_url: Optional Slack webhook URL

    Returns:
        Number of critical drift issues found
    """
    try:
        from CORE.metrics import drift_alert
        issues = drift_alert.check()
    except Exception as e:
        print(f"  [NOTIFIER] Drift check failed: {e}")
        return 0

    critical = [i for i in issues if i.severity == "critical"]
    warnings = [i for i in issues if i.severity == "warning"]

    if critical:
        msg_lines = [f":fire: *{len(critical)} Critical Drift Issues*"]
        for c in critical[:5]:
            msg_lines.append(f"> * {c.message[:120]}")
        if len(critical) > 5:
            msg_lines.append(f"> ... and {len(critical) - 5} more")
        send_slack_alert("\n".join(msg_lines), webhook_url, title="Drift Alert")

    if warnings:
        msg_lines = [f":warning: *{len(warnings)} Warning Drift Issues*"]
        for w in warnings[:3]:
            msg_lines.append(f"> * {w.message[:120]}")
        if len(warnings) > 3:
            msg_lines.append(f"> ... and {len(warnings) - 3} more")
        send_slack_alert("\n".join(msg_lines), webhook_url, title="Drift Warning")

    print(f"  [NOTIFIER] Drift: {len(critical)} critical, {len(warnings)} warnings, "
          f"{len(issues) - len(critical) - len(warnings)} info")
    return len(critical)


def check_breakers_and_alert(webhook_url: (str | None) = None) -> int:
    """Check circuit breaker dead letters and send alerts.

    Args:
        webhook_url: Optional Slack webhook URL

    Returns:
        Number of dead letters found
    """
    try:
        from CORE.circuit_breaker import breaker_registry
        summary = breaker_registry.get_dead_letter_summary()
    except Exception as e:
        print(f"  [NOTIFIER] Breaker check failed: {e}")
        return 0

    total = summary.get("total_dead_letters", 0)

    if total > 0:
        per_breaker = summary.get("per_breaker", {})
        top = sorted(per_breaker.items(), key=lambda x: -x[1])[:5]
        msg_lines = [f":envelope: *{total} Dead Letters in Queue*"]
        for name, count in top:
            msg_lines.append(f"> * {name}: {count}")
        if summary.get("recent_errors"):
            err = summary["recent_errors"][0]
            msg_lines.append(f"> Latest: {err.get('error', '')[:80]}")
        send_slack_alert("\n".join(msg_lines), webhook_url, title="Dead Letter Alert")

    print(f"  [NOTIFIER] Dead letters: {total}")
    return total


def check_clock_and_alert(webhook_url: (str | None) = None) -> int:
    """Check hybrid-logical-clock health and send notifications.

    Closes audit §5 item 5: clock trust became *measurable* on 2026-09-15
    (``get_clock_skew_ms()``, ``rejected()``) and the monitor renders it, but
    nothing alerted on it — so a peer with a broken clock was visible only to
    somebody already looking at the dashboard.

    Two distinct signals, alerted separately because they mean different
    things:

    * **Real skew** (``skew_ms``) — an accepted peer's clock runs ahead of
      ours. Tolerable, and expected: a few seconds is network latency. Alert
      above ``AZAZAEL_CLOCK_SKEW_ALERT_MS`` (default 60s).
    * **Refused stamps** (``rejected`` / ``rejected_skew_ms``) — a peer is
      *outside* the drift bound, so its stamps are not adopted for ordering at
      all. One is a curiosity (a legacy store being loaded); a continuing rate
      means a clock is wrong and cross-env ordering against that peer is
      effectively disabled.

    Env knobs:
        AZAZAEL_CLOCK_SKEW_ALERT_MS=60000     accepted-skew threshold
        AZAZAEL_CLOCK_REJECT_ALERT=1          refused count that triggers

    Returns:
        Number of critical clock issues found (0 = healthy).
    """
    try:
        from CORE.hlc import hlc
        clock = hlc()
        skew_ms = int(clock.skew_ms())
        refused = int(clock.rejected())
        refused_skew_ms = int(clock.rejected_skew_ms())
        observed = int(clock.observations())
        node = clock.node
    except Exception as e:
        print(f"  [NOTIFIER] Clock check failed: {e}")
        return 0

    try:
        skew_limit = int(os.environ.get("AZAZAEL_CLOCK_SKEW_ALERT_MS", "60000"))
        refuse_limit = int(os.environ.get("AZAZAEL_CLOCK_REJECT_ALERT", "1"))
        auto_purge_at = int(os.environ.get("AZAZAEL_CLOCK_AUTO_PURGE_REFUSED",
                                           "25"))
    except ValueError as exc:
        note_failure("notifier:L308", exc)
        skew_limit, refuse_limit = 60000, 1
        auto_purge_at = 25

    critical = 0
    # A refused stamp is the harder failure: the peer's stamps are being
    # discarded, so any value ordered by them loses deterministically.
    if refused >= refuse_limit:
        critical += 1
        mins = refused_skew_ms / 60000.0
        # Auto-remedy: refused stamps already lose ordering (they cannot win
        # a merge or propagate), so purging reclaims gossip/store space
        # without changing any ordering outcome. Gated well above the alert
        # threshold: a lone refused stamp is a curiosity (legacy load), a
        # pile is a wrong clock. 0 disables. Never raises.
        purged = None
        if auto_purge_at > 0 and refused >= auto_purge_at:
            try:
                from CORE import spine_registry as _sr
                purged = _sr.purge_implausible_stamps(dry_run=False).get(
                    "repaired", 0)
            except Exception as exc:
                note_failure("notifier:clock-auto-purge", exc)
        send_slack_alert(
            f":clock1: *{refused} peer stamp(s) refused* on `{node}`\n"
            f"> Worst offset: {refused_skew_ms:,}ms (~{mins:,.0f} min beyond the "
            f"bound)\n"
            f"> Refused stamps are NOT adopted for ordering, so they cannot win "
            f"a merge or propagate — but a peer outside the bound cannot be "
            f"ordered against at all.\n"
            f"> Repair a store that already holds them: "
            f"`spine_registry.purge_implausible_stamps(dry_run=False)`"
            + (f"\n> Auto-purge repaired {purged} stamp(s) "
               f"(AZAZAEL_CLOCK_AUTO_PURGE_REFUSED={auto_purge_at})."
               if purged is not None else ""),
            webhook_url, title="Clock Trust Alert",
        )

    if abs(skew_ms) > skew_limit:
        critical += 1
        direction = "ahead of" if skew_ms > 0 else "behind"
        send_slack_alert(
            f":hourglass: *Clock skew {skew_ms:+,}ms* on `{node}`\n"
            f"> Newest accepted peer clock is {abs(skew_ms):,}ms {direction} ours "
            f"(limit {skew_limit:,}ms, {observed} stamp(s) observed)\n"
            f"> Ordering is unaffected — the HLC is logical, not wall-clock — but "
            f"this is the number that predicts divergence.",
            webhook_url, title="Clock Skew Warning",
        )

    print(f"  [NOTIFIER] Clock: skew {skew_ms:+,}ms, {refused} refused "
          f"(worst {refused_skew_ms:,}ms), {observed} observed")
    return critical


def run_check(
    webhook_url: (str | None) = None,
    last_state: (dict[str, Any] | None) = None,
) -> (dict[str, Any] | None):
    """Run a single check cycle: health, drift, breakers, clock.

    Args:
        webhook_url: Optional Slack webhook URL
        last_state: Previous health state for change detection

    Returns:
        Updated health state
    """
    print(f"\n  [NOTIFIER] Check cycle at {datetime.now().strftime('%H:%M:%S')}")
    state = check_health_and_alert(webhook_url, last_state)
    check_drift_and_alert(webhook_url)
    check_breakers_and_alert(webhook_url)
    check_clock_and_alert(webhook_url)
    return state


def start_watcher(
    webhook_url: (str | None) = None,
    interval: int = 60,
    web_dashboard: bool = False,
    prometheus: bool = False,
):
    """Start the periodic notifier watcher.

    Args:
        webhook_url: Optional Slack webhook URL
        interval: Check interval in seconds
        web_dashboard: Also start web dashboard
        prometheus: Also start Prometheus endpoint
    """
    print(f"  [NOTIFIER] Starting watcher (interval={interval}s)")
    if webhook_url:
        print("  [NOTIFIER] Slack webhook configured")
    else:
        print("  [NOTIFIER] No Slack webhook — alerts printed to console only")

    # Start web dashboard if requested
    dashboard_server = None
    dashboard_thread = None
    if web_dashboard:
        try:
            from CORE.web_dashboard import WebDashboardServer
            dashboard_server = WebDashboardServer(port=18422, watch=True, interval=interval)
            dashboard_thread = dashboard_server.start_background()
            time.sleep(0.3)
        except Exception as e:
            print(f"  [WARN] Could not start web dashboard: {e}")

    # Start Prometheus endpoint if requested
    prometheus_server = None
    prometheus_thread = None
    if prometheus:
        try:
            from CORE.health_server import PrometheusServer
            prometheus_server = PrometheusServer(port=18421)
            prometheus_thread = prometheus_server.start_background()
            time.sleep(0.3)
            print("  [NOTIFIER] Prometheus endpoint on port 18421")
        except Exception as e:
            print(f"  [WARN] Could not start Prometheus server: {e}")

    state = None
    try:
        while True:
            state = run_check(webhook_url, state)
            time.sleep(interval)
    except KeyboardInterrupt:
        print("\n  [NOTIFIER] Stopping...")
        if dashboard_server:
            dashboard_server.stop()
        if prometheus_server:
            prometheus_server.stop()


def main():
    parser = argparse.ArgumentParser(
        description="Azazael Notifier — health alert system with Slack integration",
    )
    parser.add_argument("--check", action="store_true", help="One-shot check and alert")
    parser.add_argument("--watch", action="store_true", help="Periodic watcher mode")
    parser.add_argument("--interval", type=int, default=60, help="Check interval (seconds)")
    parser.add_argument(
        "--webhook", default=None,
        help="Slack webhook URL (or set SLACK_WEBHOOK_URL env var)",
    )
    parser.add_argument(
        "--web-dashboard", action="store_true",
        help="Also start web dashboard (port 18422)",
    )
    parser.add_argument(
        "--prometheus", action="store_true",
        help="Also start Prometheus endpoint (port 18421)",
    )

    args = parser.parse_args()

    webhook_url = args.webhook or get_webhook_url()
    if not webhook_url:
        print("  [NOTIFIER] No Slack webhook URL. Alerts will be logged to console only.")
        print("  set SLACK_WEBHOOK_URL environment variable to enable Slack alerts.")

    if args.watch:
        start_watcher(
            webhook_url=webhook_url,
            interval=args.interval,
            web_dashboard=args.web_dashboard,
            prometheus=args.prometheus,
        )
    else:
        run_check(webhook_url)


if __name__ == "__main__":
    main()

"""Seed the Knowledge Graph with 10 production-grade symptom→cause→resolution
triplets, mirroring the runbooks and KB articles loaded by
`seed_runbooks_kb_extended.sql`.

Run after applying migration 004 and seeding runbooks/KB:

    cd backend
    python scripts/seed_knowledge_graph.py

Idempotent — re-running just strengthens the existing nodes. Each triplet
is sent through KnowledgeGraphService.teach_triplet, which is the same
dedup-aware path used by the /kg/teach REST endpoint.

The point of this script is to give the user a graph that's *immediately
useful* — the Resolution agent will start auto-fixing matching tickets
before any human has resolved one through the UI.
"""
from __future__ import annotations

import sys
from pathlib import Path

# Allow running from anywhere — find the backend root.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.services.knowledge_graph_service import KnowledgeGraphService  # noqa: E402


TRIPLETS = [
    {
        "symptom_label": "SAP application appears to be down — users cannot reach the GUI",
        "symptom_description": (
            "Multiple users in finance/HR report 'cannot connect to SAP', 'SAP not "
            "responding', or 'application down'. Server is pingable; SAP GUI hangs at "
            "logon screen or times out."
        ),
        "cause_label": "SAP central instance work processes are stopped or in error state",
        "resolution_label": "Restart the SAP instance via sapcontrol and verify all processes GREEN",
        "resolution_steps": [
            {"order": 1, "title": "SSH to SAP host as <sid>adm"},
            {"order": 2, "title": "sapcontrol GetProcessList — capture state"},
            {"order": 3, "title": "sapcontrol RestartInstance if disp+work or icman not GREEN"},
            {"order": 4, "title": "Wait 90s, re-check process list"},
            {"order": 5, "title": "Hit /sap/public/ping — assert HTTP 200"},
        ],
        "category": "Application",
        "keywords": ["sap", "application", "down", "sapcontrol", "instance", "disp", "icman"],
    },
    {
        "symptom_label": "Spreadsheet or database table is stuck in READ ONLY mode",
        "symptom_description": (
            "User reports 'cannot save', 'spreadsheet is read only', 'database returned "
            "read-only mode'. Other users on different rows of the same table work fine."
        ),
        "cause_label": "Abandoned blocking session holding a lock on the table",
        "resolution_label": "Identify head blocker via sp_who2, capture context, kill if idle",
        "resolution_steps": [
            {"order": 1, "title": "Connect to DB with DBA credentials"},
            {"order": 2, "title": "Run sp_who2 active — find blocking chain"},
            {"order": 3, "title": "Capture last_command + program_name for the head blocker"},
            {"order": 4, "title": "KILL the blocker if idle >10 min and abandoned"},
            {"order": 5, "title": "Verify chain cleared; user retries"},
        ],
        "category": "Database",
        "keywords": ["read", "only", "spreadsheet", "lock", "blocking", "session", "database"],
    },
    {
        "symptom_label": "Cannot connect to corporate mail server / Outlook disconnected",
        "symptom_description": (
            "User reports 'cannot reach mail server', 'Outlook offline', 'mail not working'. "
            "Other applications on the same machine work."
        ),
        "cause_label": "Mail server unreachable due to DNS drift or LB unhealthy",
        "resolution_label": "Diagnose connectivity (ping/DNS/telnet) and remediate by branch",
        "resolution_steps": [
            {"order": 1, "title": "Ping FQDN; record latency and loss"},
            {"order": 2, "title": "nslookup against internal DNS and 8.8.8.8 — detect drift"},
            {"order": 3, "title": "Telnet 25 and 587, capture SMTP banner"},
            {"order": 4, "title": "Check LB health endpoint"},
            {"order": 5, "title": "Page network team if LB unhealthy"},
        ],
        "category": "Infrastructure",
        "keywords": ["mail", "outlook", "server", "smtp", "exchange", "connectivity", "dns"],
    },
    {
        "symptom_label": "User locked out — cannot log in with correct password",
        "symptom_description": (
            "User reports 'locked out', 'password expired', 'cannot login to laptop'. "
            "User has tried the correct password multiple times."
        ),
        "cause_label": "Active Directory account is locked or password has expired",
        "resolution_label": "Unlock-ADAccount and reset password with change-at-next-logon",
        "resolution_steps": [
            {"order": 1, "title": "Get-ADUser to check LockedOut / PasswordExpired"},
            {"order": 2, "title": "Unlock-ADAccount if locked"},
            {"order": 3, "title": "Set-ADAccountPassword -Reset if expired"},
            {"order": 4, "title": "Force change at next logon"},
            {"order": 5, "title": "Notify user via alternate contact"},
        ],
        "category": "Identity",
        "keywords": ["locked", "password", "expired", "login", "active", "directory", "unlock"],
    },
    {
        "symptom_label": "Cannot connect to VPN — authentication failed",
        "symptom_description": (
            "User reports 'VPN authentication failed', 'RSA token not working', "
            "'GlobalProtect rejecting credentials'. Token shows correct rolling code."
        ),
        "cause_label": "MFA token out of sync or machine certificate expired",
        "resolution_label": "Resync token and refresh cert if expired",
        "resolution_steps": [
            {"order": 1, "title": "Verify account enabled in MFA system"},
            {"order": 2, "title": "Resync token (RSA: Resync Authenticator)"},
            {"order": 3, "title": "Pull last 3 VPN gateway attempts, get failure code"},
            {"order": 4, "title": "If cert-expired: delete + re-enroll machine cert"},
            {"order": 5, "title": "User retries; verify connect-success log"},
        ],
        "category": "Network",
        "keywords": ["vpn", "authentication", "token", "rsa", "duo", "certificate", "globalprotect"],
    },
    {
        "symptom_label": "Disk full alert / cannot save file to server",
        "symptom_description": (
            "Monitoring fires 'disk space >90%' or user reports 'cannot save', "
            "'no space left on device'. Affects a single volume."
        ),
        "cause_label": "Log files have accumulated without rotation, filling the volume",
        "resolution_label": "Rotate old logs, purge temp files, verify <80% utilisation",
        "resolution_steps": [
            {"order": 1, "title": "df -h to identify volumes >90%"},
            {"order": 2, "title": "du -sh /* to find top largest directories"},
            {"order": 3, "title": "Rotate + gzip logs older than 14 days"},
            {"order": 4, "title": "Purge IIS/nginx logs older than 30 days"},
            {"order": 5, "title": "Clear OS temp files >7 days"},
            {"order": 6, "title": "Re-run df -h; escalate to storage if still >80%"},
        ],
        "category": "Infrastructure",
        "keywords": ["disk", "full", "space", "cannot", "save", "volume", "cleanup", "logs"],
    },
    {
        "symptom_label": "Reports loading very slowly / ETL timing out",
        "symptom_description": (
            "Users report 'reports take 60+ seconds to load', 'ETL is timing out', "
            "'queries used to be fast'. No infrastructure changes recently."
        ),
        "cause_label": "Index fragmentation + stale statistics on hot tables",
        "resolution_label": "Rebuild fragmented indexes ONLINE and update statistics with full scan",
        "resolution_steps": [
            {"order": 1, "title": "Capture actual execution plan for the slow query"},
            {"order": 2, "title": "Check dm_db_index_usage_stats for unused/missing"},
            {"order": 3, "title": "ALTER INDEX REBUILD WITH (ONLINE = ON) for >30% fragmented"},
            {"order": 4, "title": "UPDATE STATISTICS WITH FULLSCAN on affected tables"},
            {"order": 5, "title": "Re-run query; capture before/after duration"},
        ],
        "category": "Performance",
        "keywords": ["slow", "query", "report", "etl", "timeout", "index", "performance", "fragmented"],
    },
    {
        "symptom_label": "Kubernetes pod stuck in CrashLoopBackOff",
        "symptom_description": (
            "Monitoring alert: 'pod restarting repeatedly', 'service unavailable'. "
            "kubectl shows STATUS=CrashLoopBackOff. Affects one deployment in one cluster."
        ),
        "cause_label": "Liveness probe failing due to OOMKill or dependency unreachable",
        "resolution_label": "Diagnose via describe + previous logs, branch by reason, scale memory or fix deps",
        "resolution_steps": [
            {"order": 1, "title": "kubectl describe pod — capture Events section"},
            {"order": 2, "title": "kubectl logs --previous --tail=200"},
            {"order": 3, "title": "Branch ImagePullBackOff: check imagePullSecrets"},
            {"order": 4, "title": "Branch OOMKilled: edit deployment, +50% memory limit"},
            {"order": 5, "title": "Branch Liveness failed: verify deps reachable from pod"},
            {"order": 6, "title": "Watch pod until Running for 5 consecutive minutes"},
        ],
        "category": "Infrastructure",
        "keywords": ["kubernetes", "pod", "crashloopbackoff", "oomkilled", "k8s", "liveness"],
    },
    {
        "symptom_label": "Azure Data Factory pipeline failed",
        "symptom_description": (
            "ADF pipeline alert: 'pipeline failed', 'activity error', triggered via "
            "ADF webhook. Need to identify failing activity and retry."
        ),
        "cause_label": "Linked-service credential expired / throttling / activity timeout",
        "resolution_label": "Branch by ADF error code; rerun from failed activity to preserve checkpoint",
        "resolution_steps": [
            {"order": 1, "title": "Open pipeline run in ADF Studio"},
            {"order": 2, "title": "Identify failed activity + capture error code"},
            {"order": 3, "title": "ErrorCode 2100: rotate linked-service credential from Key Vault"},
            {"order": 4, "title": "ErrorCode 2200: increase activity timeout"},
            {"order": 5, "title": "ErrorCode 6000: wait 5 min for throttle to clear"},
            {"order": 6, "title": "Click Rerun from failed activity"},
            {"order": 7, "title": "Monitor 10 min; escalate if same activity fails again"},
        ],
        "category": "Infrastructure",
        "keywords": ["adf", "azure", "data", "factory", "pipeline", "failed", "rerun"],
    },
    {
        "symptom_label": "Web application showing stale data / changes not reflecting",
        "symptom_description": (
            "User reports 'site showing old data', 'my profile change isn't visible', "
            "'cache is stale'. Refreshing the page doesn't help."
        ),
        "cause_label": "Stale entries in Redis/Memcached fronting the application",
        "resolution_label": "Targeted scan-and-delete of the stale key pattern, plus CDN invalidation if applicable",
        "resolution_steps": [
            {"order": 1, "title": "Reproduce in incognito — confirm server-side staleness"},
            {"order": 2, "title": "Connect to Redis/Memcached fronting the app"},
            {"order": 3, "title": "redis-cli --scan --pattern key:* | xargs DEL"},
            {"order": 4, "title": "If CDN: invalidate the affected URL path"},
            {"order": 5, "title": "User re-verifies in fresh browser session"},
        ],
        "category": "Application",
        "keywords": ["cache", "stale", "redis", "memcached", "cdn", "reflecting", "web"],
    },
]


def main() -> None:
    print(f"Seeding Knowledge Graph with {len(TRIPLETS)} triplets…")
    created_summary = {"symptom": 0, "cause": 0, "resolution": 0}
    reused_summary = {"symptom": 0, "cause": 0, "resolution": 0}

    for i, triplet in enumerate(TRIPLETS, 1):
        try:
            result = KnowledgeGraphService.teach_triplet(
                **triplet,
                taught_by_user_id="USR-ADMIN-001",
                initial_confidence=0.75,  # Higher prior since these are vetted
            )
        except Exception as e:  # noqa: BLE001
            print(f"  [{i:>2}] FAILED ({triplet['symptom_label'][:60]}): {e}")
            continue

        for node_type in ("symptom", "cause", "resolution"):
            if result.get(f"{node_type}_created"):
                created_summary[node_type] += 1
            else:
                reused_summary[node_type] += 1
        verb = "created" if result.get("symptom_created") else "reused"
        print(f"  [{i:>2}] {verb} symptom — {triplet['symptom_label'][:70]}")

    print()
    print("Summary:")
    print(f"  Symptoms   — created: {created_summary['symptom']:>2}, reused: {reused_summary['symptom']:>2}")
    print(f"  Causes     — created: {created_summary['cause']:>2}, reused: {reused_summary['cause']:>2}")
    print(f"  Resolutions— created: {created_summary['resolution']:>2}, reused: {reused_summary['resolution']:>2}")
    print()
    print("Done. The Resolution agent will now auto-apply these for matching incidents.")


if __name__ == "__main__":
    main()

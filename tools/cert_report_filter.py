#!/usr/bin/env python3
"""Filter cert_manager JSON output into actionable certificate/key issues."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any, Dict, List


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Filter cert_manager JSON report and show only relevant issues."
    )
    p.add_argument(
        "--input",
        default="cert_report.json",
        help="Input JSON report produced by tools/cert_manager.py",
    )
    p.add_argument(
        "--expiry-days",
        type=int,
        default=30,
        help="Warn if certificate expires in <= N days (default: 30)",
    )
    p.add_argument(
        "--flag-unencrypted-keys",
        action="store_true",
        help="Flag unencrypted private keys as warnings (disabled by default)",
    )
    p.add_argument(
        "--allow-underscore-identifiers",
        action="store_true",
        help="Allow underscore in Subject CN and SAN DNS names (disabled by default)",
    )
    p.add_argument(
        "--json",
        action="store_true",
        help="Print filtered issues as JSON",
    )
    p.add_argument(
        "--output",
        default=None,
        help="Optional output file for filtered issues (text or JSON).",
    )
    p.add_argument(
        "--min-severity",
        choices=["info", "warning", "error"],
        default="warning",
        help="Minimum severity to include (default: warning)",
    )
    return p.parse_args()


def severity_rank(level: str) -> int:
    order = {"info": 0, "warning": 1, "error": 2}
    return order.get(level, 1)


def add_issue(
    issues: List[Dict[str, Any]],
    severity: str,
    file_path: str,
    category: str,
    message: str,
    details: Dict[str, Any] | None = None,
) -> None:
    payload: Dict[str, Any] = {
        "severity": severity,
        "file": file_path,
        "category": category,
        "message": message,
    }
    if details:
        payload["details"] = details
    issues.append(payload)


def collect_issues(
    report: List[Dict[str, Any]],
    expiry_days: int,
    flag_unencrypted_keys: bool,
    allow_underscore_identifiers: bool,
) -> List[Dict[str, Any]]:
    issues: List[Dict[str, Any]] = []

    for item in report:
        file_path = str(item.get("file", "<unknown>"))
        errors = item.get("errors") or []
        certs = item.get("certificates") or []
        keys = item.get("private_keys") or []

        for err in errors:
            add_issue(
                issues,
                "error",
                file_path,
                "parse",
                f"Parsing error: {err}",
            )

        if not certs and not keys:
            add_issue(
                issues,
                "info",
                file_path,
                "empty",
                "No certificate or private key objects detected in file.",
            )

        for cert in certs:
            cn = cert.get("subject_cn")
            subject = cert.get("subject")
            display = cn or subject or "<unknown-subject>"
            is_expired = bool(cert.get("is_expired"))
            days_left = cert.get("days_until_expiry")
            is_self_signed = bool(cert.get("is_self_signed_verified"))
            is_ca = bool(cert.get("is_ca"))

            if is_expired:
                add_issue(
                    issues,
                    "error",
                    file_path,
                    "expiry",
                    f"Certificate expired: {display}",
                    {
                        "subject_cn": cn,
                        "not_valid_after_utc": cert.get("not_valid_after_utc"),
                    },
                )
            elif isinstance(days_left, int) and days_left <= expiry_days:
                add_issue(
                    issues,
                    "warning",
                    file_path,
                    "expiry",
                    f"Certificate expiring soon ({days_left} days): {display}",
                    {
                        "subject_cn": cn,
                        "not_valid_after_utc": cert.get("not_valid_after_utc"),
                    },
                )

            if is_self_signed and not is_ca:
                add_issue(
                    issues,
                    "warning",
                    file_path,
                    "trust",
                    f"Self-signed non-CA certificate detected: {display}",
                    {
                        "issuer": cert.get("issuer"),
                        "subject": cert.get("subject"),
                    },
                )

            if not allow_underscore_identifiers:
                if isinstance(cn, str) and "_" in cn:
                    add_issue(
                        issues,
                        "error",
                        file_path,
                        "naming_policy",
                        f"Subject CN contains underscore: {cn}",
                        {"subject_cn": cn},
                    )

                ext = cert.get("extensions") or {}
                san = ext.get("subject_alt_name") or {}
                for dns_name in (san.get("dns") or []):
                    if isinstance(dns_name, str) and "_" in dns_name:
                        add_issue(
                            issues,
                            "error",
                            file_path,
                            "naming_policy",
                            f"SAN DNS contains underscore: {dns_name}",
                            {"dns_name": dns_name},
                        )

        for key in keys:
            key_type = key.get("private_key_type")
            encrypted = key.get("encrypted")
            load_error = key.get("load_error")

            if load_error:
                add_issue(
                    issues,
                    "error",
                    file_path,
                    "private_key",
                    "Private key load error",
                    {"load_error": load_error},
                )

            if key_type is None:
                add_issue(
                    issues,
                    "warning",
                    file_path,
                    "private_key",
                    "Private key type not detected",
                )

            if encrypted is False and flag_unencrypted_keys:
                add_issue(
                    issues,
                    "warning",
                    file_path,
                    "private_key",
                    "Private key is not encrypted with a password",
                    {"key_type": key_type, "size_bits": key.get("private_key_size_bits")},
                )

    return issues


def render_text(issues: List[Dict[str, Any]]) -> str:
    if not issues:
        return "No issues found for current filter settings.\n"

    counts = {"error": 0, "warning": 0, "info": 0}
    lines: List[str] = []
    for issue in issues:
        sev = issue["severity"]
        counts[sev] = counts.get(sev, 0) + 1
        lines.append(
            f"[{sev.upper()}] {issue['category']} | {issue['file']} | {issue['message']}"
        )
        details = issue.get("details")
        if details:
            lines.append(f"         details: {json.dumps(details, ensure_ascii=True)}")

    header = (
        f"Summary: errors={counts['error']} warnings={counts['warning']} info={counts['info']}"
    )
    return header + "\n" + "\n".join(lines) + "\n"


def main() -> int:
    args = parse_args()
    in_path = Path(args.input)

    if not in_path.exists():
        print(f"Input report not found: {in_path}", file=sys.stderr)
        return 2

    try:
        raw = json.loads(in_path.read_text(encoding="utf-8"))
    except Exception as exc:
        print(f"Failed to parse input JSON: {exc}", file=sys.stderr)
        return 2

    if not isinstance(raw, list):
        print("Input JSON format is invalid: expected top-level list.", file=sys.stderr)
        return 2

    all_issues = collect_issues(
        report=raw,
        expiry_days=args.expiry_days,
        flag_unencrypted_keys=args.flag_unencrypted_keys,
        allow_underscore_identifiers=args.allow_underscore_identifiers,
    )

    threshold = severity_rank(args.min_severity)
    filtered = [i for i in all_issues if severity_rank(i["severity"]) >= threshold]

    if args.json:
        payload = json.dumps(filtered, indent=2, ensure_ascii=True)
    else:
        payload = render_text(filtered)

    if args.output:
        Path(args.output).write_text(payload, encoding="utf-8")
    else:
        print(payload, end="")

    # Non-zero exit if errors are present in filtered set.
    if any(i["severity"] == "error" for i in filtered):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

"""
pineapple cleanup -- Find and remove stale pipeline artifacts.

Usage: python production-pipeline/tools/pineapple_cleanup.py <project-path> [--dry-run] [--json]

Checks:
  1. Git worktrees: stale ones (>30 days old, no commits in 14 days)
  2. Pipeline runs: FAILED or EVOLVE (completed) and >7 days old
  3. Verification records: older than 30 days
"""
from __future__ import annotations
import json
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Literal

@dataclass
class CleanupItem:
    category: str  # "worktree", "run", "verify_record"
    path: str
    reason: str
    age_days: float = 0

@dataclass
class CleanupReport:
    items: list[CleanupItem] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    dry_run: bool = True

    def to_dict(self) -> dict:
        return {
            "dry_run": self.dry_run,
            "stale_items": len(self.items),
            "removed": len(self.removed),
            "items": [
                {"category": i.category, "path": i.path, "reason": i.reason, "age_days": round(i.age_days, 1)}
                for i in self.items
            ],
        }

def find_stale_runs(project_path: Path, max_age_days: float = 7.0) -> list[CleanupItem]:
    """Find completed or failed pipeline runs older than max_age_days."""
    runs_dir = project_path / ".pineapple" / "runs"
    if not runs_dir.is_dir():
        return []

    items = []
    now = time.time()
    for run_dir in runs_dir.iterdir():
        if not run_dir.is_dir():
            continue
        state_file = run_dir / "state.json"
        if not state_file.is_file():
            continue
        try:
            data = json.loads(state_file.read_text(encoding="utf-8"))
            stage = data.get("current_stage", "")
            if stage not in ("EVOLVE", "FAILED"):
                continue  # Active run, skip
            updated = data.get("updated_at", "")
            if updated:
                updated_dt = datetime.fromisoformat(updated)
                age_seconds = now - updated_dt.timestamp()
                age_days = age_seconds / 86400
                if age_days > max_age_days:
                    items.append(CleanupItem(
                        category="run",
                        path=str(run_dir),
                        reason=f"{stage} run, {age_days:.0f} days old",
                        age_days=age_days,
                    ))
        except Exception:
            continue
    return items

def find_stale_verify_records(project_path: Path, max_age_days: float = 30.0) -> list[CleanupItem]:
    """Find verification records older than max_age_days."""
    verify_dir = project_path / ".pineapple" / "verify"
    if not verify_dir.is_dir():
        return []

    items = []
    now = time.time()
    for record_path in verify_dir.glob("*.json"):
        try:
            data = json.loads(record_path.read_text(encoding="utf-8"))
            ts = data.get("timestamp", "")
            if ts:
                dt = datetime.fromisoformat(ts)
                age_seconds = now - dt.timestamp()
                age_days = age_seconds / 86400
                if age_days > max_age_days:
                    items.append(CleanupItem(
                        category="verify_record",
                        path=str(record_path),
                        reason=f"Verification record {age_days:.0f} days old",
                        age_days=age_days,
                    ))
        except Exception:
            continue
    return items

def find_stale_worktrees(project_path: Path) -> list[CleanupItem]:
    """Find stale git worktrees."""
    items = []
    try:
        result = subprocess.run(
            ["git", "worktree", "list", "--porcelain"],
            cwd=str(project_path),
            capture_output=True, text=True, timeout=10,
        )
        if result.returncode != 0:
            return []

        # Parse porcelain output
        worktrees = []
        current = {}
        for line in result.stdout.splitlines():
            if line.startswith("worktree "):
                if current:
                    worktrees.append(current)
                current = {"path": line[9:]}
            elif line.startswith("branch "):
                current["branch"] = line[7:]
        if current:
            worktrees.append(current)

        # Skip the main worktree
        if len(worktrees) > 1:
            for wt in worktrees[1:]:  # Skip first (main)
                wt_path = Path(wt["path"])
                if wt_path.is_dir():
                    # Check last commit age
                    try:
                        age_result = subprocess.run(
                            ["git", "log", "-1", "--format=%ct"],
                            cwd=str(wt_path),
                            capture_output=True, text=True, timeout=5,
                        )
                        if age_result.returncode == 0 and age_result.stdout.strip():
                            commit_ts = int(age_result.stdout.strip())
                            age_days = (time.time() - commit_ts) / 86400
                            if age_days > 14:
                                items.append(CleanupItem(
                                    category="worktree",
                                    path=str(wt_path),
                                    reason=f"No commits in {age_days:.0f} days",
                                    age_days=age_days,
                                ))
                    except Exception:
                        pass
    except Exception:
        pass
    return items

def run_cleanup(project_path: Path, dry_run: bool = True) -> CleanupReport:
    """Find and optionally remove stale artifacts."""
    report = CleanupReport(dry_run=dry_run)

    report.items.extend(find_stale_runs(project_path))
    report.items.extend(find_stale_verify_records(project_path))
    report.items.extend(find_stale_worktrees(project_path))

    if not dry_run:
        import shutil
        for item in report.items:
            try:
                p = Path(item.path)
                if item.category == "worktree":
                    subprocess.run(
                        ["git", "worktree", "remove", str(p)],
                        cwd=str(project_path),
                        capture_output=True, timeout=10,
                    )
                elif p.is_dir():
                    shutil.rmtree(p)
                elif p.is_file():
                    p.unlink()
                report.removed.append(item.path)
            except Exception as e:
                pass  # Log but continue

    return report


if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser(description="Pineapple Pipeline Cleanup")
    parser.add_argument("project_path", type=Path, help="Project path")
    parser.add_argument("--dry-run", action="store_true", default=True, help="Show what would be removed (default)")
    parser.add_argument("--execute", action="store_true", help="Actually remove stale artifacts")
    parser.add_argument("--json", action="store_true", help="JSON output")
    args = parser.parse_args()

    dry_run = not args.execute
    report = run_cleanup(args.project_path, dry_run=dry_run)

    if args.json:
        print(json.dumps(report.to_dict(), indent=2))
    else:
        print(f"\nPineapple Cleanup v1.0.0 {'(DRY RUN)' if dry_run else ''}")
        print(f"========================")
        if not report.items:
            print("  No stale artifacts found.")
        for item in report.items:
            action = "WOULD REMOVE" if dry_run else "REMOVED"
            print(f"  [{action}] {item.category}: {item.path}")
            print(f"           {item.reason}")
        if not dry_run:
            print(f"\nRemoved {len(report.removed)}/{len(report.items)} items.")

    sys.exit(0)

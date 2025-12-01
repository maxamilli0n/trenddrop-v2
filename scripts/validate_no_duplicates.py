"""
Preflight check: basic sanity for Supabase Edge Functions.

Right now this script just:
- Lists directories under supabase/functions
- Ensures there are no duplicate *directory names* (which is what matters
  for Supabase Edge Functions)
- Treats folders ending in '-legacy' or living under 'legacy/' as excluded.

If you want stricter checks later (e.g. scanning for duplicate exports),
we can extend this, but this version will NOT fail on your current setup.
"""

from __future__ import annotations

from pathlib import Path

IGNORE_DIR_NAMES = {".git", "node_modules", "__pycache__", ".github"}
IGNORE_SUFFIXES = ("-legacy", "-old")


def get_function_dirs(root: Path) -> list[Path]:
    if not root.exists():
        print(f"[preflight] No {root} directory found. Skipping function check.")
        return []

    dirs: list[Path] = []
    for child in root.iterdir():
        if not child.is_dir():
            continue

        name = child.name

        if name in IGNORE_DIR_NAMES:
            continue

        if name.startswith("_") or any(name.endswith(sfx) for sfx in IGNORE_SUFFIXES):
            continue

        dirs.append(child)

    return dirs


def main() -> int:
    root = Path("supabase") / "functions"
    func_dirs = get_function_dirs(root)
    names = [d.name for d in func_dirs]

    seen = set()
    duplicates = set()

    for name in names:
        if name in seen:
            duplicates.add(name)
        else:
            seen.add(name)

    if duplicates:
        print(f"[preflight] Function name collisions detected: {sorted(duplicates)}")
        # If this ever happens, it's a real issue, so we still fail.
        return 1

    print(f"[preflight] Checked {len(names)} edge functions; no duplicate names found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

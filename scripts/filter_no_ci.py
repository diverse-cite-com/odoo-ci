#!/usr/bin/env python3
"""Filter modules based on no_ci list in repo_deps.yaml.

Usage:
    filter_no_ci.py <addons_csv> <repo_deps.yaml> [--emit testable|no_ci]

Reads the no_ci list from repo_deps.yaml and partitions the addons CSV.
  --emit testable  (default) Print addons NOT in no_ci
  --emit no_ci     Print addons that ARE in no_ci
"""
import argparse
import re
import sys


def _parse_no_ci(path):
    """Parse the no_ci list from repo_deps.yaml without requiring pyyaml."""
    no_ci = set()
    in_no_ci = False
    with open(path) as f:
        for line in f:
            stripped = line.strip()
            if stripped == "no_ci:" or stripped.startswith("no_ci:"):
                in_no_ci = True
                continue
            if in_no_ci:
                m = re.match(r"^-\s+(\S+)", stripped)
                if m:
                    no_ci.add(m.group(1))
                elif stripped and not stripped.startswith("#"):
                    break  # next top-level key
    return no_ci


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("addons_csv", help="Comma-separated list of addon names")
    parser.add_argument("deps_file", help="Path to repo_deps.yaml")
    parser.add_argument(
        "--emit",
        choices=("testable", "no_ci"),
        default="testable",
        help="Which partition to output (default: testable)",
    )
    args = parser.parse_args()

    no_ci = _parse_no_ci(args.deps_file)
    all_addons = [a for a in args.addons_csv.split(",") if a]

    if no_ci:
        print(
            "no_ci modules: " + ", ".join(sorted(no_ci)),
            file=sys.stderr,
        )

    if args.emit == "testable":
        result = [a for a in all_addons if a not in no_ci]
    else:
        result = [a for a in all_addons if a in no_ci]

    print(",".join(result))


if __name__ == "__main__":
    main()

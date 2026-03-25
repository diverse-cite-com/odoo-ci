#!/usr/bin/env python3
"""Filter out modules listed under no_ci in repo_deps.yaml."""
import sys
import yaml


def main():
    addons_csv = sys.argv[1]
    deps_file = sys.argv[2]

    with open(deps_file) as f:
        data = yaml.safe_load(f) or {}

    no_ci = set(data.get("no_ci", []))
    if no_ci:
        print(
            "Excluding no_ci modules: " + ", ".join(sorted(no_ci)),
            file=sys.stderr,
        )

    addons = [a for a in addons_csv.split(",") if a not in no_ci]
    print(",".join(addons))


if __name__ == "__main__":
    main()

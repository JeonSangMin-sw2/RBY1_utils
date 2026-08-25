from __future__ import annotations

import argparse

from . import benchmark, make_oracle, validate_corpus


def main() -> int:
    parser = argparse.ArgumentParser(prog="rby1-cs-analyzer-v4-cli")
    parser.add_argument("command", choices=("benchmark", "make-oracle", "validate-corpus"))
    args, remaining = parser.parse_known_args()
    mapping = {
        "benchmark": benchmark.main,
        "make-oracle": make_oracle.main,
        "validate-corpus": validate_corpus.main,
    }
    import sys

    original = sys.argv
    try:
        sys.argv = [f"{original[0]} {args.command}", *remaining]
        return int(mapping[args.command]())
    finally:
        sys.argv = original


if __name__ == "__main__":
    raise SystemExit(main())

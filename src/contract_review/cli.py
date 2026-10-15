import argparse
import json
from pathlib import Path

from .workflow import run_review


def main():
    parser = argparse.ArgumentParser(description="Review a synthetic numbered contract")
    parser.add_argument("--contract", default="fixtures/meridian-services.md")
    parser.add_argument("--output")
    args = parser.parse_args()
    report = run_review(args.contract)
    text = json.dumps(report, indent=2) + "\n"
    if args.output:
        Path(args.output).write_text(text)
    else:
        print(text, end="")

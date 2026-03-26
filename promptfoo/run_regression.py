#!/usr/bin/env python3
"""Run PromptFoo regression tests for pipeline prompts.

Usage: python promptfoo/run_regression.py
Requires: Node.js (npx) installed
"""
import subprocess
import sys


def main():
    # Check npx is available
    try:
        subprocess.run(["npx", "--version"], capture_output=True, check=True, timeout=10)
    except (FileNotFoundError, subprocess.CalledProcessError):
        print("[SKIP] npx not found. Install Node.js to run prompt regression tests.")
        print("[SKIP] Download: https://nodejs.org/")
        sys.exit(0)  # Skip, don't fail

    print("[INFO] Running PromptFoo prompt regression tests...")
    result = subprocess.run(
        ["npx", "promptfoo", "eval", "--config", "promptfoo/promptfooconfig.yaml", "--no-cache"],
        text=True,
        timeout=300,
    )

    sys.exit(result.returncode)


if __name__ == "__main__":
    main()

"""Backward-compatible alias for the weekly pipeline (python -m scripts.weekly_pipeline)."""
import sys

from scripts.weekly_pipeline import main

if __name__ == "__main__":
    sys.exit(main())

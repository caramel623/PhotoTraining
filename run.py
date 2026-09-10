"""Convenience launcher.

Usage:
    python run.py [--workspace PATH]
"""
import sys

from vehicle_dataset_manager.main import main

if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))
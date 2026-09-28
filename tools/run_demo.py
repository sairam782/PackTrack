"""Compatibility entry point for the interactive PackTrack app."""
from pathlib import Path
import sys
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from launch import main

if __name__ == "__main__":
    main()

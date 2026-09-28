"""Compatibility entry point; launch.py starts the interactive app."""
from verify_tracking import main

if __name__ == "__main__":
    print("This command runs a verification test and exits. For the app: .venv/bin/python launch.py", flush=True)
    main()

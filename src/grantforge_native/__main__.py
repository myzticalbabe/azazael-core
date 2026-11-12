"""`python -m grantforge_native` — the module entry the executable is built from."""
import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

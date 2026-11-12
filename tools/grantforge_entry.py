"""Build entry for grantforge.exe — the only file the packager compiles as a script."""
import sys

from grantforge_native.cli import main

if __name__ == "__main__":
    sys.exit(main())

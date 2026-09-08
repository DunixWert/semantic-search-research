"""Package entry point for running literature_library as a module or script."""
import sys
from pathlib import Path

# Add package directory to sys.path if invoked directly
current_dir = Path(__file__).resolve().parent
parent_dir = current_dir.parent
if str(parent_dir) not in sys.path:
    sys.path.insert(0, str(parent_dir))

from literature_library.cli import main

if __name__ == "__main__":
    raise SystemExit(main())

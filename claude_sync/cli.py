"""Entry point for the installed ``claude-sync`` console script.

Loads the single-file ``claude-sync.py`` at the repository root via
importlib so we don't have to restructure the existing implementation.
The hyphenated filename isn't a valid Python identifier, hence the
spec_from_file_location approach.
"""

import importlib.util
import os
import sys


def main() -> None:
    """Load claude-sync.py and delegate to its main()."""
    impl_path = os.path.join(
        os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
        "claude-sync.py",
    )

    if not os.path.exists(impl_path):
        print(f"error: could not find claude-sync.py at {impl_path}", file=sys.stderr)
        sys.exit(1)

    spec = importlib.util.spec_from_file_location("claude_sync_impl", impl_path)
    if spec is None or spec.loader is None:
        print(f"error: failed to load claude-sync.py from {impl_path}", file=sys.stderr)
        sys.exit(1)

    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    sys.exit(mod.main())


if __name__ == "__main__":
    main()

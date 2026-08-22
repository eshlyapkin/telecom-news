"""Allow running the package with ``python -m telecom_news``."""

import sys

from .cli import main

if __name__ == "__main__":
    sys.exit(main())

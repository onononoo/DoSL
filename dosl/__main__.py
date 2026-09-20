"""Entry point for ``python -m dosl`` and for the frozen executables.

The import below is absolute rather than relative on purpose. PyInstaller
runs this file as a top-level script named ``__main__``, with no parent
package, so ``from .cli import main`` raises ImportError inside the bundle
while working perfectly from source. Absolute works in both.
"""

import sys

from dosl.cli import main

if __name__ == "__main__":
    sys.exit(main())

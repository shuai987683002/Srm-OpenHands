"""Frozen-binary entry point for the ``srm`` CLI.

PyInstaller runs the entry script as ``__main__`` with no package context, so a
module that uses relative imports cannot serve as the entry point directly.
This shim imports the package absolutely and calls the CLI's ``main``.
"""

import sys

from srm_open.api.cli import main

if __name__ == "__main__":
    sys.exit(main())

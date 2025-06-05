# devpulse/__main__.py
# Enable `python -m devpulse` to launch the CLI.

from .cli.__main__ import main

if __name__ == "__main__":
    raise SystemExit(main())

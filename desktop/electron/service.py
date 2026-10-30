"""Standalone local API entry point; never dispatch frozen Python as a CLI interpreter."""
from multiprocessing import freeze_support
from repopilot.desktop.__main__ import main

if __name__ == '__main__':
    freeze_support()
    main()

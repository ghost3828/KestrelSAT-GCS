"""
KestrelSAT Ground Control Station Application
A GUI for interacting with serial devices, built with tkinter, pyserial and
PyQtGraph.

This file stays the entry point so the PyInstaller specs keep working
unchanged; the implementation lives in the kestrelsat package.
"""

from kestrelsat.app import main

if __name__ == "__main__":
    main()

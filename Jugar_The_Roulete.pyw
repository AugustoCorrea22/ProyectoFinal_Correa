"""Doble clic aquí para jugar a The Roulete (se abre sin ventana de consola)."""
from pathlib import Path
import runpy

runpy.run_path(str(Path(__file__).resolve().with_name("main.py")), run_name="__main__")

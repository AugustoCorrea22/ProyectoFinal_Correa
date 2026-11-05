"""
Punto de entrada principal del videojuego Ruleta Roguelike.
Ejecuta `python3.13 main.py` para abrir la interfaz gráfica completa (Fase 6).
"""

from pathlib import Path
import sys
import traceback

# Garantiza que el directorio raíz del proyecto esté siempre en sys.path
if getattr(sys, "frozen", False):
    PROJECT_ROOT = Path(sys.executable).resolve().parent
else:
    PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


if __name__ == "__main__":
    try:
        from src.table_rules_ext import install as _install_table_rules
        _install_table_rules()
        from src.ui import run_gui
        run_gui()
    except Exception:
        log_file = PROJECT_ROOT / "crash_log.txt"
        log_file.write_text(traceback.format_exc(), encoding="utf-8")
        raise

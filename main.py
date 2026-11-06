"""
Punto de entrada de The Roulete.
Ejecuta `py main.py` (o haz doble clic en Jugar_The_Roulete.pyw).
La primera vez instala customtkinter si falta: es la única librería externa del juego.
"""

import importlib
from pathlib import Path
import subprocess
import sys
import traceback

PROJECT_ROOT = Path(__file__).resolve().parent
if str(PROJECT_ROOT) not in sys.path:
    sys.path.insert(0, str(PROJECT_ROOT))


def _install_customtkinter() -> bool:
    """Instala customtkinter con pip, mostrando un aviso mientras tanto."""
    import site
    import tkinter as tk

    window = tk.Tk()
    window.title("The Roulete")
    tk.Label(window, text="Preparando el juego por primera vez...", padx=40, pady=30).pack()
    window.update()

    # pythonw no tiene consola y pip la necesita: se usa python.exe, sin mostrar ventana
    python = Path(sys.executable)
    if python.stem.lower() == "pythonw":
        python = python.with_name("python" + python.suffix)
    command = [str(python), "-m", "pip", "install", "--quiet", "customtkinter"]
    in_virtualenv = sys.prefix != sys.base_prefix
    if not in_virtualenv:
        command.insert(4, "--user")
    flags = subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0
    try:
        ok = subprocess.run(command, creationflags=flags, capture_output=True, timeout=300).returncode == 0
    except (OSError, subprocess.SubprocessError):
        ok = False
    window.destroy()
    if ok and not in_virtualenv:
        site.addsitedir(site.getusersitepackages())
    importlib.invalidate_caches()
    return ok


def _ensure_customtkinter() -> None:
    try:
        import customtkinter  # noqa: F401
        return
    except ImportError:
        pass
    if _install_customtkinter():
        try:
            import customtkinter  # noqa: F401
            return
        except ImportError:
            pass
    from tkinter import Tk, messagebox

    Tk().withdraw()
    messagebox.showerror(
        "The Roulete",
        "No se pudo instalar customtkinter. Conéctate a internet y vuelve a intentarlo, "
        "o ejecuta en una terminal:\n\npy -m pip install customtkinter",
    )
    sys.exit(1)


if __name__ == "__main__":
    try:
        _ensure_customtkinter()
        from src.ui import run_gui
        run_gui()
    except Exception:
        (PROJECT_ROOT / "crash_log.txt").write_text(traceback.format_exc(), encoding="utf-8")
        raise

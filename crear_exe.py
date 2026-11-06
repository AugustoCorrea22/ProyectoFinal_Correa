"""
Crea The_Roulete.exe: un acceso directo pequeño que abre el juego con el Python instalado.

Usa el compilador de C# que ya viene con Windows, así que no hace falta instalar nada
y el .exe no necesita archivos .dll propios. Ejecutar con:
    py crear_exe.py
"""

import base64
import os
from pathlib import Path
import struct
import subprocess
import tempfile

from src.icono import ICON_PNG_BASE64

ROOT = Path(__file__).resolve().parent
EXE_PATH = ROOT / "The_Roulete.exe"

LAUNCHER_SOURCE = r"""
using System;
using System.Collections.Generic;
using System.Diagnostics;
using System.IO;
using System.Windows.Forms;

static class Program
{
    const string Title = "The Roulete";

    static IEnumerable<string> PythonCandidates()
    {
        yield return "pyw.exe";
        string[] roots = {
            Path.Combine(Environment.GetFolderPath(Environment.SpecialFolder.LocalApplicationData), "Programs", "Python"),
            Environment.GetFolderPath(Environment.SpecialFolder.ProgramFiles),
        };
        foreach (string root in roots)
        {
            if (!Directory.Exists(root)) continue;
            string[] folders = Directory.GetDirectories(root, "Python3*");
            Array.Sort(folders);
            Array.Reverse(folders);
            foreach (string folder in folders)
            {
                string candidate = Path.Combine(folder, "pythonw.exe");
                if (File.Exists(candidate)) yield return candidate;
            }
        }
        yield return "pythonw.exe";
    }

    [STAThread]
    static int Main()
    {
        string folder = AppDomain.CurrentDomain.BaseDirectory;
        string game = Path.Combine(folder, "Jugar_The_Roulete.pyw");
        if (!File.Exists(game))
        {
            MessageBox.Show("No encuentro Jugar_The_Roulete.pyw.\nDeja este .exe dentro de la carpeta del juego.",
                            Title, MessageBoxButtons.OK, MessageBoxIcon.Error);
            return 1;
        }
        foreach (string python in PythonCandidates())
        {
            try
            {
                ProcessStartInfo info = new ProcessStartInfo(python, "\"" + game + "\"");
                info.WorkingDirectory = folder;
                info.UseShellExecute = false;
                Process.Start(info);
                return 0;
            }
            catch (Exception) { }
        }
        DialogResult answer = MessageBox.Show(
            "Para jugar necesitas Python 3.\n¿Quieres abrir la página de descarga?",
            Title, MessageBoxButtons.YesNo, MessageBoxIcon.Information);
        if (answer == DialogResult.Yes) Process.Start("https://www.python.org/downloads/");
        return 1;
    }
}
"""


def _write_icon(path: Path) -> None:
    """Envuelve el PNG del ícono del juego en un archivo .ico."""
    png = base64.b64decode(ICON_PNG_BASE64)
    width, height = struct.unpack(">II", png[16:24])
    entry = struct.pack(
        "<BBBBHHII", width % 256, height % 256, 0, 0, 1, 32, len(png), 22
    )
    path.write_bytes(struct.pack("<HHH", 0, 1, 1) + entry + png)


def main() -> None:
    compiler = Path(os.environ["WINDIR"]) / "Microsoft.NET" / "Framework64" / "v4.0.30319" / "csc.exe"
    if not compiler.exists():
        raise SystemExit(f"No encontré el compilador de Windows en {compiler}")
    with tempfile.TemporaryDirectory() as tmp:
        source = Path(tmp) / "lanzador.cs"
        icon = Path(tmp) / "icono.ico"
        source.write_text(LAUNCHER_SOURCE, encoding="utf-8")
        _write_icon(icon)
        result = subprocess.run(
            [str(compiler), "/nologo", "/target:winexe", "/optimize+", f"/win32icon:{icon}", f"/out:{EXE_PATH}", str(source)],
            capture_output=True, text=True, errors="replace",
        )
    if result.returncode != 0:
        raise SystemExit(f"No se pudo compilar:\n{result.stdout}\n{result.stderr}")
    print(f"Listo: {EXE_PATH} ({EXE_PATH.stat().st_size / 1024:.0f} KB)")


if __name__ == "__main__":
    main()

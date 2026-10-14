"""Run the app with the project virtual environment's Python."""

import os
import subprocess
import sys

ROOT = os.path.dirname(os.path.abspath(__file__))


def _venv_python():
    if sys.platform == "win32":
        names = ("venv", ".venv")
        exe = os.path.join("Scripts", "python.exe")
    else:
        names = ("venv", ".venv")
        exe = os.path.join("bin", "python")
    for name in names:
        path = os.path.join(ROOT, name, exe)
        if os.path.isfile(path):
            return path
    return None


def main():
    py = _venv_python()
    app = os.path.join(ROOT, "app.py")
    if not os.path.isfile(app):
        print("app.py not found.", file=sys.stderr)
        sys.exit(1)
    if not py:
        print(
            "No virtual environment found. From this folder run:\n"
            "  python -m venv venv\n"
            "  venv\\Scripts\\pip install -r requirements.txt   (Windows)\n"
            "  ./venv/bin/pip install -r requirements.txt       (macOS/Linux)",
            file=sys.stderr,
        )
        sys.exit(1)
    os.chdir(ROOT)
    raise SystemExit(subprocess.call([py, app]))


if __name__ == "__main__":
    main()

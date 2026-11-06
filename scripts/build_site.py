"""Genera `site/`: la UI estática + `orq.zip` (código Python que corre en el navegador con Pyodide).

Uso:  python scripts/build_site.py      (y luego publicar `site/` en la rama gh-pages, ver README)
"""
import shutil
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
SITE = ROOT / "site"


def main():
    SITE.mkdir(exist_ok=True)
    for item in SITE.iterdir():            # conserva site/.git para republicar en gh-pages sin forzar el push
        if item.name != ".git":
            shutil.rmtree(item) if item.is_dir() else item.unlink()
    shutil.copytree(ROOT / "web", SITE, dirs_exist_ok=True)
    with zipfile.ZipFile(SITE / "orq.zip", "w", zipfile.ZIP_DEFLATED) as z:
        for f in sorted((ROOT / "orq").rglob("*.py")):
            z.write(f, f.relative_to(ROOT).as_posix())
    (SITE / ".nojekyll").write_text("")
    print(f"Sitio generado en {SITE} ({sum(1 for _ in SITE.rglob('*') if _.is_file())} archivos)")


if __name__ == "__main__":
    main()

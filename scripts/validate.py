#!/usr/bin/env python3
"""Compile project Python files and run the complete standard-library test suite."""
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def source_files():
    files = (
        list(ROOT.glob("*.py"))
        + list((ROOT / "tests").glob("*.py"))
        + list((ROOT / "scripts").glob("*.py"))
    )
    return sorted(files)


def main():
    files = source_files()
    try:
        for path in files:
            compile(path.read_text(encoding="utf-8"), str(path), "exec")
    except (OSError, SyntaxError, UnicodeDecodeError) as error:
        print(f"[FALHA] Verificação de sintaxe: {error}", file=sys.stderr)
        return 1

    print(f"[OK] Sintaxe Python verificada em {len(files)} arquivo(s).")
    result = subprocess.run(
        [
            sys.executable,
            "-m",
            "unittest",
            "discover",
            "-s",
            str(ROOT / "tests"),
            "-v",
        ],
        cwd=ROOT,
        check=False,
    )
    if result.returncode:
        print("[FALHA] A suíte de testes não passou.", file=sys.stderr)
        return result.returncode

    print("[OK] Suíte de testes concluída.")
    return 0


if __name__ == "__main__":
    sys.exit(main())

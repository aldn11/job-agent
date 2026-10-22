#!/usr/bin/env python3
"""Compile project Python files and run the complete standard-library test suite."""
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_TESTS = {
    "add_linkedin.py": "test_add_linkedin.py",
    "collector.py": "test_collector.py",
    "cover_letter.py": "test_cover_letter.py",
    "email_collector.py": "test_email_collector.py",
    "list_jobs.py": "test_list_jobs.py",
    "location_policy.py": "test_location_policy.py",
    "run_pipeline.py": "test_run_pipeline.py",
    "store.py": "test_store_retries.py",
    "telegram_notify.py": "test_notifications.py",
    "url_policy.py": "test_url_policy.py",
    "wrapper.py": "test_wrapper_validation.py",
}


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
    project_modules = {path.name for path in ROOT.glob("*.py")}
    unmapped_modules = sorted(project_modules - COMPONENT_TESTS.keys())
    if unmapped_modules:
        print(
            "[FALHA] Componentes sem associação a testes: "
            + ", ".join(unmapped_modules),
            file=sys.stderr,
        )
        return 1

    missing_tests = [
        test_file
        for test_file in COMPONENT_TESTS.values()
        if not (ROOT / "tests" / test_file).is_file()
    ]
    if missing_tests:
        print(
            "[FALHA] Componentes sem arquivo de teste: "
            + ", ".join(missing_tests),
            file=sys.stderr,
        )
        return 1

    print(
        "[OK] Todos os "
        f"{len(COMPONENT_TESTS)} componentes do projeto têm teste associado."
    )
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

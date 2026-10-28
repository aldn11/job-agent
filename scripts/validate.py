#!/usr/bin/env python3
"""Compile project Python files and run the complete standard-library test suite."""
import argparse
import os
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
COMPONENT_TESTS = {
    "add_linkedin.py": "test_add_linkedin.py",
    "add_remot.py": "test_add_remot.py",
    "bairesdev_collector.py": "test_bairesdev_collector.py",
    "collector.py": "test_collector.py",
    "email_collector.py": "test_email_collector.py",
    "feed_filters.py": "test_feed_filters.py",
    "remot_parser.py": "test_remot_parser.py",
    "remotive_collector.py": "test_remotive_collector.py",
    "list_jobs.py": "test_list_jobs.py",
    "location_policy.py": "test_location_policy.py",
    "jobspresso_collector.py": "test_jobspresso_collector.py",
    "run_pipeline.py": "test_run_pipeline.py",
    "store.py": "test_store_retries.py",
    "telegram_notify.py": "test_notifications.py",
    "url_policy.py": "test_url_policy.py",
    "wrapper.py": "test_wrapper_validation.py",
}
SCRIPT_TESTS = {
    "scripts/validate.py": "test_validate_script.py",
}


def check_hermes_runtime(container_name="hermes", profile_name="vargas"):
    """Ask Hermes' own strict registry reader to validate its runtime file."""
    registry_path = f"/opt/data/profiles/{profile_name}/runtime/active_sessions.json"
    check_code = (
        "import sys; from pathlib import Path; "
        "from hermes_cli.active_sessions import _read_entries; "
        "entries = _read_entries(Path(sys.argv[1]), strict=True); "
        "print(f'active_sessions.json válido; leases={len(entries)}')"
    )
    command = [
        "docker", "exec", "-u", "hermes", container_name,
        "python", "-c", check_code, registry_path,
    ]
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=15, check=False
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        print(f"[FALHA] Não foi possível validar o runtime Hermes: {error}", file=sys.stderr)
        return False

    if result.returncode:
        detail = (result.stderr or result.stdout or "sem diagnóstico").strip()
        print(
            "[FALHA] Registro de sessões Hermes inválido ou inacessível: "
            + detail[-1500:],
            file=sys.stderr,
        )
        return False

    print("[OK] " + (result.stdout or "Registro de sessões Hermes válido.").strip())
    return True


def source_files():
    files = (
        list(ROOT.glob("*.py"))
        + list((ROOT / "tests").glob("*.py"))
        + list((ROOT / "scripts").glob("*.py"))
    )
    return sorted(files)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--check-hermes-runtime",
        action="store_true",
        help="valida o active_sessions.json no container Hermes usando o leitor estrito do próprio Hermes",
    )
    parser.add_argument(
        "--hermes-container",
        default=os.getenv("HERMES_CONTAINER_NAME", "hermes"),
        help="nome do container Hermes (padrão: HERMES_CONTAINER_NAME ou hermes)",
    )
    parser.add_argument(
        "--hermes-profile",
        default=os.getenv("HERMES_PROFILE", "vargas"),
        help="perfil Hermes cujo registro será validado (padrão: HERMES_PROFILE ou vargas)",
    )
    args = parser.parse_args(argv)

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

    missing_script_tests = [
        test_file
        for test_file in SCRIPT_TESTS.values()
        if not (ROOT / "tests" / test_file).is_file()
    ]
    if missing_script_tests:
        print(
            "[FALHA] Scripts sem teste associado: "
            + ", ".join(missing_script_tests),
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
    if args.check_hermes_runtime and not check_hermes_runtime(
        args.hermes_container, args.hermes_profile
    ):
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())

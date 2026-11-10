import sys


def main():
    if sys.argv[1:2] == ["--check-bundle"]:
        from nodera.installer_bundle import check_command

        return check_command(sys.argv[2:])

    from nodera.installer_ui import main as run_installer

    return run_installer()


if __name__ == "__main__":
    raise SystemExit(main())

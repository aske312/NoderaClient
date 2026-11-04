import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--proxy-guard":
        from nodera.proxy_guard import main
    elif len(sys.argv) > 1 and sys.argv[1] in {
        "--autostart-status",
        "--configure-autostart",
        "--remove-autostart",
    }:
        from nodera.autostart import handle_command

        raise SystemExit(handle_command(sys.argv[1:]))
    else:
        from nodera.app import main
    raise SystemExit(main())

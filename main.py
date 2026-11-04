import sys

if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "--proxy-guard":
        from nodera.proxy_guard import main
    else:
        from nodera.app import main
    raise SystemExit(main())

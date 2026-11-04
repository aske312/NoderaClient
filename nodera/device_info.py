"""Windows edition and release sent with subscription requests."""

import platform


def windows_registry() -> dict[str, str]:
    try:
        import winreg

        with winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows NT\CurrentVersion"
        ) as key:
            values = {}
            for name in ("ProductName", "DisplayVersion", "CurrentBuildNumber", "InstallationType"):
                try:
                    value, _ = winreg.QueryValueEx(key, name)
                    if isinstance(value, str):
                        values[name] = value
                except OSError:
                    pass
            return values
    except (ImportError, OSError):
        return {}


def windows_identity() -> tuple[str, str]:
    values = windows_registry()
    product = values.get("ProductName", "")
    build = values.get("CurrentBuildNumber", "")
    # Windows 11 can retain "Windows 10" in the compatibility registry value.
    if (
        values.get("InstallationType") == "Client"
        and build.isdecimal()
        and int(build) >= 22000
        and product.startswith("Windows 10")
    ):
        product = product.replace("Windows 10", "Windows 11", 1)

    def header(value: str, fallback: str, limit: int) -> str:
        safe = " ".join(value.split())
        return safe[:limit] if safe and safe.isascii() else fallback

    return (
        header(product, f"Windows {platform.release()}", 80),
        header(values.get("DisplayVersion", ""), platform.version(), 40),
    )

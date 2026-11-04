"""Per-user DPAPI protection; credentials never enter settings.json."""

import ctypes
import json
import os
import tempfile
from ctypes import wintypes
from dataclasses import asdict
from pathlib import Path

from nodera.models import ClientError, Profile, ProfileLibrary, Settings


def data_directory() -> Path:
    if os.name != "nt":
        raise ClientError("Nodera поддерживает Windows.")
    return Path(os.environ["LOCALAPPDATA"]) / "NoderaClient"


def protect(data: bytes, decrypt: bool = False) -> bytes:
    if os.name != "nt":
        raise ClientError("Защита профиля требует Windows DPAPI.")

    class Blob(ctypes.Structure):
        _fields_ = [("size", wintypes.DWORD), ("data", ctypes.POINTER(ctypes.c_ubyte))]

    buffer = ctypes.create_string_buffer(data)
    source = Blob(len(data), ctypes.cast(buffer, ctypes.POINTER(ctypes.c_ubyte)))
    output = Blob()
    crypt = ctypes.WinDLL("crypt32", use_last_error=True)
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel.LocalFree.argtypes = [ctypes.c_void_p]
    kernel.LocalFree.restype = ctypes.c_void_p
    function = crypt.CryptUnprotectData if decrypt else crypt.CryptProtectData
    function.argtypes = [
        ctypes.POINTER(Blob),
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(Blob),
    ]
    function.restype = wintypes.BOOL
    if not function(ctypes.byref(source), None, None, None, None, 1, ctypes.byref(output)):
        raise ClientError("Windows не удалось расшифровать или сохранить профиль.")
    try:
        return ctypes.string_at(output.data, output.size)
    finally:
        kernel.LocalFree(output.data)


def atomic_write(path: Path, content: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    descriptor, temporary = tempfile.mkstemp(dir=path.parent, prefix=".nodera-")
    try:
        with os.fdopen(descriptor, "wb") as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def valid_client_point(point):
    if not isinstance(point, dict):
        return False
    if not point:
        return True
    return (
        set(point) <= {"code", "lat", "lon", "name", "source", "detected_at"}
        and isinstance(point.get("code"), str)
        and len(point["code"]) == 2
        and point["code"].isascii()
        and point["code"].isalpha()
        and type(point.get("lat")) in (int, float)
        and -90 <= point["lat"] <= 90
        and type(point.get("lon")) in (int, float)
        and -180 <= point["lon"] <= 180
        and isinstance(point.get("name"), str)
        and len(point["name"]) <= 80
        and point.get("source") in {"manual", "ip-physical"}
    )


class Store:
    def __init__(self, root: Path):
        self.root = root

    def load_settings(self) -> Settings:
        path = self.root / "settings.json"
        if not path.exists():
            settings = Settings()
            self.save_settings(settings)
            return settings
        try:
            data = json.loads(path.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError
            # Legacy executable overrides are intentionally ignored: the core is embedded.
            migrated_core = "core_path" in data
            data.pop("core_path", None)
            # Connection mode belongs to this installation, never to a subscription.
            migrated_mode = "follow_profile" in data
            if type(data.pop("follow_profile", False)) is not bool:
                raise ValueError
            settings = Settings(**data)
            if (
                not isinstance(settings.device_id, str)
                or not settings.device_id
                or type(settings.mixed_port) is not int
                or not (settings.mixed_port == 0 or 1024 <= settings.mixed_port <= 65535)
                or type(settings.tun) is not bool
                or type(settings.system_proxy) is not bool
                or type(settings.update_hours) is not int
                or settings.update_hours not in (0, 1, 6, 12, 24)
                or type(settings.sidebar_collapsed) is not bool
                or type(settings.poll_seconds) is not int
                or settings.poll_seconds not in (1, 3, 5, 10)
                or settings.log_level not in ("debug", "info", "warning", "error")
                or type(settings.log_limit) is not int
                or not 500 <= settings.log_limit <= 20000
                or type(settings.save_logs) is not bool
                or type(settings.geo_auto) is not bool
                or type(settings.close_to_tray) is not bool
                or settings.flow_poll_seconds not in (1, 2, 3, 5, 10)
                or type(settings.flow_poll_seconds) is not int
                or settings.flow_history_minutes not in (1, 5, 15, 30)
                or type(settings.flow_history_minutes) is not int
                or type(settings.save_connection_logs) is not bool
                or settings.connection_log_mb not in (4, 8, 16, 32)
                or type(settings.connection_log_mb) is not int
                or settings.connection_log_interval not in (5, 15, 30, 60)
                or type(settings.connection_log_interval) is not int
                or type(settings.map_auto_zoom) is not bool
                or not valid_client_point(settings.client_point)
                or type(settings.client_location_attempted) is not bool
                or settings.tun_stack not in ("profile", "mixed", "system", "gvisor")
                or type(settings.tun_mtu) is not int
                or not (settings.tun_mtu == 0 or 1280 <= settings.tun_mtu <= 9000)
                or settings.ipv6_mode not in ("profile", "on", "off")
            ):
                raise ValueError
            if migrated_core or migrated_mode:
                self.save_settings(settings)
            return settings
        except (ValueError, TypeError) as exc:
            raise ClientError("Файл настроек повреждён. Восстановите settings.json.") from exc

    def save_settings(self, settings: Settings) -> None:
        atomic_write(self.root / "settings.json", json.dumps(asdict(settings)).encode())

    def load_profile(self) -> Profile | None:
        return self.load_library().active

    def load_library(self) -> ProfileLibrary:
        path = self.root / "profiles.dpapi"
        if path.exists():
            try:
                data = json.loads(protect(path.read_bytes(), decrypt=True))
                if not isinstance(data, dict) or data.get("schema") != 1:
                    raise ValueError
                profiles = [Profile(**item) for item in data["profiles"]]
                ids = [profile.id for profile in profiles]
                if len(ids) != len(set(ids)) or any(
                    not isinstance(key, str) or not key for key in ids
                ):
                    raise ValueError
                library = ProfileLibrary(profiles, data["active_id"])
                if (profiles and library.active is None) or (
                    not profiles and library.active_id is not None
                ):
                    raise ValueError
                return library
            except (ValueError, TypeError, KeyError) as exc:
                raise ClientError(
                    "Не удалось прочитать сохранённые подписки. Файл profiles.dpapi повреждён."
                ) from exc
        legacy = self._legacy_profile()
        library = ProfileLibrary([legacy], legacy.id) if legacy else ProfileLibrary()
        if legacy:
            self.save_library(library)
        return library

    def _legacy_profile(self) -> Profile | None:
        path = self.root / "profile.dpapi"
        if not path.exists():
            return None
        try:
            return Profile(**json.loads(protect(path.read_bytes(), decrypt=True)))
        except (ValueError, TypeError) as exc:
            raise ClientError(
                "Сохранённый профиль повреждён. Импортируйте подписку заново."
            ) from exc

    def save_profile(self, profile: Profile) -> None:
        library = self.load_library()
        self.save_library(library.upsert(profile))

    def save_library(self, library: ProfileLibrary) -> None:
        data = {
            "schema": 1,
            "profiles": [asdict(profile) for profile in library.profiles],
            "active_id": library.active_id,
        }
        atomic_write(
            self.root / "profiles.dpapi",
            protect(json.dumps(data, ensure_ascii=False).encode("utf-8")),
        )
        # Migration commits the complete encrypted library before removing the old file.
        try:
            (self.root / "profile.dpapi").unlink(missing_ok=True)
        except OSError:
            pass  # A committed library takes precedence over a read-only legacy file.

    def delete_profile(self) -> None:
        library = self.load_library()
        remaining = [item for item in library.profiles if item.id != library.active_id]
        self.save_library(ProfileLibrary(remaining, remaining[0].id if remaining else None))

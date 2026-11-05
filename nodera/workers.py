import threading

from PySide6.QtCore import QThread, Signal

from nodera.geolocation import transport_peers
from nodera.models import ClientError


class GeoTask(QThread):
    progress = Signal(str, object)
    message = Signal(str)

    def __init__(
        self, client, profile, names, choices, dns, parent=None, force=False, core_pid=None
    ):
        super().__init__(parent)
        self.client, self.profile, self.names = client, profile, names
        self.choices, self.dns, self.force = choices, dns, force
        self.cancelled = threading.Event()
        self.core_pid = core_pid

    def run(self):
        peers = transport_peers(self.core_pid)
        for name in self.names:
            if self.cancelled.is_set():
                break
            try:
                result = self.client.check_node(
                    self.profile, name, self.choices, self.dns, self.cancelled, self.force, peers
                )
                if not self.cancelled.is_set():
                    self.progress.emit(name, result)
            except ClientError as exc:
                self.message.emit(str(exc))
            except Exception:
                self.message.emit("Не удалось проверить IP узла.")

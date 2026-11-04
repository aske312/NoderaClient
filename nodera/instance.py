"""Graceful handover to a newly opened executable, scoped to the Windows user."""

import hashlib
import json
import time

from PySide6.QtCore import QObject, QTimer
from PySide6.QtNetwork import QLocalServer, QLocalSocket

from nodera.models import ClientError


def instance_name(root):
    digest = hashlib.sha256(str(root.resolve()).casefold().encode()).hexdigest()[:24]
    return "Nodera-" + digest


def request_restart(name):
    socket = QLocalSocket()
    socket.connectToServer(name)
    if not socket.waitForConnected(1500):
        return None
    socket.write(b"restart\n")
    socket.waitForBytesWritten(1500)
    if not socket.canReadLine() and not socket.waitForReadyRead(3000) and not socket.canReadLine():
        socket.abort()
        raise ClientError("Запущенная Nodera не ответила на запрос перезапуска.")
    raw = bytes(socket.readLine(256))
    socket.disconnectFromServer()
    try:
        response = json.loads(raw)
        if response.get("status") != "restarting":
            raise ValueError
        return response.get("reconnect") is True
    except (ValueError, AttributeError) as exc:
        raise ClientError("Перезапуск Nodera уже выполняется. Дождитесь открытия окна.") from exc


def acquire_instance(lock, name, timeout=45):
    if lock.tryLock(100):
        return False
    reconnect = request_restart(name)
    if reconnect is None:
        if lock.tryLock(100):
            return False
        raise ClientError(
            "Запущена старая версия Nodera без поддержки перезапуска. Закройте её через трей и откройте новую сборку."
        )
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if lock.tryLock(100):
            return reconnect
    raise ClientError("Предыдущий экземпляр Nodera ещё завершает работу. Повторите запуск позже.")


class RestartServer(QObject):
    def __init__(self, name, reconnect, shutdown, parent=None):
        super().__init__(parent)
        self.reconnect, self.shutdown = reconnect, shutdown
        self.restarting = False
        self.server = QLocalServer(self)
        self.server.setSocketOptions(QLocalServer.SocketOption.UserAccessOption)
        self.peers = set()
        self.server.newConnection.connect(self.accept)
        # Only the process owning the application lock constructs this server.
        QLocalServer.removeServer(name)
        if not self.server.listen(name):
            raise ClientError("Не удалось создать локальный канал перезапуска Nodera.")

    def accept(self):
        while self.server.hasPendingConnections():
            peer = self.server.nextPendingConnection()
            self.peers.add(peer)
            peer.readyRead.connect(lambda p=peer: self.receive(p))
            peer.disconnected.connect(lambda p=peer: self.discard(p))
            QTimer.singleShot(3000, lambda p=peer: p.abort() if p in self.peers else None)
            if peer.bytesAvailable():
                self.receive(peer)

    def discard(self, peer):
        self.peers.discard(peer)
        peer.deleteLater()

    def receive(self, peer):
        if peer.bytesAvailable() > 32:
            peer.abort()
            return
        if not peer.canReadLine():
            return
        if bytes(peer.readLine(32)) != b"restart\n":
            peer.abort()
            return
        response = {
            "status": "busy" if self.restarting else "restarting",
            "reconnect": bool(self.reconnect()),
        }
        peer.write(json.dumps(response).encode() + b"\n")
        peer.flush()
        peer.disconnectFromServer()
        if not self.restarting:
            self.restarting = True
            QTimer.singleShot(0, self.shutdown)

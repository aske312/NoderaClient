"""Connection snapshots supplement Mihomo's per-attempt stdout logs."""

import ntpath


class ConnectionTrace:
    def __init__(self):
        self.active = {}

    def update(self, connections):
        if not isinstance(connections, list):
            return []
        current, events = {}, []
        for item in connections:
            if not isinstance(item, dict) or not item.get("id"):
                continue
            metadata = item.get("metadata") or {}
            if not isinstance(metadata, dict):
                continue
            key = str(item["id"])
            upload, download = item.get("upload", 0), item.get("download", 0)
            if type(upload) is not int or type(download) is not int:
                continue
            description = (
                f"id={key} network={metadata.get('network', '-')} "
                f"inbound={metadata.get('type', '-')} process={ntpath.basename(str(metadata.get('processPath') or metadata.get('process') or '-'))} "
                f"source={metadata.get('sourceIP', '-')}:{metadata.get('sourcePort', '-')} "
                f"target={metadata.get('host') or metadata.get('destinationIP') or '-'}:{metadata.get('destinationPort', '-')} "
                f"ip={metadata.get('destinationIP', '-')} chain={item.get('chains', [])}"
            )
            state = (description, upload, download)
            previous = self.active.get(key)
            if previous is None:
                events.append(
                    f"event=connection_seen {description} upload={upload} download={download}"
                )
            if download > 0 and (previous is None or previous[2] == 0):
                events.append(
                    f"event=response_observed {description} upload={upload} download={download}"
                )
            current[key] = state
        for key, (description, upload, download) in self.active.items():
            if key not in current:
                events.append(
                    f"event=connection_not_observed {description} last_upload={upload} last_download={download}"
                )
        self.active = current
        return events

    def clear(self, reason):
        events = [
            f"event=connection_session_end {description} reason={reason} last_upload={upload} last_download={download}"
            for description, upload, download in self.active.values()
        ]
        self.active = {}
        return events

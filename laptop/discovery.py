"""
discovery.py — advertises the laptop on the LAN via mDNS (_syncbridge._tcp) so the phone
can find it again if the laptop's IP changes (DHCP). Optional: if `zeroconf` isn't installed
or the network blocks multicast, everything still works via the IP stored at pairing time.
The phone always re-verifies the pinned certificate fingerprint, so a spoofed mDNS reply
cannot impersonate the laptop.
"""
import socket


class Advertiser:
    def __init__(self):
        self._zc = None
        self._info = None

    def start(self, ip: str, port: int) -> bool:
        try:
            from zeroconf import ServiceInfo, Zeroconf
        except ImportError:
            return False
        try:
            name = socket.gethostname().split(".")[0] or "laptop"
            self._info = ServiceInfo(
                "_syncbridge._tcp.local.",
                f"SyncBridge-{name}._syncbridge._tcp.local.",
                addresses=[socket.inet_aton(ip)],
                port=port,
                properties={"v": "1"},
            )
            self._zc = Zeroconf()
            self._zc.register_service(self._info)
            return True
        except Exception:
            self.stop()
            return False

    def stop(self):
        try:
            if self._zc and self._info:
                self._zc.unregister_service(self._info)
            if self._zc:
                self._zc.close()
        except Exception:
            pass
        self._zc = self._info = None

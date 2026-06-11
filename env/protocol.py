"""Thin client for the Zenyte RL control socket (newline-delimited JSON; see server/CONTROL_PROTOCOL.md)."""
import json
import socket

DEFAULT_HOST = "127.0.0.1"
DEFAULT_PORT = 43500


class ControlClient:
    def __init__(self, host=DEFAULT_HOST, port=DEFAULT_PORT, timeout=30.0):
        self.host, self.port, self.timeout = host, port, timeout
        self._sock = None
        self._f = None
        self.connect()

    def connect(self):
        self.close()
        self._sock = socket.create_connection((self.host, self.port), timeout=self.timeout)
        self._f = self._sock.makefile("rwb")

    def _cmd(self, line):
        self._f.write((line + "\n").encode())
        self._f.flush()
        reply = self._f.readline()
        if not reply:
            raise ConnectionError("control socket closed by server")
        return json.loads(reply.decode())

    def ping(self):
        return self._cmd("ping")

    def reset(self, zulrah_hp=0):
        """zulrah_hp > 0 spawns Zulrah at that starting HP (curriculum); 0 leaves it at full."""
        return self._cmd("reset" if int(zulrah_hp) <= 0 else f"reset {int(zulrah_hp)}")

    def step(self, action):
        return self._cmd(f"step {int(action)}")

    def state(self):
        return self._cmd("state")

    def map(self):
        return self._cmd("map")

    def close(self):
        for c in (self._f, self._sock):
            try:
                if c is not None:
                    c.close()
            except OSError:
                pass
        self._f = self._sock = None

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        self.close()

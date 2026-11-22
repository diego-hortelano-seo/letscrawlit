"""The Windows program: data outside the program folder, and a port that never clashes."""
import socket

from app import config, desktop


def test_code_keeps_data_in_the_project():
    assert not config.FROZEN and config.USER_DIR == config.BASE_DIR


def test_pick_port_skips_a_busy_port(monkeypatch):
    busy = socket.socket()
    busy.bind((desktop.HOST, 0))
    busy.listen(1)
    taken = busy.getsockname()[1]
    try:
        monkeypatch.setattr(desktop, "FIRST_PORT", taken)
        port, running = desktop._pick_port()
        assert port != taken and not running
    finally:
        busy.close()

"""pytest plugin: inert unless OTARI_SWITCH_RECORD=1 or OTARI_SWITCH_OVERRIDE is set
(both are set only by otari-switch itself)."""

from otari_switch.record import install_from_env
from otari_switch.record.override import install_from_env as install_override


def pytest_configure(config):  # noqa: D401
    install_from_env()
    install_override()

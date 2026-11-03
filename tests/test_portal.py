"""The portal after logging straight back in. Run with: python3 -m unittest discover tests"""

import sys
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "daemon"))

from gi.repository import Gio, GLib  # noqa: E402

import portal  # noqa: E402


def states(front, gnome):
    table = {portal.FRONT: front, portal.GNOME: gnome}
    return lambda unit: table[unit]


class FakeBus:
    """Answers the color-scheme question the way it's told to."""

    def __init__(self, error=None):
        self.error = error
        self.asked = []

    def call(self, name, path, interface, method, params, reply, flags, timeout, cancellable, callback):
        self.asked.append((interface, method, params.unpack(), timeout))
        callback(self, None)

    def call_finish(self, _result):
        if self.error:
            raise self.error
        return GLib.Variant("(v)", (GLib.Variant("u", 1),))


def timed_out():
    return GLib.Error.new_literal(Gio.io_error_quark(), "Timeout was reached", Gio.IOErrorEnum.TIMED_OUT)


class Heal(unittest.TestCase):
    def setUp(self):
        self.log = mock.Mock()
        self.fixed = []
        self.fix = lambda log: self.fixed.append(log)

    def test_up_without_its_gnome_half_is_restarted_at_once(self):
        bus = FakeBus()
        portal.heal(bus, self.log, states(("loaded", "active"), ("loaded", "inactive")), self.fix)
        self.assertEqual(len(self.fixed), 1)
        self.assertEqual(bus.asked, [])

    def test_a_working_portal_is_left_alone(self):
        bus = FakeBus()
        portal.heal(bus, self.log, states(("loaded", "active"), ("loaded", "active")), self.fix)
        self.assertEqual(self.fixed, [])
        self.assertEqual(bus.asked[0][:3], ("org.freedesktop.portal.Settings", "ReadOne",
                                            ("org.freedesktop.appearance", "color-scheme")))

    def test_still_starting_is_not_stuck(self):
        bus = FakeBus()
        portal.heal(bus, self.log, states(("loaded", "active"), ("loaded", "activating")), self.fix)
        self.assertEqual(self.fixed, [])

    def test_no_answer_with_the_gnome_half_down_is_restarted(self):
        down = states(("loaded", "inactive"), ("loaded", "failed"))
        portal.heal(FakeBus(timed_out()), self.log, down, self.fix)
        self.assertEqual(len(self.fixed), 1)

    def test_a_slow_portal_whose_gnome_half_runs_is_left_alone(self):
        portal.heal(FakeBus(timed_out()), self.log, states(("loaded", "active"), ("loaded", "active")), self.fix)
        self.assertEqual(self.fixed, [])

    def test_no_portal_installed_is_left_alone(self):
        missing = GLib.Error.new_literal(Gio.dbus_error_quark(), "The name is not activatable", Gio.DBusError.SERVICE_UNKNOWN)
        portal.heal(FakeBus(missing), self.log, states(("not-found", "inactive"), ("not-found", "inactive")), self.fix)
        self.assertEqual(self.fixed, [])

    def test_without_systemd_nothing_happens(self):
        portal.heal(FakeBus(timed_out()), self.log, lambda unit: None, self.fix)
        self.assertEqual(self.fixed, [])


class UnitState(unittest.TestCase):
    def test_reads_systemctl(self):
        out = mock.Mock(stdout="LoadState=loaded\nActiveState=inactive\n")
        with mock.patch.object(portal.subprocess, "run", return_value=out) as run:
            self.assertEqual(portal.unit_state(portal.GNOME), ("loaded", "inactive"))
        self.assertIn("--user", run.call_args.args[0])

    def test_systemctl_missing(self):
        with mock.patch.object(portal.subprocess, "run", side_effect=OSError):
            self.assertIsNone(portal.unit_state(portal.GNOME))




class Restart(unittest.TestCase):
    def test_queues_the_restart_without_waiting(self):
        with mock.patch.object(portal.subprocess, "run") as run:
            portal.restart(mock.Mock())
        argv = run.call_args.args[0]
        self.assertEqual(argv[:4], ["systemctl", "--user", "--no-block", "restart"])
        self.assertIn(portal.GNOME, argv)
        self.assertIn(portal.FRONT, argv)

    def test_no_systemctl_is_only_logged(self):
        log = mock.Mock()
        with mock.patch.object(portal.subprocess, "run", side_effect=OSError("missing")):
            portal.restart(log)
        self.assertTrue(log.warning.called)


if __name__ == "__main__":
    unittest.main()

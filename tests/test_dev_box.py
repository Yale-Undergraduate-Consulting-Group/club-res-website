"""ops/dev_box.py starts the stopped dev box for a release and stops it afterwards.

A fake EC2/SSM records the commands, so these tests prove which state transitions
issue a start or stop, that transitional states are waited out, and that a stale
SSM "Online" from before the stop is not mistaken for this boot."""
from pathlib import Path
import sys
import unittest

sys.path.insert(0, str(Path(__file__).parents[1] / 'ops'))
import dev_box

BOX = 'i-0123456789abcdef0'
BOOT = '2026-11-17T12:00:00+00:00'


class FakeAws:
    def __init__(self, states, pings=(), status_states=('running',)):
        self.states, self.pings, self.status_states = list(states), list(pings), list(status_states)
        self.commands = []

    def __call__(self, *args):
        if args[:2] == ('ec2', 'describe-instances'):
            state = self.states.pop(0) if len(self.states) > 1 else self.states[0]
            return {'Reservations': [{'Instances': [{'State': {'Name': state}, 'LaunchTime': BOOT}]}]}
        if args[:2] == ('ec2', 'describe-instance-status'):
            state = self.status_states.pop(0) if len(self.status_states) > 1 else self.status_states[0]
            return {'InstanceStatuses': [] if state is None else [{'InstanceState': {'Name': state}}]}
        if args[:2] == ('ssm', 'describe-instance-information'):
            ping = self.pings.pop(0) if len(self.pings) > 1 else self.pings[0]
            return {'InstanceInformationList': [ping] if ping else []}
        self.commands.append(args[:2])
        return {}


def online(at):
    return {'PingStatus': 'Online', 'LastPingDateTime': at}


class DevBoxTests(unittest.TestCase):
    def run_start(self, fake):
        dev_box.start(BOX, call=fake, sleep=lambda _: None)

    def test_start_boots_a_stopped_box_and_waits_for_ssm_from_this_boot(self):
        stale = online('2026-11-17T11:59:00+00:00')
        fake = FakeAws(['stopped', 'pending', 'running'], [None, stale, online('2026-11-17T12:00:40.5+00:00')])
        self.run_start(fake)
        self.assertEqual(fake.commands, [('ec2', 'start-instances')])
        self.assertEqual(fake.pings, [online('2026-11-17T12:00:40.5+00:00')])

    def test_start_waits_for_ec2_status_before_checking_ssm(self):
        fake = FakeAws(['running'], [online('2026-11-17T12:01:00+00:00')],
                       status_states=[None, 'pending', 'running'])
        self.run_start(fake)
        self.assertEqual(fake.pings, [online('2026-11-17T12:01:00+00:00')])

    def test_start_waits_out_a_stop_in_progress_before_starting(self):
        fake = FakeAws(['stopping', 'stopping', 'stopped', 'running'], [online('2026-11-17T12:01:00+00:00')])
        self.run_start(fake)
        self.assertEqual(fake.commands, [('ec2', 'start-instances')])

    def test_start_leaves_a_running_box_alone(self):
        fake = FakeAws(['running'], [online('2026-11-17T12:05:00+00:00')])
        self.run_start(fake)
        self.assertEqual(fake.commands, [])

    def test_start_fails_when_ssm_never_reports_this_boot(self):
        fake = FakeAws(['running'], [{'PingStatus': 'ConnectionLost', 'LastPingDateTime': BOOT}])
        with self.assertRaisesRegex(SystemExit, 'SSM agent'):
            self.run_start(fake)

    def test_terminated_box_is_an_error_not_a_start(self):
        fake = FakeAws(['terminated'])
        with self.assertRaisesRegex(SystemExit, 'terminated'):
            self.run_start(fake)
        self.assertEqual(fake.commands, [])

    def test_stop_waits_for_a_booting_box_then_stops_it_once(self):
        fake = FakeAws(['pending', 'running', 'stopping', 'stopping', 'stopped'])
        dev_box.stop(BOX, call=fake, sleep=lambda _: None)
        self.assertEqual(fake.commands, [('ec2', 'stop-instances')])

    def test_stop_of_a_stopped_box_does_nothing(self):
        fake = FakeAws(['stopped'])
        dev_box.stop(BOX, call=fake, sleep=lambda _: None)
        self.assertEqual(fake.commands, [])

    def test_stop_reports_a_box_that_never_stops(self):
        fake = FakeAws(['stopping'])
        with self.assertRaisesRegex(SystemExit, 'did not become stopped'):
            dev_box.stop(BOX, call=fake, sleep=lambda _: None)


if __name__ == '__main__':
    unittest.main()

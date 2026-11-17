"""Start the dev box for a release and stop it afterwards (Dev workflow deploy job).

Dev is stopped between releases. `start` brings BOX_INSTANCE_ID to running and
waits until its SSM agent has checked in since this boot, so the deploy's
SendCommand reaches it. `stop` brings it back to stopped. Both converge from any
transitional state, so a rerun, or a box left half-way by a cancelled job, is fine.
The deploy role may start and stop only the dev box (terraform/github.tf).
"""
from datetime import datetime
import json
import os
import subprocess
import sys
import time

POLL_SECONDS = 5
# target state -> (state from which to act, EC2 command)
ACTIONS = {'running': ('stopped', 'start-instances'), 'stopped': ('running', 'stop-instances')}


def aws(*args):
    return json.loads(subprocess.check_output(['aws', *args, '--output', 'json'], text=True))


def converge(instance_id, target, attempts, call=aws, sleep=time.sleep):
    """Drive the instance to `target`, waiting out pending/stopping; return its description."""
    source, command = ACTIONS[target]
    for _ in range(attempts):
        instance = call('ec2', 'describe-instances', '--instance-ids', instance_id)['Reservations'][0]['Instances'][0]
        state = instance['State']['Name']
        if state == target:
            return instance
        if state == source:
            call('ec2', command, '--instance-ids', instance_id)
        elif state not in ('pending', 'stopping'):
            raise SystemExit(f'{instance_id} is {state}; it cannot become {target}')
        sleep(POLL_SECONDS)
    raise SystemExit(f'{instance_id} did not become {target} within {attempts * POLL_SECONDS} s')


def wait_for_ssm(instance_id, launched, attempts, call=aws, sleep=time.sleep):
    """SSM keeps reporting Online for a while after a stop, so require a ping from this boot."""
    for _ in range(attempts):
        found = call('ssm', 'describe-instance-information',
                     '--filters', f'Key=InstanceIds,Values={instance_id}')['InstanceInformationList']
        if found and found[0].get('PingStatus') == 'Online' \
                and datetime.fromisoformat(found[0]['LastPingDateTime']) >= launched:
            return
        sleep(POLL_SECONDS)
    raise SystemExit(f'{instance_id} is running, but its SSM agent was not online within {attempts * POLL_SECONDS} s')


def start(instance_id, call=aws, sleep=time.sleep):
    instance = converge(instance_id, 'running', attempts=60, call=call, sleep=sleep)
    wait_for_ssm(instance_id, datetime.fromisoformat(instance['LaunchTime']), attempts=60, call=call, sleep=sleep)


def stop(instance_id, call=aws, sleep=time.sleep):
    converge(instance_id, 'stopped', attempts=120, call=call, sleep=sleep)


if __name__ == '__main__':
    if os.environ.get('DEPLOYMENT_ENV') != 'dev':
        raise SystemExit('Only the dev box is started and stopped per release')
    command, instance_id = sys.argv[1], os.environ['BOX_INSTANCE_ID']
    {'start': start, 'stop': stop}[command](instance_id)
    print(f'{instance_id} is {"running and online in SSM" if command == "start" else "stopped"}.')

"""Read-only AWS deployment preflight. Does not send SSM commands or invoke models.

Selects the box by its Site tag, requires it to be exactly BOX_INSTANCE_ID, and
checks the security posture the deployment relies on.
"""
import json
import os
import subprocess


def aws(*args):
    return json.loads(subprocess.check_output(['aws', *args, '--output', 'json'], text=True))


def validate(instance, groups, volumes, edge):
    problems = []
    if instance.get('State', {}).get('Name') != 'running':
        problems.append('Target instance is not running; start it before deploying')
    if instance.get('MetadataOptions', {}).get('HttpTokens') != 'required':
        problems.append('IMDSv2 is required')
    for group in groups:
        for rule in group.get('IpPermissions', []):
            if rule.get('IpRanges') or rule.get('Ipv6Ranges'):
                problems.append(f'Security group {group.get("GroupId")} permits direct CIDR ingress')
            elif not edge:
                problems.append(f'Security group {group.get("GroupId")} permits ingress without an edge')
            elif (rule.get('IpProtocol'), rule.get('FromPort'), rule.get('ToPort')) != ('tcp', 80, 80):
                problems.append(f'Security group {group.get("GroupId")} permits ingress other than tcp/80')
    if not volumes or any(v.get('Encrypted') is not True for v in volumes):
        problems.append('All instance volumes must be encrypted')
    return problems


if __name__ == '__main__':
    environment = os.environ['DEPLOYMENT_ENV']
    expected = os.environ['BOX_INSTANCE_ID']
    reservations = aws('ec2', 'describe-instances', '--filters',
                       f'Name=tag:Site,Values=club-res-website-{environment}',
                       'Name=instance-state-name,Values=pending,running,stopping,stopped')['Reservations']
    instances = [i for r in reservations for i in r['Instances']]
    if [i['InstanceId'] for i in instances] != [expected]:
        raise SystemExit(f'Expected exactly {expected} tagged Site=club-res-website-{environment}; '
                         f'found {sorted(i["InstanceId"] for i in instances)}')
    instance = instances[0]
    groups = aws('ec2', 'describe-security-groups', '--group-ids',
                 *[g['GroupId'] for g in instance['SecurityGroups']])['SecurityGroups']
    volumes = aws('ec2', 'describe-volumes', '--volume-ids',
                  *[b['Ebs']['VolumeId'] for b in instance['BlockDeviceMappings'] if 'Ebs' in b])['Volumes']
    problems = validate(instance, groups, volumes, edge=environment == 'prod')
    if problems:
        raise SystemExit('; '.join(problems))
    print(f'{expected} is the only {environment} box: running, IMDSv2 required, encrypted volumes, no direct public ingress.')

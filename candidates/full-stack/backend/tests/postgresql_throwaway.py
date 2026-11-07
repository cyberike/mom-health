"""Throwaway local PostgreSQL for the opt-in parity checks; not a deployment tool.

Creates a brand-new cluster with initdb in ``build/postgresql-fixtures/<id>/``,
serves it only on a Unix-domain socket in an owner-only directory there (no TCP
listener: listen_addresses is empty), creates one fictional non-superuser role
and one database, runs the given command with MOM_HEALTH_TEST_POSTGRESQL_DSN
and MOM_HEALTH_TEST_POSTGRESQL_CLUSTER set, then stops the server and checks
that it exited. Socket authentication is ``trust``: only the server account
(and root) can reach the socket, and no password or other credential exists.
Fixtures are retained for inspection unless --discard is given; nothing outside
the new cluster directory is created, changed or removed.

PostgreSQL refuses to run as root, so a root caller names an unprivileged
account with --run-as. From candidates/full-stack/backend:

    python tests/postgresql_throwaway.py --run-as postgres -- \\
        python -m unittest tests.postgresql_parity -v

--locale icu-en-US creates the database with a linguistic ICU collation
instead of byte order ("C"). The restart checks call control() below.
"""
import argparse
import json
import os
from pathlib import Path
import pwd
import shutil
import subprocess
import sys
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parents[2] / 'build' / 'postgresql-fixtures'
# Fictional names; trust authentication on a private socket needs no secret.
ADMIN, ROLE, DATABASE = 'synthetic_admin', 'synthetic_app', 'mom_health_synthetic_parity'
LOCALES = {'c': "LOCALE 'C'", 'icu-en-US': "LOCALE 'C' LOCALE_PROVIDER icu ICU_LOCALE 'en-US'"}


def find_bindir(explicit=None):
    if explicit:
        return Path(explicit).resolve()
    on_path = shutil.which('pg_ctl')
    if on_path:
        return Path(on_path).resolve().parent
    installed = sorted(Path('/usr/lib/postgresql').glob('*/bin/pg_ctl'), key=lambda p: p.parent.parent.name.zfill(4))
    if installed:
        return installed[-1].parent
    raise SystemExit('PostgreSQL server binaries (initdb, pg_ctl) not found; pass --bindir')


def describe(cluster):
    cluster = Path(cluster).resolve()
    if cluster.parent != FIXTURES.resolve():
        raise SystemExit('Refusing a cluster outside ' + str(FIXTURES))
    return json.loads((cluster / 'cluster.json').read_text())


def as_account(command, account):
    return ['runuser', '-u', account, '--'] + command if account else command


def run(command):
    result = subprocess.run(command, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True)
    if result.returncode:
        raise RuntimeError('Command failed (%d): %s\n%s' % (result.returncode, ' '.join(command), result.stdout))
    return result.stdout


def pg_ctl(info, *arguments):
    return as_account([str(Path(info['bindir']) / 'pg_ctl'), '-D', info['data']] + list(arguments), info['run_as'])


def control(cluster, action, mode='fast'):
    """Stop, start or restart the throwaway server. mode=immediate is a crash stop."""
    info = describe(cluster)
    if action in ('stop', 'restart'):
        run(pg_ctl(info, '-m', mode, '-w', '-t', '60', 'stop'))
    if action in ('start', 'restart'):
        run(pg_ctl(info, '-l', info['log'], '-w', '-t', '60', 'start'))


def running(info):
    return subprocess.run(pg_ctl(info, 'status'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL).returncode == 0


def leftover_processes(info):
    """PIDs whose command line names this cluster's data directory (Linux /proc)."""
    found = []
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if info['data'].encode() in (entry / 'cmdline').read_bytes():
                found.append(int(entry.name))
        except OSError:
            pass
    return found


def create(cluster, bindir, run_as, locale):
    sockets = cluster / 's'
    if len(str(sockets / '.s.PGSQL.5432').encode()) > 107:
        raise SystemExit('Socket path too long for this checkout location: ' + str(sockets))
    sockets.mkdir(parents=True)
    for path in (cluster, sockets):
        if run_as:
            account = pwd.getpwnam(run_as)
            os.chown(path, account.pw_uid, account.pw_gid)
        path.chmod(0o700)
    info = dict(bindir=str(bindir), data=str(cluster / 'data'), sockets=str(sockets), log=str(cluster / 'server.log'), run_as=run_as)
    (cluster / 'cluster.json').write_text(json.dumps(info, indent=1) + '\n')
    run(as_account([str(bindir / 'initdb'), '-D', info['data'], '-U', ADMIN, '--auth-local=trust', '--auth-host=reject',
                    '-E', 'UTF8', '--locale=C', '--no-instructions'], run_as))
    with open(Path(info['data']) / 'postgresql.conf', 'a') as conf:
        conf.write("\n# Throwaway parity cluster: Unix socket only, owner-only, durable commits.\n"
                   "listen_addresses = ''\nunix_socket_directories = '%s'\nunix_socket_permissions = 0700\n"
                   "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\nmax_connections = 100\n" % sockets)
    return info


def main():
    parser = argparse.ArgumentParser(description='Run a command against a throwaway local PostgreSQL cluster.')
    parser.add_argument('--run-as', help='unprivileged account for the server (required as root)')
    parser.add_argument('--bindir', help='directory with initdb, pg_ctl and psql')
    parser.add_argument('--locale', choices=sorted(LOCALES), default='c')
    parser.add_argument('--discard', action='store_true', help='delete the cluster directory after a verified stop')
    parser.add_argument('command', nargs=argparse.REMAINDER)
    args = parser.parse_args()
    command = args.command[1:] if args.command[:1] == ['--'] else args.command
    if not command:
        parser.error('give the command to run after --')
    if os.geteuid() == 0 and not args.run_as:
        parser.error('PostgreSQL refuses to run as root; pass --run-as <unprivileged account>')
    if args.run_as and os.geteuid() != 0:
        parser.error('--run-as needs root (runuser); omit it to run as the current account')
    bindir = find_bindir(args.bindir)
    cluster = FIXTURES / uuid4().hex[:12]
    info, status = None, 1
    try:
        info = create(cluster, bindir, args.run_as, args.locale)
        control(cluster, 'start')
        run([str(bindir / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', info['sockets'], '-U', ADMIN, '-d', 'postgres',
             '-c', 'CREATE ROLE %s LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' % ROLE,
             '-c', "CREATE DATABASE %s OWNER %s TEMPLATE template0 ENCODING 'UTF8' %s" % (DATABASE, ROLE, LOCALES[args.locale])])
        dsn = 'host=%s dbname=%s user=%s' % (info['sockets'], DATABASE, ROLE)
        print('Throwaway PostgreSQL (%s, locale %s, socket only): %s' % (bindir, args.locale, cluster), file=sys.stderr, flush=True)
        environment = dict(os.environ, MOM_HEALTH_TEST_POSTGRESQL_DSN=dsn, MOM_HEALTH_TEST_POSTGRESQL_CLUSTER=str(cluster))
        status = subprocess.run(command, env=environment).returncode
    finally:
        if info is not None:
            if running(info):
                control(cluster, 'stop')
            leftovers = leftover_processes(info)
            stopped = not running(info) and not (Path(info['data']) / 'postmaster.pid').exists() and not leftovers
            print('Throwaway PostgreSQL stopped and exited: %s%s' % ('yes' if stopped else 'NO', ' (pids %s)' % leftovers if leftovers else ''),
                  file=sys.stderr, flush=True)
            if not stopped:
                status = status or 1
            elif args.discard:
                shutil.rmtree(cluster)
                print('Discarded ' + str(cluster), file=sys.stderr, flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())

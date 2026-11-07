"""Throwaway local PostgreSQL for the opt-in parity checks; not a deployment tool.

Creates a brand-new cluster with initdb in ``build/postgresql-fixtures/<id>/``,
serves it only on a Unix-domain socket in an owner-only directory (no TCP
listener: listen_addresses is empty), creates one fictional non-superuser role
and one database, and runs the given command with MOM_HEALTH_TEST_POSTGRESQL_DSN
and MOM_HEALTH_TEST_POSTGRESQL_CLUSTER set. Every libpq ``PG*`` variable is
removed from the environment of the launcher's own clients and of the command,
so no client can be redirected off the private socket. Afterwards it stops the
server and checks that it exited, also when the launcher is interrupted or
terminated (SIGINT, SIGTERM or SIGHUP; a command still running is stopped
first). Socket authentication is ``trust``: only the server account (and root)
can reach the socket, and no password or other credential exists. Fixtures are
retained for inspection unless --discard is given. Nothing outside the new
cluster directory is created, changed or removed, except a short private socket
directory in the system temporary directory when the checkout path is too long
for a Unix socket; that directory is removed once the server has stopped.

Needs Linux (it reads /proc to confirm the server exited). PostgreSQL refuses to
run as root, so a root caller names an unprivileged account with --run-as, which
uses runuser. The cluster directory and its cluster.json then stay root-owned;
only data/, log/ and the socket directory belong to that account, so it cannot
change what root later runs. From candidates/full-stack/backend:

    python tests/postgresql_throwaway.py --run-as postgres -- \\
        python -m unittest tests.postgresql_parity -v

--locale icu-en-US creates the database with a linguistic ICU collation
instead of byte order ("C"). The crash check calls control() below.
"""
import argparse
import json
import os
from pathlib import Path
import re
import shutil
import signal
import subprocess
import sys
import tempfile
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parents[2] / 'build' / 'postgresql-fixtures'
# Fictional names; trust authentication on a private socket needs no secret.
ADMIN, ROLE, DATABASE = 'synthetic_admin', 'synthetic_app', 'mom_health_synthetic_parity'
LOCALES = {'c': "LOCALE 'C'", 'icu-en-US': "LOCALE 'C' LOCALE_PROVIDER icu ICU_LOCALE 'en-US'"}
SOCKET_PATH_LIMIT = 107  # bytes, including the ".s.PGSQL.5432" file name


def find_bindir(explicit=None, root='/usr/lib/postgresql'):
    if explicit:
        return Path(explicit).resolve()
    on_path = shutil.which('pg_ctl')
    if on_path:
        return Path(on_path).resolve().parent
    # Highest version number wins, so 16 is chosen over 9.6.
    installed = sorted(Path(root).glob('*/bin/pg_ctl'),
                       key=lambda p: tuple(int(part) for part in re.findall(r'\d+', p.parent.parent.name)))
    if installed:
        return installed[-1].parent
    raise SystemExit('PostgreSQL server binaries (initdb, pg_ctl) not found; pass --bindir')


def private_environment(**extra):
    """The caller's environment without libpq's PG* variables (PGHOSTADDR, PGSERVICE, ...)."""
    environment = {name: value for name, value in os.environ.items() if not name.startswith('PG')}
    environment.update(extra)
    return environment


def describe(cluster):
    cluster = Path(cluster).resolve()
    if cluster.parent != FIXTURES.resolve():
        raise SystemExit('Refusing a cluster outside ' + str(FIXTURES))
    return json.loads((cluster / 'cluster.json').read_text())


def as_account(command, account):
    return ['runuser', '-u', account, '--'] + command if account else command


def run(command, stdin=None):
    result = subprocess.run(command, input=stdin, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True,
                            env=private_environment())
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
    return subprocess.run(pg_ctl(info, 'status'), stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                          env=private_environment()).returncode == 0


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
    owner = None
    if run_as:
        import pwd  # POSIX only; imported here so the module itself imports anywhere
        account = pwd.getpwnam(run_as)
        owner = (account.pw_uid, account.pw_gid)
    cluster.mkdir(parents=True)
    cluster.chmod(0o755 if owner else 0o700)
    sockets = cluster / 's'
    if len(str(sockets / '.s.PGSQL.5432').encode()) > SOCKET_PATH_LIMIT:
        sockets = Path(tempfile.mkdtemp(prefix='mhpg-'))  # deep checkout: short private directory instead
    # Written before anything else, so a failure part-way can still be cleaned up.
    info = dict(bindir=str(bindir), data=str(cluster / 'data'), sockets=str(sockets), log=str(cluster / 'log' / 'server.log'),
                run_as=run_as)
    (cluster / 'cluster.json').write_text(json.dumps(info, indent=1) + '\n')
    for path in (cluster / 'data', cluster / 'log', sockets):
        path.mkdir(exist_ok=True)
        if owner:
            os.chown(path, *owner)
        path.chmod(0o700)
    run(as_account([str(bindir / 'initdb'), '-D', info['data'], '-U', ADMIN, '--auth-local=trust', '--auth-host=reject',
                    '-E', 'UTF8', '--locale=C', '--no-instructions'], run_as))
    # The server account appends its own settings, so root never writes inside its directories.
    run(as_account(['tee', '-a', str(Path(info['data']) / 'postgresql.conf')], run_as), stdin=(
        "\n# Throwaway parity cluster: Unix socket only, owner-only, durable commits.\n"
        "listen_addresses = ''\nunix_socket_directories = '%s'\nunix_socket_permissions = 0700\n"
        "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\nmax_connections = 100\n" % sockets))
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
    signals = [getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name)]

    def interrupted(signum, frame):
        raise KeyboardInterrupt(signum)
    for signum in signals[1:]:  # SIGINT already raises KeyboardInterrupt
        signal.signal(signum, interrupted)
    info, child, status = None, None, 1
    try:
        info = create(cluster, bindir, args.run_as, args.locale)
        control(cluster, 'start')
        run([str(bindir / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', info['sockets'], '-U', ADMIN, '-d', 'postgres',
             '-c', 'CREATE ROLE %s LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' % ROLE,
             '-c', "CREATE DATABASE %s OWNER %s TEMPLATE template0 ENCODING 'UTF8' %s" % (DATABASE, ROLE, LOCALES[args.locale])])
        dsn = 'host=%s dbname=%s user=%s' % (info['sockets'], DATABASE, ROLE)
        print('Throwaway PostgreSQL (%s, locale %s, socket only): %s' % (bindir, args.locale, cluster), file=sys.stderr, flush=True)
        child = subprocess.Popen(command, env=private_environment(MOM_HEALTH_TEST_POSTGRESQL_DSN=dsn, MOM_HEALTH_TEST_POSTGRESQL_CLUSTER=str(cluster)))
        status = child.wait()
    except KeyboardInterrupt as stop:
        signum = int(stop.args[0]) if stop.args else int(signal.SIGINT)
        status = 128 + signum
        print('Interrupted by signal %d; stopping the throwaway server.' % signum, file=sys.stderr, flush=True)
    finally:
        for signum in signals:  # finish the cleanup even if signalled again
            signal.signal(signum, signal.SIG_IGN)
        if child is not None and child.poll() is None:
            child.terminate()
            try:
                child.wait(30)
            except subprocess.TimeoutExpired:
                child.kill()
                child.wait()
        if info is None and (cluster / 'cluster.json').exists():
            info = describe(cluster)  # create() failed part-way; the server was never started
        stopped = True
        if info is not None:
            if running(info):
                control(cluster, 'stop')
            leftovers = leftover_processes(info)
            stopped = not running(info) and not (Path(info['data']) / 'postmaster.pid').exists() and not leftovers
            print('Throwaway PostgreSQL stopped and exited: %s%s' % ('yes' if stopped else 'NO', ' (pids %s)' % leftovers if leftovers else ''),
                  file=sys.stderr, flush=True)
            if stopped and Path(info['sockets']).parent != cluster:
                shutil.rmtree(info['sockets'], ignore_errors=True)
        if not stopped:
            status = status or 1
        elif args.discard and cluster.exists():
            shutil.rmtree(cluster)
            print('Discarded ' + str(cluster), file=sys.stderr, flush=True)
    return status


if __name__ == '__main__':
    sys.exit(main())

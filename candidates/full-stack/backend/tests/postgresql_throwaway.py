"""Throwaway local PostgreSQL for the opt-in parity checks; not a deployment tool.

Creates a brand-new cluster with initdb in ``build/postgresql-fixtures/<id>/``,
serves it only on a Unix-domain socket in an owner-only directory (no TCP
listener: listen_addresses is empty), creates one fictional non-superuser role
and one database, and runs the given command with MOM_HEALTH_TEST_POSTGRESQL_DSN
and MOM_HEALTH_TEST_POSTGRESQL_CLUSTER set. Every libpq ``PG*`` variable is
removed from the environment of the launcher's own clients and of the command,
so no client can be redirected off the private socket.

Afterwards it stops the server and checks that it exited, also when the
launcher is interrupted or terminated (SIGINT, SIGTERM or SIGHUP). A setup step
still running at that moment (initdb, pg_ctl, psql) may finish first, so none of
them is left running on its own; a command still running is sent SIGTERM. Only
the command's own process is signalled: programs it started stop too only when
the signal went to the whole process group (Ctrl-C, timeout, a closed
terminal). Further signals are ignored until the cleanup is done, and the
cleanup's server commands run in their own session, out of reach of a repeated
group signal. A signal already ignored when the launcher starts (under nohup,
for example) stays ignored. Status lines go to stderr; a closed terminal or a
broken pipe does not stop the cleanup. If the server does not start, the end of
its log is included in the error, because --discard deletes the log.

Socket authentication is ``trust``: only the server account (and root) can
reach the socket, and no password or other credential exists. Fixtures are
retained for inspection unless --discard is given. Nothing outside the new
cluster directory is created, changed or removed, except a private socket
directory under /tmp when the checkout path is too long for a Unix socket; that
directory is named on stderr and removed once the server has stopped.

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
import time
from uuid import uuid4

FIXTURES = Path(__file__).resolve().parents[2] / 'build' / 'postgresql-fixtures'
# Fictional names; trust authentication on a private socket needs no secret.
ADMIN, ROLE, DATABASE = 'synthetic_admin', 'synthetic_app', 'mom_health_synthetic_parity'
LOCALES = {'c': "LOCALE 'C'", 'icu-en-US': "LOCALE 'C' LOCALE_PROVIDER icu ICU_LOCALE 'en-US'"}
SOCKET_PATH_LIMIT = 107  # bytes, including the ".s.PGSQL.5432" file name
# Not TMPDIR: that can be long, or private to root (pam_tmpdir), and the server account must reach the socket.
SHORT_SOCKET_BASE = '/tmp'
SIGNALS = tuple(getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name))
STEP_GRACE = 120  # seconds an interrupted setup step may take to finish before it is killed
STOP_GRACE = 60  # seconds the cleanup waits for every process naming the cluster to exit


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


def say(message):
    """A status line on stderr. A closed terminal (EIO) or a broken pipe (EPIPE) must not cut the cleanup short."""
    try:
        print(message, file=sys.stderr, flush=True)
    except OSError:
        pass


def ignore_signals():
    for signum in SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def describe(cluster):
    cluster = Path(cluster).resolve()
    if cluster.parent != FIXTURES.resolve():
        raise SystemExit('Refusing a cluster outside ' + str(FIXTURES))
    return json.loads((cluster / 'cluster.json').read_text())


def as_account(command, account):
    return ['runuser', '-u', account, '--'] + command if account else command


def run(command, stdin=None, detached=False):
    """Run one step and return its output. If the launcher is signalled meanwhile, the step may still
    finish (killed after STEP_GRACE seconds), so a signal sent to the launcher alone cannot leave an
    initdb or pg_ctl running on its own. detached=True starts it in its own session, so signals sent
    to the launcher's process group do not reach it."""
    process = subprocess.Popen(command, stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                               stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=private_environment(),
                               start_new_session=detached)
    try:
        output, _ = process.communicate(stdin)
    except KeyboardInterrupt:
        try:
            if process.stdin:
                process.stdin.close()  # input cut short: end it, so the step cannot wait for more
        except OSError:
            pass
        try:
            process.communicate(timeout=STEP_GRACE)
        except subprocess.TimeoutExpired:
            process.kill()
            process.communicate()
        raise
    if process.returncode:
        raise RuntimeError('Command failed (%d): %s\n%s' % (process.returncode, ' '.join(command), output))
    return output


def pg_ctl(info, *arguments):
    return as_account([str(Path(info['bindir']) / 'pg_ctl'), '-D', info['data']] + list(arguments), info['run_as'])


def log_tail(path, lines=20):
    try:
        return ''.join(Path(path).read_text(errors='replace').splitlines(keepends=True)[-lines:])
    except OSError as error:
        return '(not readable: %s)\n' % error


def control(cluster, action, mode='fast'):
    """Stop, start or restart the throwaway server. mode=immediate is a crash stop."""
    info = describe(cluster)
    if action in ('stop', 'restart'):
        run(pg_ctl(info, '-m', mode, '-w', '-t', '60', 'stop'))
    if action in ('start', 'restart'):
        try:
            run(pg_ctl(info, '-l', info['log'], '-w', '-t', '60', 'start'))
        except RuntimeError as failure:
            raise RuntimeError('%s\nEnd of the server log %s:\n%s' % (failure, info['log'], log_tail(info['log']))) from None


def running(info):
    try:
        return subprocess.run(pg_ctl(info, 'status'), stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                              stderr=subprocess.DEVNULL, env=private_environment(), start_new_session=True).returncode == 0
    except OSError:  # no pg_ctl there (a wrong --bindir), so no server was started from it
        return False


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


def stop_server(info):
    """Stop the server if one runs, then wait up to STOP_GRACE seconds until no process names the
    cluster and none is reported running. Returns the PIDs still left. A failed stop (pg_ctl refuses a
    single-user backend that initdb has not finished with, for example) is reported and retried."""
    deadline, reported = time.monotonic() + STOP_GRACE, False
    while True:
        try:
            if running(info):
                run(pg_ctl(info, '-m', 'fast', '-w', '-t', '60', 'stop'), detached=True)
        except RuntimeError as failure:
            if not reported:
                say(str(failure).strip())
                reported = True
        leftovers = leftover_processes(info)
        if (not leftovers and not running(info)) or time.monotonic() >= deadline:
            return leftovers
        time.sleep(0.5)


def create(cluster, bindir, run_as, locale):
    owner = None
    if run_as:
        import pwd  # POSIX only; imported here so the module itself imports anywhere
        account = pwd.getpwnam(run_as)
        owner = (account.pw_uid, account.pw_gid)
    cluster.mkdir(parents=True)
    cluster.chmod(0o755 if owner else 0o700)
    info = dict(bindir=str(bindir), data=str(cluster / 'data'), sockets=str(cluster / 's'), log=str(cluster / 'log' / 'server.log'),
                run_as=run_as)
    # A deep checkout gets a short private socket directory instead. Signals wait until cluster.json
    # names it, so an interrupted launcher can always find and remove it.
    held = signal.pthread_sigmask(signal.SIG_BLOCK, SIGNALS)
    try:
        if len(os.path.join(info['sockets'], '.s.PGSQL.5432').encode()) > SOCKET_PATH_LIMIT:
            info['sockets'] = tempfile.mkdtemp(prefix='mhpg-', dir=SHORT_SOCKET_BASE)
        try:
            # Written before anything else, so a failure part-way can still be cleaned up.
            (cluster / 'cluster.json').write_text(json.dumps(info, indent=1) + '\n')
        except BaseException:
            if Path(info['sockets']).parent != cluster:
                os.rmdir(info['sockets'])
            raise
    finally:
        signal.pthread_sigmask(signal.SIG_SETMASK, held)
    if Path(info['sockets']).parent != cluster:
        say('Throwaway PostgreSQL socket directory (the checkout path is too long for a socket): ' + info['sockets'])
    for path in (cluster / 'data', cluster / 'log', Path(info['sockets'])):
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
        "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\nmax_connections = 100\n" % info['sockets']))
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

    def interrupted(signum, frame):
        ignore_signals()  # finish the cleanup even if signalled again
        raise KeyboardInterrupt(signum)
    for signum in SIGNALS:
        if signal.getsignal(signum) is not signal.SIG_IGN:  # left ignored (nohup, a background job), as Python does
            signal.signal(signum, interrupted)
    info, child, status = None, None, 1
    try:
        try:
            info = create(cluster, bindir, args.run_as, args.locale)
            control(cluster, 'start')
            run([str(bindir / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', info['sockets'], '-U', ADMIN, '-d', 'postgres',
                 '-c', 'CREATE ROLE %s LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' % ROLE,
                 '-c', "CREATE DATABASE %s OWNER %s TEMPLATE template0 ENCODING 'UTF8' %s" % (DATABASE, ROLE, LOCALES[args.locale])])
            dsn = 'host=%s dbname=%s user=%s' % (info['sockets'], DATABASE, ROLE)
            say('Throwaway PostgreSQL (%s, locale %s, socket only): %s' % (bindir, args.locale, cluster))
            child = subprocess.Popen(command, env=private_environment(MOM_HEALTH_TEST_POSTGRESQL_DSN=dsn, MOM_HEALTH_TEST_POSTGRESQL_CLUSTER=str(cluster)))
            status = child.wait()
        finally:
            ignore_signals()  # still inside the outer try: a signal arriving just before this is handled below
    except KeyboardInterrupt as stop:
        signum = int(stop.args[0]) if stop.args else int(signal.SIGINT)
        status = 128 + signum
        say('Interrupted by signal %d; stopping the throwaway server.' % signum)
    finally:
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
            leftovers = stop_server(info)
            stopped = not leftovers and not running(info) and not (Path(info['data']) / 'postmaster.pid').exists()
            say('Throwaway PostgreSQL stopped and exited: %s%s' % ('yes' if stopped else 'NO', ' (pids %s)' % leftovers if leftovers else ''))
            if stopped and Path(info['sockets']).parent != cluster:
                shutil.rmtree(info['sockets'], ignore_errors=True)
        if not stopped:
            status = status or 1
        elif args.discard and cluster.exists():
            shutil.rmtree(cluster)
            say('Discarded ' + str(cluster))
    return status


if __name__ == '__main__':
    sys.exit(main())

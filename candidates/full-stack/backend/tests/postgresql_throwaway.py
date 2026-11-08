"""Throwaway local PostgreSQL for the opt-in parity checks; not a deployment tool.

Creates a brand-new cluster with initdb in ``build/postgresql-fixtures/<id>/``,
serves it only on a Unix-domain socket in an owner-only directory (no TCP
listener: listen_addresses is empty), creates one fictional non-superuser role
and one database, and runs the given command with MOM_HEALTH_TEST_POSTGRESQL_DSN
and MOM_HEALTH_TEST_POSTGRESQL_CLUSTER set. Every libpq ``PG*`` variable is
removed from the environment of the launcher's own clients and of the command,
so no client can be redirected off the private socket.

Afterwards it stops the server and checks that it exited, also when the
launcher is interrupted or terminated (SIGINT, SIGTERM or SIGHUP): no process
may still use the cluster's data directory, by naming it on its command line,
having it as PGDATA in its environment (initdb and pg_ctl set that for their
helpers, and programs the server starts inherit it) or running in it (every
server process does; a backend overwrites the other two with its process
title). A setup step still running at that moment (initdb, tee, pg_ctl, psql)
may finish first, so none of them is left running on its own; one still
running after STEP_GRACE seconds is stopped (see run()). A command still
running is sent SIGTERM. A signal that arrives while a step or the command is
being started is held until its process is known, and then handled the same
way (of two, the first decides). Only the command's own process is signalled:
programs it started stop too only when the signal went to the whole process
group (Ctrl-C, timeout, a closed terminal). Further signals are ignored until
the cleanup is done, and the cleanup's server commands run in their own
session, out of reach of a repeated group signal. A signal already ignored when
the launcher starts (under nohup, for example) stays ignored. The exit status
is the command's (128+N if signal N ended it), 128+N if signal N interrupted
the launcher, and non-zero if the setup failed or the server was not seen to
exit. Status lines go to stderr; a closed terminal or a broken pipe neither
stops the cleanup nor changes the exit status, also after an error that came
before the first status line. A non-blocking stderr that is full for a moment
silences none of the lines after it. With stderr buffered (no PYTHONUNBUFFERED
or -u), the line that met the full pipe is kept too, as far as Python's buffer
holds it (4 KiB for a pipe); unbuffered, Python drops that line. A traceback
written into a full pipe keeps only its first line buffered (twice on Python
3.13), and nothing of it unbuffered. If the server does not start, the end of
its log is included in the error, because --discard deletes the log.

Socket authentication is ``trust``: only the server account (and root) can
reach the socket, and no password or other credential exists. Fixtures are
retained for inspection unless --discard is given. Nothing outside the new
cluster directory is created, changed or removed, except a private socket
directory under /tmp when the checkout path is too long for a Unix socket; that
directory is named on stderr and removed once the server has stopped. While the
server runs, it has a System V shared memory segment, which only a server that
did not stop (killed, for example) leaves behind.

Needs Linux (it reads /proc to confirm the server exited). PostgreSQL refuses to
run as root, so a root caller names an unprivileged account with --run-as, which
uses runuser. The cluster directory and its cluster.json then stay root-owned;
only data/, log/ and the socket directory belong to that account, so it cannot
change what root later runs. Root without CAP_SYS_PTRACE cannot read that
account's working directories, and in Docker's default capability set, which
also lacks CAP_SYS_ADMIN and CAP_PERFMON, not its environments either: the
cleanup then still finds the server's own processes, by their command line or
their title (which names the cluster: cluster_name), but not a program the
server started that outlives it (COPY ... TO PROGRAM, archive_command), and the
launcher says so when the server starts. A postmaster that did not stop
(killed, for example) leaves its postmaster.pid, which keeps the cluster too.
From candidates/full-stack/backend:

    python tests/postgresql_throwaway.py --run-as postgres -- \\
        python -m unittest tests.postgresql_parity -v

--locale icu-en-US creates the database with a linguistic ICU collation
instead of byte order ("C"). The crash check calls control() below.
"""
import argparse
import atexit
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
STEP_GRACE = 120  # seconds an interrupted setup step may take to finish before it is stopped
TERM_GRACE = 10  # seconds between SIGTERM and SIGKILL for such a step (runuser takes 2 to end its child)
STOP_GRACE = 60  # seconds the cleanup waits for every process using the cluster (see leftover_processes()) to exit
EXIT_FLUSH_GRACE = 2  # seconds the exit waits for a full non-blocking stderr to take what is still buffered
# While start() starts a step or the command, a signal is held here until the process is known.
HELD = {'starting': False, 'signum': None}
try:  # CPython: signal.pthread_sigmask() is a Python function around this C one (see main())
    from _signal import pthread_sigmask as C_PTHREAD_SIGMASK
except ImportError:  # not POSIX, or another Python
    C_PTHREAD_SIGMASK = getattr(signal, 'pthread_sigmask', None)


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
    """A status line on stderr. A closed terminal (EIO) or a broken pipe (EPIPE) must not cut the cleanup
    short. A non-blocking stderr that is full for now (EAGAIN) is left as it is: its reader is still there.
    With stderr buffered, the line then waits in Python's buffer (as far as that holds it) and goes out with
    a later one or with flush_stderr() at exit; unbuffered, Python drops it without an error."""
    try:
        print(message, file=sys.stderr, flush=True)
    except BlockingIOError:
        pass
    except OSError:
        silence_stderr()


def silence_stderr():
    """Point stderr's file descriptor at /dev/null once nobody reads it. What is still buffered for it can
    then be flushed at exit; otherwise Python's final flush fails and the exit status becomes 120."""
    try:
        null = os.open(os.devnull, os.O_WRONLY)
        try:
            os.dup2(null, sys.stderr.fileno())
        finally:
            os.close(null)
    except (OSError, ValueError, AttributeError):  # no file descriptor behind stderr
        pass


def flush_stderr():
    """Registered by main() with atexit, which runs before Python's own final flush: flush what is still
    buffered for stderr (a usage message, or a status line that met a full pipe, for example), and silence
    stderr if that fails or is cut short, so the final flush cannot fail and turn the exit status into 120.
    A full non-blocking stderr gets EXIT_FLUSH_GRACE seconds to take it first. Before main() installs its
    handlers, Ctrl-C is Python's KeyboardInterrupt, which could cut this short: one arriving as this function
    is entered is raised ahead of its try, and a second one (from a wrapper that passes Ctrl-C on, for
    example) could land before the silencing. So main() also registers the C function that blocks SIGINT,
    which atexit calls just before this one, and SIGINT stays blocked until the process exits; one already
    pending is raised by that call, which atexit reports. Registered without it, this function blocks SIGINT
    itself, inside its try, and one pending then still has stderr silenced. Either way, a Ctrl-C that ends
    main() after it registered this function but before its handlers gives exit status 130 (128+2, as one
    after them does) rather than ending the process by the signal. Once main() has run, SIGINT, SIGTERM and
    SIGHUP are ignored anyway; before that, SIGTERM and SIGHUP end the process."""
    try:
        if hasattr(signal, 'pthread_sigmask'):  # POSIX
            signal.pthread_sigmask(signal.SIG_BLOCK, [signal.SIGINT])
        deadline = time.monotonic() + EXIT_FLUSH_GRACE
        while True:
            try:
                sys.stderr.flush()
                return
            except BlockingIOError:
                if time.monotonic() >= deadline:
                    break
                time.sleep(0.05)
            except (OSError, ValueError, AttributeError):  # nobody reads it, or no stream is left
                break
    except BaseException:
        silence_stderr()
        raise
    silence_stderr()


def ignore_signals():
    for signum in SIGNALS:
        signal.signal(signum, signal.SIG_IGN)


def interrupted(signum, frame):
    """Handler for SIGNALS: stop the run, and ignore further signals until the cleanup is done. While a
    step or the command is being started, the signal is held until its process is known (see start()),
    so the cleanup can still stop it; the first one held decides, as when none is held. Signals are not
    ignored (or blocked) meanwhile: the command would inherit that, ignore the cleanup's SIGTERM and be
    stopped only by SIGKILL after the cleanup's 30 s."""
    if HELD['starting']:
        if HELD['signum'] is None:
            HELD['signum'] = signum
        return
    ignore_signals()  # finish the cleanup even if signalled again
    raise KeyboardInterrupt(signum)


def start(command, **options):
    """subprocess.Popen(command, **options) with a signal held until the caller has bound the process: a
    signal handled just as Popen returns would otherwise leave the process unsupervised (a tee waiting for
    input nobody would close, for example). The caller calls release() first thing inside the try that
    stops the process when interrupted. If Popen fails, a held signal replaces the failure: the run was
    interrupted either way, and the exit status says so."""
    HELD.update(starting=True, signum=None)
    try:
        return subprocess.Popen(command, **options)
    except BaseException:
        release()
        raise


def release():
    """Stop holding signals (see start()), and act on one that arrived while they were held."""
    HELD['starting'] = False
    if HELD['signum'] is not None:
        interrupted(HELD['signum'], None)


def describe(cluster):
    cluster = Path(cluster).resolve()
    if cluster.parent != FIXTURES.resolve():
        raise SystemExit('Refusing a cluster outside ' + str(FIXTURES))
    return json.loads((cluster / 'cluster.json').read_text())


def as_account(command, account):
    return ['runuser', '-u', account, '--'] + command if account else command


def run(command, stdin=None, detached=False):
    """Run one step and return its output. If the launcher is signalled meanwhile, also while the step is
    being started, the step may still finish within STEP_GRACE seconds (then it is stopped, see
    stop_step()), so a signal sent to the launcher alone cannot leave an initdb or pg_ctl running on its
    own. detached=True starts it in its own session, so signals sent to the launcher's process group do
    not reach it."""
    process = start(command, stdin=subprocess.DEVNULL if stdin is None else subprocess.PIPE,
                    stdout=subprocess.PIPE, stderr=subprocess.STDOUT, text=True, env=private_environment(),
                    start_new_session=detached)
    try:
        release()
        output, _ = process.communicate(stdin)
    except KeyboardInterrupt:
        try:
            if process.stdin:
                process.stdin.close()  # input cut short: end it, so the step cannot wait for more
        except OSError:
            pass
        # Closed: communicate() must not flush it. Before Python 3.13 it does when nothing was communicated
        # yet (a signal held while the step started), and that ValueError would replace the interrupt.
        process.stdin = None
        try:
            process.communicate(timeout=STEP_GRACE)
        except subprocess.TimeoutExpired:
            stop_step(process)
        raise
    if process.returncode:
        raise RuntimeError('Command failed (%d): %s\n%s' % (process.returncode, ' '.join(command), output))
    return output


def stop_step(process):
    """Stop a setup step that outlived STEP_GRACE: SIGTERM, then SIGKILL after TERM_GRACE seconds. As root,
    initdb, pg_ctl and tee run under runuser, which ends the step itself 2 s after a SIGTERM; psql runs as
    the caller and gets the signals directly. If something still holds the step's output after the
    SIGKILL (a stopped step stops runuser too), the launcher stops reading it, so the wait stays bounded;
    the cleanup then reports what still uses the cluster (see leftover_processes())."""
    process.terminate()
    try:
        process.communicate(timeout=TERM_GRACE)
    except subprocess.TimeoutExpired:
        process.kill()
        process.stdout.close()
        process.wait()


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


def cluster_name(cluster):
    """The server's cluster_name, which create() sets: every server process but the postmaster starts its
    title (its command line) with "postgres: <name>: "."""
    return 'mom-health-' + Path(cluster).name


def leftover_processes(info):
    """PIDs of the processes that still use this cluster's data directory (Linux /proc): their command line
    names it or is a server process title of this cluster, their environment has it as PGDATA, or it is
    their working directory. The postmaster names the directory and has it as PGDATA; its children
    (backends and auxiliary processes) overwrite both with their title, which names the cluster, so a
    backend left by a killed postmaster is found too. initdb and pg_ctl set PGDATA for what they start, so
    this finds a helper of an interrupted initdb (its sh -c and bootstrap backend name no directory), and a
    program the server starts (archive_command, COPY ... TO PROGRAM) inherits it. Any other process working
    in the data directory, a shell someone left in it included, is found too: the cluster is then kept.
    Command lines are readable to everyone, so the server's own processes are found whatever else of theirs
    the caller may read. A program the server started, or an initdb helper, is found only through its
    environment or working directory: another account's are not readable to an ordinary caller, its working
    directory not to root without CAP_SYS_PTRACE, and neither to root in Docker's default capability set (which
    also lacks CAP_SYS_ADMIN and CAP_PERFMON); see server_unseen()."""
    data = info['data']
    named, variable, where, found = data.encode(), b'PGDATA=' + data.encode(), os.path.realpath(data), []
    title = ('postgres: %s: ' % cluster_name(Path(data).parent)).encode()
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            command = (entry / 'cmdline').read_bytes()
            if (named in command or command.startswith(title) or variable in (entry / 'environ').read_bytes().split(b'\0')
                    or os.readlink(entry / 'cwd') == where):
                found.append(int(entry.name))
        except OSError:
            pass
    return found


def can_read(read, path):
    """Whether read(path) succeeds: False if permission is denied; any other OSError is raised."""
    try:
        read(path)
    except PermissionError:
        return False
    return True


def server_unseen(info):
    """What leftover_processes() cannot see here once the server runs, for main() to say, or None. Without
    /proc it sees no process at all. Where neither the postmaster's environment nor its working directory can
    be read (root in Docker's default capability set, for example), it still finds the server's own processes
    by their command line or title, but not a program the server started (COPY ... TO PROGRAM,
    archive_command), which it could find only through one of those two."""
    if not os.path.isdir('/proc/self'):
        return 'no /proc here, so the cleanup cannot see whether any process still uses the cluster'
    try:
        postmaster = int((Path(info['data']) / 'postmaster.pid').read_text().split()[0])
    except (OSError, ValueError, IndexError):  # no server after all: nothing to look at
        return None
    try:
        environment = can_read(Path.read_bytes, Path('/proc/%d/environ' % postmaster))
        working_directory = can_read(os.readlink, '/proc/%d/cwd' % postmaster)
    except OSError:  # the server is already gone
        return None
    if environment or working_directory:
        return None
    return ("the server account's environments and working directories are not readable here (root without "
            "CAP_SYS_PTRACE, as in Docker's default capability set?), so the cleanup cannot find a program the server "
            "started (COPY ... TO PROGRAM, archive_command) that outlives it; it finds the server's own processes")


def stop_server(info):
    """Stop the server if one runs, then wait up to STOP_GRACE seconds until no process uses the
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
        "fsync = on\nsynchronous_commit = on\nfull_page_writes = on\nmax_connections = 100\n"
        "cluster_name = '%s'\n" % (info['sockets'], cluster_name(cluster))))  # in every server process title
    return info


def main():
    atexit.register(flush_stderr)
    if C_PTHREAD_SIGMASK:
        # atexit calls the last registered first: this C function blocks SIGINT before flush_stderr() runs its first
        # instruction, where Python would raise a Ctrl-C that has just arrived, ahead of its try (see there).
        atexit.register(C_PTHREAD_SIGMASK, signal.SIG_BLOCK, [signal.SIGINT])
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
    for signum in SIGNALS:
        if signal.getsignal(signum) is not signal.SIG_IGN:  # left ignored (nohup, a background job), as Python does
            signal.signal(signum, interrupted)
    info, child, status = None, None, 1
    try:
        try:
            info = create(cluster, bindir, args.run_as, args.locale)
            control(cluster, 'start')
            unseen = server_unseen(info)
            if unseen:
                say('Throwaway PostgreSQL: ' + unseen)
            run([str(bindir / 'psql'), '-X', '-q', '-v', 'ON_ERROR_STOP=1', '-h', info['sockets'], '-U', ADMIN, '-d', 'postgres',
                 '-c', 'CREATE ROLE %s LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE' % ROLE,
                 '-c', "CREATE DATABASE %s OWNER %s TEMPLATE template0 ENCODING 'UTF8' %s" % (DATABASE, ROLE, LOCALES[args.locale])])
            dsn = 'host=%s dbname=%s user=%s' % (info['sockets'], DATABASE, ROLE)
            say('Throwaway PostgreSQL (%s, locale %s, socket only): %s' % (bindir, args.locale, cluster))
            child = start(command, env=private_environment(MOM_HEALTH_TEST_POSTGRESQL_DSN=dsn, MOM_HEALTH_TEST_POSTGRESQL_CLUSTER=str(cluster)))
            release()  # child names the command's process now, so the cleanup below can stop it
            status = child.wait()
            if status < 0:  # killed by signal -status: 128+N, as a shell and the launcher itself report it
                status = 128 - status
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

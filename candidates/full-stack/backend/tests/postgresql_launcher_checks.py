"""Opt-in checks of the throwaway PostgreSQL launcher itself. Fictional data only.

Not collected by ``unittest discover``. The launch checks need Linux and the
PostgreSQL server binaries; as root, the launcher's server runs as the postgres
account, and the failed-setup check also runs once as that account without
--run-as. Each launch check runs a private copy of the launcher in a unique
directory (under build/, deep enough that its socket goes in a private
directory under /tmp; one layout of the ownership check uses a short copy under
/tmp, whose socket stays in the cluster), with a small probe command and
--discard, so every cluster is removed again and concurrent runs cannot disturb
each other. The checks of a killed postmaster and of a process still using the
cluster after the wait keep their cluster by design and remove it themselves.
Interrupted (a Ctrl-C at the test's terminal, which the launcher in its own
session does not get), a launch check runs none of its cleanups, so it leaves
its private copy (build/launcher-check-*, or /tmp/lc-* for the short layout,
and build/launcher-tmpdir-* too for the TMPDIR check);
each of those two also leaves its cluster inside that copy and its /tmp socket
directory (cluster.json names it), and the killed-postmaster check also the
postmaster's System V shared memory segment (postmaster.pid line 7 names its
key and id), but only once its command has printed: interrupted earlier, that
print fails on the closed pipe, nothing is killed, and the launcher discards
the cluster itself. As root, the check that the cleanup sees every process of
the server also runs without CAP_SYS_PTRACE and in Docker's default capability
set, where capsh can drop those (it needs CAP_SETPCAP and /bin/bash), and skips
them otherwise; the check that a /proc hiding the server's processes is
reported also needs unshare and CAP_SYS_ADMIN, to mount one with hidepid in a
mount namespace of its own (Linux 5.8 or later), and the check of a /proc that
belongs to another PID namespace needs unshare and CAP_SYS_ADMIN too. Run where
/proc is another PID namespace's (its parent's, in a PID namespace of its own
under unshare --pid without --mount-proc, or one that does not show it at all),
the checks that compare their processes' PIDs with /proc's, the launch checks
among them, are skipped, and no cleanup signals a process that /proc names:
/proc's PIDs are not theirs there. The other checks need only POSIX, except
five that read /proc and are skipped without it (the environment,
working-directory and title checks of the cleanup and the memory-maps check,
Linux; the runuser step check, Linux, root and runuser) and one skipped
without ctypes (a Ctrl-C just as the exit begins). The checks that need no
server also run under the first complete interpreter of every other CPython 3
version installed in /usr/bin or /usr/local/bin (before 3.13, communicate()
flushes a stdin that run() has closed). The module imports anywhere. From
candidates/full-stack/backend:

    python -m unittest tests.postgresql_launcher_checks -v
"""
import contextlib
import errno
import io
import itertools
import json
import os
from pathlib import Path
import re
import select
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import types
import unittest
from unittest.mock import patch

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import postgresql_throwaway as launcher  # noqa: E402

ROOT = os.name == 'posix' and os.geteuid() == 0
RUN_AS = ['--run-as', 'postgres'] if ROOT else []
SIGNALS = tuple(getattr(signal, name) for name in ('SIGINT', 'SIGTERM', 'SIGHUP') if hasattr(signal, name))
STARTED = 'Throwaway PostgreSQL ('
SOCKETS = 'Throwaway PostgreSQL socket directory'
UNSEEN = "Throwaway PostgreSQL: the server account's environments and working directories are not readable here"
HIDDEN = "Throwaway PostgreSQL: the server's processes are hidden in /proc here"
DOCKER_DEFAULT_CAPABILITIES = 0xa80425fb  # root's CapEff in a container with Docker's default capability set
CAP_SYS_PTRACE = 19
WAITING = r'''
import os, sys, time
print(os.getpid(), flush=True)
time.sleep(120)
'''
PROBE = r'''
import json, os
from pathlib import Path
cluster = Path(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'])
info = json.loads((cluster / 'cluster.json').read_text())
owner = lambda path: os.stat(path).st_uid
print(json.dumps({'libpq': sorted(name for name in os.environ if name.startswith('PG')),
                  'dsn': os.environ['MOM_HEALTH_TEST_POSTGRESQL_DSN'], 'sockets': info['sockets'],
                  'socket_dir': [os.stat(info['sockets']).st_mode & 0o7777, owner(info['sockets'])],
                  'owners': {'cluster': owner(cluster), 'metadata': owner(cluster / 'cluster.json'), 'data': owner(info['data']),
                             'config': owner(Path(info['data']) / 'postgresql.conf'), 'log': owner(Path(info['log']).parent)}}))
'''
HANGUP_PROBE = r'''
import signal, time
print('SIGHUP ignored:', signal.getsignal(signal.SIGHUP) == signal.SIG_IGN, flush=True)
time.sleep(3)
print('finished normally', flush=True)
'''
# Waits until the file exists, or 120 s, so an interrupted check (whose cleanups do not run) cannot keep it, and
# with it the launcher and its server, running for good.
UNTIL = r'''
import os, time
end = time.monotonic() + 120
while not os.path.exists(%r) and time.monotonic() < end:
    time.sleep(0.05)
'''
# Runs the launcher with a SIGTERM to itself just before or just after Popen has started the command,
# which is where a signal sent to the launcher alone lands if it arrives while the command starts.
SIGNAL_WHILE_STARTING = r'''
import os, runpy, signal, subprocess, sys
when, marker, launcher = sys.argv[1:4]


class Popen(subprocess.Popen):
    def __init__(self, args, *rest, **options):
        command = marker in str(args)
        if command and when == 'before':
            os.kill(os.getpid(), signal.SIGTERM)
        super().__init__(args, *rest, **options)
        if command:
            print('command started as', self.pid, file=sys.stderr, flush=True)
            if when == 'after':
                os.kill(os.getpid(), signal.SIGTERM)


subprocess.Popen = Popen
sys.argv = sys.argv[3:]
runpy.run_path(launcher, run_name='__main__')
'''
# The launcher failing before its first status line: find_bindir() fails in one of two ways, or (usage) the
# arguments are wrong, or (interrupt) a Ctrl-C arrives in find_bindir(), before main() installs its handlers.
# "failed" and the time (time.monotonic(), one clock for every process) go to stdout once main() has failed, just
# before the exit. With "+ctrl-c", a Ctrl-C also arrives just as the exit begins: raised by a C call that atexit
# makes first (registered last), so Python handles it only at its next check. SIGINT is Python's KeyboardInterrupt,
# as at a terminal, whatever this test process inherited.
EARLY_ERROR = r'''
import atexit, signal, sys, time
signal.signal(signal.SIGINT, signal.default_int_handler)
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
failure, _, at_exit = sys.argv[2].partition('+')


def find_bindir(explicit=None):
    if failure == 'message':
        raise SystemExit('PostgreSQL server binaries (initdb, pg_ctl) not found; pass --bindir')
    if failure == 'interrupt':
        signal.raise_signal(signal.SIGINT)
    raise PermissionError(13, 'Permission denied (synthetic)')


if failure != 'usage':
    launcher.find_bindir = find_bindir
sys.argv = ['postgresql_throwaway.py'] + sys.argv[3:]
try:
    sys.exit(launcher.main())
finally:
    if at_exit == 'ctrl-c':
        import ctypes
        atexit.register(getattr(ctypes.CDLL(None), 'raise'), int(signal.SIGINT))
    print('failed', time.monotonic(), flush=True)
'''
EARLY_ERRORS = (('invalid option', 'usage', ['--locale', 'synthetic-bogus'], 2), ('error message', 'message', RUN_AS, 1),
                ('uncaught exception', 'exception', RUN_AS, 1))
# The launcher with the named signal sent to itself just as main() installs its handler for it, so that it is handled
# at once (os.kill() checks for signals before it returns). find_bindir() and create() are replaced: the run needs no
# server binaries and cannot make a cluster.
SIGNAL_AS_HANDLED = r'''
import os, signal, sys
from pathlib import Path
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
target, install = getattr(signal, sys.argv[2]), signal.signal
install(target, signal.SIG_DFL)  # whatever this process inherited (nohup ignores SIGHUP)


def installing(signum, handler):
    previous = install(signum, handler)
    if signum == target and handler is launcher.interrupted:
        os.kill(os.getpid(), signum)
    return previous


def create(*arguments):
    raise SystemExit('create() was reached: the signal was not handled as its handler was installed')


signal.signal, launcher.create, launcher.find_bindir = installing, create, lambda explicit=None: Path('/synthetic/bin')
sys.argv = ['postgresql_throwaway.py'] + sys.argv[3:]
sys.exit(launcher.main())
'''
# Status lines on a non-blocking stderr: the first meets a full pipe, the rest come after the parent drained it.
FULL_STREAM = r'''
import subprocess, sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
launcher.say('status line 1')
print('said', flush=True)
sys.stdin.readline()  # the parent has drained the pipe
launcher.say('status line 2')
launcher.say('status line 3')
subprocess.run([sys.executable, '-c', 'import sys; sys.stderr.write("command stderr line\\n")'])
'''
# A last status line that meets a full non-blocking stderr, then the exit, with flush_stderr() registered as main() does.
FULL_AT_EXIT = r'''
import atexit, sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
atexit.register(launcher.flush_stderr)
launcher.say('last status line')
print('exiting', flush=True)
'''
# A command that leaves a process behind which uses the cluster without naming it: it has the data directory as
# PGDATA, or as its working directory. The process ends once RELEASE exists, or after 30 s: less than the default
# STOP_GRACE, so an interrupted run of the check that waits for it (whose cleanups do not run) leaves no cluster
# behind. The check with a 3 s STOP_GRACE keeps its cluster and /tmp socket directory by design; interrupted, it
# leaves both (the kept cluster's cluster.json names the socket directory).
LEAVES_A_PROCESS = r'''
import os, subprocess, sys
release, how = %r, %r
data = os.path.join(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'], 'data')
wait = 'import os, time\nend = time.monotonic() + 30\nwhile not os.path.exists(%%r) and time.monotonic() < end:\n    time.sleep(0.05)'
waiter = subprocess.Popen([sys.executable, '-c', wait %% release], env=dict(os.environ, PGDATA=data) if how == 'PGDATA' else None,
                          cwd=data if how == 'working directory' else None, stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL,
                          stderr=subprocess.DEVNULL, start_new_session=True)
print(waiter.pid, flush=True)
'''
WAYS_TO_USE_THE_CLUSTER = ('PGDATA', 'working directory')
# The launcher with a STOP_GRACE of 3 s, so a check can see what the cleanup does once that wait is over.
SHORT_STOP_GRACE = r'''
import sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
launcher.STOP_GRACE = 3
sys.argv = sys.argv[2:]
sys.exit(launcher.main())
'''
# A command that asks the launcher's own leftover_processes() which processes of the running server it sees, and
# whether it can read the postmaster's environment and working directory, with the launcher's capabilities (it
# inherits them). The postmaster's children (checkpointer, background writer, ...) overwrite their command line and
# environment.
SEES_THE_SERVER = r'''
import json, os, sys
from pathlib import Path
sys.path.insert(0, %r)
import postgresql_throwaway as launcher
info = launcher.describe(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER'])
postmaster = int((Path(info['data']) / 'postmaster.pid').read_text().split()[0])


def children():
    found = set()
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            if int((entry / 'stat').read_text().rsplit(')', 1)[1].split()[1]) == postmaster:
                found.add(int(entry.name))
        except (OSError, IndexError, ValueError):
            pass
    return found


readable = {'environment': launcher.can_read(Path.read_bytes, Path('/proc/%%d/environ' %% postmaster)),
            'working directory': launcher.can_read(os.readlink, '/proc/%%d/cwd' %% postmaster)}
before = children()
found = launcher.leftover_processes(info)
lasting = before & children()  # there throughout, unlike an autovacuum worker that came and went meanwhile
print(json.dumps({'postmaster': postmaster, 'children': sorted(lasting), 'found': sorted(found), 'readable': readable}))
'''
# A command that prints whether postmaster.pid's PID names any process in this /proc.
NAMES_A_PROCESS = r'''
import os
from pathlib import Path
postmaster = int((Path(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER']) / 'data' / 'postmaster.pid').read_text().split()[0])
print(os.path.exists('/proc/%d' % postmaster))
'''
# A command that kills the server's postmaster (SIGKILL, as the kernel's out-of-memory killer would) and waits until it
# is gone, reaped; the server's other processes then end by themselves, and its postmaster.pid stays behind. First it
# prints the postmaster's PID, the "key id" of its System V shared memory segment, which such a kill leaves too, outside
# the cluster, and the files the postmaster maps (null where they are not readable, as for root without CAP_SYS_PTRACE):
# its dynamic shared memory among them, which such a kill leaves as well.
KILLS_THE_POSTMASTER = r'''
import json, os, signal, time
from pathlib import Path
lines = (Path(os.environ['MOM_HEALTH_TEST_POSTGRESQL_CLUSTER']) / 'data' / 'postmaster.pid').read_text().splitlines()
postmaster = int(lines[0])
try:
    maps = Path('/proc/%d/maps' % postmaster).read_text().splitlines()
    mapped = sorted({line.split(None, 5)[5] for line in maps if len(line.split(None, 5)) == 6})
except OSError:
    mapped = None
print(json.dumps({'postmaster': postmaster, 'segment': [int(word) for word in lines[6].split()], 'mapped': mapped}), flush=True)
os.kill(postmaster, signal.SIGKILL)
end = time.monotonic() + 60
while os.path.exists('/proc/%d' % postmaster) and time.monotonic() < end:
    time.sleep(0.05)
'''
# A status line written after the reader went away, then an exit with the given status.
CLOSED_STREAM = r'''
import sys
sys.path.insert(0, sys.argv[1])
import postgresql_throwaway as launcher
sys.stdin.readline()  # the parent has closed its end of our stderr
for line in range(3):
    launcher.say('Throwaway PostgreSQL status line %d' % line)
sys.exit(143)
'''


def binaries_available():
    try:
        return (launcher.find_bindir() / 'initdb').exists()
    except SystemExit:
        return False


def server_uid():
    import pwd  # POSIX only; imported here so the module imports anywhere
    return pwd.getpwnam('postgres').pw_uid if ROOT else os.getuid()


def _cmdline(entry):
    try:
        return (entry / 'cmdline').read_bytes()
    except OSError:
        return b''


def processes_naming(path, marker=b''):
    return [entry.name for entry in Path('/proc').glob('[0-9]*')
            if str(path).encode() in _cmdline(entry) and marker in _cmdline(entry)]


def process_state(pid):
    """(state, parent pid, start time) from /proc/<pid>/stat, or None once the process is gone."""
    try:
        fields = Path('/proc/%d/stat' % pid).read_text().rsplit(')', 1)[1].split()
        return fields[0], int(fields[1]), int(fields[19])
    except (OSError, IndexError, ValueError):
        return None


def initdb_with_its_backend(root):
    """{pid: (parent pid, start time)} for initdb from this copy and the process it runs its bootstrap or single-user
    backend in, once that runs; {} before. That process is popen's sh -c, which waits for the backend (the backend
    itself where the shell execs it). By then initdb handles signals itself and ignores SIGPIPE, so an orphaned initdb
    would finish its work."""
    initdb = {}
    for entry in Path('/proc').glob('[0-9]*'):
        if _cmdline(entry).split(b'\0')[0].endswith(b'/initdb') and str(root).encode() in _cmdline(entry):
            state = process_state(int(entry.name))
            if state:
                initdb[int(entry.name)] = state[1:]
    for entry in Path('/proc').glob('[0-9]*'):
        state = process_state(int(entry.name))
        # Not its earlier "postgres -V" probe, which runs before initdb ignores SIGPIPE.
        if state and state[1] in initdb and any(flag in _cmdline(entry) for flag in (b'--boot', b'--single')):
            return {**initdb, int(entry.name): state[1:]}
    return {}


def orphaned(family):
    """The processes of family ({pid: (parent pid, start time)}) still running under another parent."""
    found = []
    for pid, (parent, started) in family.items():
        state = process_state(pid)
        if state and state[2] == started and state[0] not in 'ZX' and state[1] != parent:
            found.append(pid)
    return found


def without_capabilities(numbers):
    """(prefix, None): the prefix (capsh --drop) that runs a command as root without the capabilities numbered; or
    (None, why not), where that cannot be done here: not root, no capsh, or capsh fails (it needs CAP_SETPCAP to
    change the bounding set, and /bin/bash)."""
    if not ROOT or not shutil.which('capsh'):
        return None, 'needs root and capsh'
    drop = '--drop=' + ','.join(str(number) for number in numbers)
    probe = subprocess.run(['capsh', drop, '--', '-c', 'exit 0'], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if probe.returncode:
        return None, 'capsh cannot drop them here: %s' % (probe.stderr.strip().splitlines() or ['exit status %d' % probe.returncode])[-1]
    return ['capsh', drop, '--', '-c', 'exec "$0" "$@"'], None


def hiding_processes(numbers):
    """(prefix, None): the prefix that runs a command as root, without the capabilities numbered (none: all kept), in a
    mount namespace of its own whose /proc lists only the processes it may ptrace (hidepid=ptraceable, as systemd's
    ProtectProc=ptraceable mounts it); or (None, why not), where that cannot be done here (it needs unshare and
    CAP_SYS_ADMIN, and capsh what without_capabilities() says)."""
    drop, why_not = without_capabilities(numbers) if numbers else ([], None)
    if why_not:
        return None, why_not
    if not shutil.which('unshare'):
        return None, 'needs unshare'
    hide = ['unshare', '--mount', '--propagation', 'private', '--', 'sh', '-c',
            'mount -t proc -o hidepid=ptraceable proc /proc && exec "$0" "$@"']
    # The mount may succeed without applying the option (procfs takes options per mount only from Linux 5.8).
    probe = subprocess.run(hide + ['grep', '-q', 'hidepid=ptraceable', '/proc/self/mountinfo'], stdin=subprocess.DEVNULL,
                           capture_output=True, text=True)
    if probe.returncode:
        why = (probe.stderr.strip().splitlines() or [''])[-1]
        return None, ('cannot mount a /proc with hidepid here: %s' % why if why else
                      'the mount did not apply hidepid=ptraceable (procfs takes options per mount only from Linux 5.8)')
    return hide + drop, None


def effective_capabilities():
    """This process's effective capabilities (CapEff in /proc/self/status, Linux), or 0 where they cannot be read."""
    try:
        return int(next(line.split()[1] for line in Path('/proc/self/status').read_text().splitlines() if line.startswith('CapEff:')), 16)
    except (OSError, StopIteration, ValueError, IndexError):
        return 0


def in_pid_namespace(numbers=(), hide=False):
    """(prefix, None): the prefix that runs a command as root in a PID namespace of its own on its parent's /proc
    (unshare --pid without --mount-proc; sh, which does not exec a command that is not its last, stays its init),
    without the capabilities numbered, and with hide on a /proc mounted with hidepid=ptraceable first (see
    hiding_processes()); or (None, why not), where that cannot be done here (it needs root, unshare and
    CAP_SYS_ADMIN). With --kill-child, killing unshare (as finish() and end() do after a failed check) kills that
    init, and with it every process of the namespace, so none of them keeps the command's output open."""
    if not ROOT or not shutil.which('unshare'):
        return None, 'needs root and unshare, for a PID namespace of its own'
    front, why_not = hiding_processes([]) if hide else ([], None)
    if why_not:
        return None, why_not
    namespace = ['unshare', '--pid', '--fork', '--kill-child', '--', 'sh', '-c', '"$@"; exit $?', 'sh']
    probe = subprocess.run(front + namespace + ['true'], stdin=subprocess.DEVNULL, capture_output=True, text=True)
    if probe.returncode:
        return None, 'cannot make a PID namespace here: %s' % (probe.stderr.strip().splitlines() or ['exit status %d' % probe.returncode])[-1]
    drop, why_not = without_capabilities(numbers) if numbers else ([], None)
    if why_not:
        return None, why_not
    return front + namespace + drop, None


def unmapped(paths):
    """Those of paths that no process maps, as far as this process may read /proc/*/maps (in bytes: a mapped
    file's path need not be text)."""
    mapped = set()
    for entry in Path('/proc').glob('[0-9]*'):
        try:
            mapped.update(line.split(None, 5)[5] for line in (entry / 'maps').read_bytes().splitlines() if len(line.split(None, 5)) == 6)
        except OSError:
            pass
    return sorted(str(path) for path in paths if os.fsencode(path) not in mapped)


def proc_of_another_pid_namespace():
    """Whether /proc belongs to another PID namespace than this process's: an ancestor's (as under unshare --pid
    without --mount-proc), whose /proc/self names another process, or a child's or sibling's, which shows processes
    but no /proc/self (as after nsenter --mount into a container). Its PIDs are then not the ones this process's
    children have. Without a /proc at all, False."""
    try:
        return os.readlink('/proc/self') != str(os.getpid())
    except OSError:
        return os.path.isdir('/proc/1')


FOREIGN_PROC = proc_of_another_pid_namespace()
FOREIGN_PROC_WHY = "this /proc is another PID namespace's: its PIDs are not the ones this process's children have"


def outside_dockers_default():
    """The capability numbers that Docker's default set lacks, up to the kernel's last one (Linux)."""
    try:
        last = int(Path('/proc/sys/kernel/cap_last_cap').read_text())
    except (OSError, ValueError):
        last = 40
    return [number for number in range(last + 1) if not DOCKER_DEFAULT_CAPABILITIES >> number & 1]


def segment(key, shmid):
    """The fields of the System V shared memory segment with that key and id in /proc/sysvipc/shm, or None. The key is a
    32-bit key_t, which postmaster.pid has unsigned and /proc/sysvipc/shm signed: one of 2**31 or more (from a data
    directory whose inode number is that large, mod 2**32, as on a large XFS volume) is negative there."""
    try:
        rows = [row.split() for row in Path('/proc/sysvipc/shm').read_text().splitlines()[1:]]
    except OSError:
        return None
    return next((fields for fields in rows if fields[1:2] == [str(shmid)] and int(fields[0]) % 2**32 == int(key) % 2**32), None)


def remove_segment(key, shmid, creator):
    """Remove the System V shared memory segment that a killed postmaster left (ipcrm), but only while it is still
    there, made by that postmaster and attached to no process. ipcrm runs as the segment's owner: root without
    CAP_SYS_ADMIN (in Docker's default capability set, for example) may not remove another account's."""
    fields = segment(key, shmid)
    if fields and fields[4] == str(creator) and fields[6] == '0':
        import pwd  # POSIX only; imported here so the module imports anywhere
        command = ['ipcrm', '-m', str(shmid)]
        if int(fields[7]) != os.geteuid():
            command = ['runuser', '-u', pwd.getpwuid(int(fields[7])).pw_name, '--'] + command
        subprocess.run(command, stdin=subprocess.DEVNULL)


def ended(path, timeout=10):
    """Wait, at most timeout seconds, until no process names path any more."""
    deadline = time.monotonic() + timeout
    while processes_naming(path) and time.monotonic() < deadline:
        time.sleep(0.05)


def remove_copy(root):
    """Stop anything still running from a private copy (only after a failed check), then remove it. Where /proc is
    another PID namespace's, its PIDs would name other processes here, so none is signalled."""
    for signum in (signal.SIGQUIT, signal.SIGKILL) if not FOREIGN_PROC else ():
        for pid in processes_naming(root):
            try:
                os.kill(int(pid), signum)
            except OSError:
                pass
        deadline = time.monotonic() + 10
        while processes_naming(root) and time.monotonic() < deadline:
            time.sleep(0.1)
    shutil.rmtree(root, ignore_errors=True)


def private_copy(test, owner=None, short=False):
    """A launcher copy in a unique directory, removed after the test. Under build/ its in-cluster socket
    path is longer than a Unix socket allows; a short copy under /tmp keeps its socket in the cluster."""
    launcher.FIXTURES.parent.mkdir(parents=True, exist_ok=True)
    root = Path(tempfile.mkdtemp(prefix='lc-' if short else 'launcher-check-', dir='/tmp' if short else launcher.FIXTURES.parent))
    test.addCleanup(remove_copy, root)
    root.chmod(0o755)  # the server account must reach data/ inside it
    script = root / ('' if short else 'deep-checkout-' + 'd' * 16) / 'backend' / 'tests' / 'postgresql_throwaway.py'
    script.parent.mkdir(parents=True)
    shutil.copy(launcher.__file__, script)
    fixtures = script.parents[2] / 'build' / 'postgresql-fixtures'
    socket = len(str(fixtures / ('x' * 12) / 's' / '.s.PGSQL.5432').encode())
    (test.assertLessEqual if short else test.assertGreater)(socket, launcher.SOCKET_PATH_LIMIT)
    if owner is not None:
        for path in [root, *root.rglob('*')]:
            os.chown(path, owner, -1)
    return root, script, fixtures


def launch(script, command, *options, run_as=RUN_AS, env=None, stderr=subprocess.PIPE, ignore=(), interpreter=sys.executable,
           user=None, wrapper=(), command_line=None, prefix=()):
    """Start a launcher in its own process group, with SIGINT, SIGTERM and SIGHUP at their defaults
    (or ignored, for those in ``ignore``) whatever this test process inherited. The launcher runs
    ``interpreter -c command``, or command_line if given; prefix (capsh ... --, for example) runs it."""
    def dispositions():
        for signum in SIGNALS:
            signal.signal(signum, signal.SIG_IGN if signum in ignore else signal.SIG_DFL)
    argv = [interpreter, *wrapper, str(script), *run_as, '--discard', *options, '--', *(command_line or [interpreter, '-c', command])]
    if user:
        argv = ['runuser', '-u', user, '--'] + argv
    return subprocess.Popen([*prefix, *argv], stdout=subprocess.PIPE, stderr=stderr, text=True, env=env, start_new_session=True,
                            preexec_fn=dispositions)


def read_until(lines, prefix, seen):
    for line in lines:
        seen.append(line)
        if line.startswith(prefix):
            return line.strip()
    return None


def finish(process, timeout=180):
    """Wait for the launcher, then return the rest of its output. The rest is read through the same
    buffered streams the checks read lines from (communicate() would skip what they buffered)."""
    try:
        process.wait(timeout)
    except subprocess.TimeoutExpired:
        process.kill()
        process.wait()
    output = process.stdout.read() if process.stdout else ''
    errors = process.stderr.read() if process.stderr else ''
    for stream in (process.stdout, process.stderr):
        if stream:
            stream.close()
    return output, errors


def buffered_environment():
    """This environment with stderr buffered, as an ordinary shell gives it (PYTHONUNBUFFERED makes it unbuffered)."""
    return {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}


def full_pipe(test):
    """A pipe whose write end is non-blocking and full, as a parent that shares its non-blocking stderr would leave
    it. Returns the reader (closed after the test), the writer and how many bytes fill it."""
    reader, writer = os.pipe()
    test.addCleanup(os.close, reader)
    os.set_blocking(writer, False)
    filled = 0
    try:
        while True:
            filled += os.write(writer, b'x' * 4096)
    except BlockingIOError:
        pass
    return reader, writer, filled


# The standard library modules that the checks without a server use (Debian's python3.N-minimal alone lacks most).
STANDARD_LIBRARY = 'import argparse, json, pty, select, shutil, signal, subprocess, tempfile, termios, unittest.mock, uuid'


def other_pythons():
    """Every other CPython 3 in /usr/bin and /usr/local/bin, one per version, the first found that has the standard
    library modules these checks use: {(3, 12): (path, None), ...}. A version none of whose interpreters has them gets
    (its first interpreter, the import error)."""
    found = {}
    for directory in ('/usr/bin', '/usr/local/bin'):
        for path in sorted(Path(directory).glob('python3.*')):
            match = re.fullmatch(r'python3\.(\d+)', path.name)
            version = (3, int(match.group(1))) if match else None
            if not version or version == sys.version_info[:2] or not os.access(path, os.X_OK) or found.get(version, (0, 1))[1] is None:
                continue  # not another CPython 3, or this version has a complete one already
            # -s: not even the caller's own site-packages, as in the run itself.
            probe = subprocess.run([str(path), '-s', '-c', STANDARD_LIBRARY], stdin=subprocess.DEVNULL, capture_output=True, text=True)
            missing = (probe.stderr.strip().splitlines() or ['exit status %d' % probe.returncode])[-1] if probe.returncode else None
            if version not in found or missing is None:
                found[version] = (path, missing)
    return found


class VersionChoiceTests(unittest.TestCase):
    def test_the_highest_installed_version_number_is_chosen(self):
        root = Path(tempfile.mkdtemp(prefix='versions-'))
        self.addCleanup(shutil.rmtree, root)
        for version in ('9.6', '16', '10'):
            (root / version / 'bin').mkdir(parents=True)
            (root / version / 'bin' / 'pg_ctl').touch()
        with patch('shutil.which', return_value=None):
            self.assertEqual(root / '16' / 'bin', launcher.find_bindir(root=root))


class CleanupHelperTests(unittest.TestCase):
    def test_a_missing_pg_ctl_means_no_server_is_running(self):
        info = dict(bindir='/nonexistent-postgresql-bindir', data='/nonexistent-postgresql-data', run_as=None)
        self.assertFalse(launcher.running(info))

    def test_a_status_line_survives_a_closed_stream(self):
        for failure in (BrokenPipeError(32, 'Broken pipe'), OSError(5, 'Input/output error')):
            with self.subTest(failure=type(failure).__name__):
                silenced = []
                with patch('builtins.print', side_effect=failure), patch.object(launcher, 'silence_stderr', lambda: silenced.append(1)):
                    launcher.say('Throwaway PostgreSQL stopped and exited: yes')
                self.assertEqual([1], silenced, 'stderr was not pointed at /dev/null')

    def test_the_status_check_runs_in_its_own_session(self):
        seen = {}
        def status(command, **options):
            seen.update(options)
            return subprocess.CompletedProcess(command, 3)
        with patch.object(launcher.subprocess, 'run', status):
            self.assertFalse(launcher.running(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertIs(True, seen.get('start_new_session'))

    def test_a_failed_stop_is_reported_once_retried_in_its_own_session_and_the_cleanup_goes_on(self):
        states, sessions, said = iter([True, True, True, False]), [], []
        def stop(command, stdin=None, detached=False):
            sessions.append(detached)
            raise RuntimeError('Command failed (1): pg_ctl stop\npg_ctl: cannot stop server; single-user server is running')
        with patch.object(launcher, 'running', lambda info: next(states)), patch.object(launcher, 'run', stop), \
                patch.object(launcher, 'leftover_processes', lambda info: []), patch.object(launcher, 'say', said.append), \
                patch.object(launcher.time, 'sleep', lambda seconds: None):
            self.assertEqual([], launcher.stop_server(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertEqual([True, True], sessions)
        self.assertEqual(1, len(said))
        self.assertIn('single-user server is running', said[0])

    @unittest.skipUnless(os.path.exists('/proc/self/environ'), 'needs Linux /proc')
    @unittest.skipIf(FOREIGN_PROC, FOREIGN_PROC_WHY)
    def test_a_process_with_the_data_directory_as_pgdata_is_left_over_and_no_other(self):
        data = '/synthetic/postgresql-fixtures/%d/data' % os.getpid()
        variants = {'PGDATA is the data directory': ({'PGDATA': data}, True),
                    'PGDATA is a directory that only starts the same': ({'PGDATA': data + '-other'}, False),
                    'another variable names it': ({'NOT_PGDATA': data}, False)}
        base, pids = {name: value for name, value in os.environ.items() if name != 'PGDATA'}, {}
        for name, (extra, _) in variants.items():
            process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, env={**base, **extra})
            self.addCleanup(process.wait)
            self.addCleanup(process.stdin.close)
            pids[name] = process.pid
        readlink = os.readlink

        def refused(path, *arguments, **options):
            if str(path).endswith('/cwd'):
                raise PermissionError(13, 'Permission denied (synthetic)')
            return readlink(path, *arguments, **options)
        for working_directories in ('readable', 'refused'):  # refused: as for root without CAP_SYS_PTRACE
            with patch.object(launcher.os, 'readlink', refused) if working_directories == 'refused' else contextlib.nullcontext():
                found = launcher.leftover_processes(dict(data=data))
            for name, (_, left) in variants.items():
                with self.subTest(name, working_directories=working_directories):
                    self.assertIs(left, pids[name] in found)
        # A process that exited (and was reaped) after /proc was listed, listed first: the scan goes on past it.
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()
        glob = Path.glob

        def listed(path, pattern, *arguments, **options):
            entries = list(glob(path, pattern, *arguments, **options))
            return [Path('/proc/%d' % gone.pid)] + entries if str(path) == '/proc' else entries
        with patch.object(Path, 'glob', listed):
            found = launcher.leftover_processes(dict(data=data))
        for name, (_, left) in variants.items():
            with self.subTest(name, listed_first='a process already gone'):
                self.assertIs(left, pids[name] in found)

    @unittest.skipUnless(os.path.exists('/proc/self/cwd'), 'needs Linux /proc')
    @unittest.skipIf(FOREIGN_PROC, FOREIGN_PROC_WHY)
    def test_a_process_working_in_the_data_directory_is_left_over_and_no_other(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-cwd-'))
        self.addCleanup(shutil.rmtree, root)
        (root / 'data').mkdir()
        (root / 'data-other').mkdir()
        (root / 'link').symlink_to(root / 'data')  # cluster.json may name the directory through a symlink
        pids = {}
        for name in ('data', 'data-other'):
            process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE, cwd=root / name,
                                       env={key: value for key, value in os.environ.items() if key != 'PGDATA'})
            self.addCleanup(process.wait)
            self.addCleanup(process.stdin.close)
            pids[name] = process.pid
        read_bytes = Path.read_bytes

        def refused(path):
            if str(path).endswith('/environ'):
                raise PermissionError(13, 'Permission denied (synthetic)')
            return read_bytes(path)
        # Environments refused: as under a policy that refuses only /proc/*/environ; the working directory is read anyway.
        for named, environments in itertools.product(('data', 'link'), ('readable', 'refused')):
            with self.subTest(cluster_json_names=named, environments=environments):
                with patch.object(Path, 'read_bytes', refused) if environments == 'refused' else contextlib.nullcontext():
                    found = launcher.leftover_processes(dict(data=str(root / named)))
                self.assertIn(pids['data'], found, 'a process working in the data directory was overlooked')
                self.assertNotIn(pids['data-other'], found, 'a directory that only starts the same was taken for it')

    @unittest.skipUnless(os.path.exists('/proc/self/cmdline'), 'needs Linux /proc')
    @unittest.skipIf(FOREIGN_PROC, FOREIGN_PROC_WHY)
    def test_a_process_titled_as_a_server_process_of_the_cluster_is_left_over_and_no_other(self):
        data = '/synthetic/postgresql-fixtures/r7%d/data' % os.getpid()
        name = launcher.cluster_name('/synthetic/postgresql-fixtures/r7%d' % os.getpid())
        titles = {'this cluster': ('postgres: %s: checkpointer ' % name, True),
                  'a cluster whose name only starts the same': ('postgres: %s-other: checkpointer ' % name, False),
                  'the name elsewhere on the command line': ('synthetic postgres: %s: checkpointer ' % name, False)}
        processes = {}
        for case, (title, _) in titles.items():
            # As a backend's title, in place of its command line (and so without the data directory). This interpreter
            # runs under it: a multi-call sleep (busybox, toybox) would take the title for the name of the program to be.
            # With argv[0] naming no file, a relocatable interpreter (python-build-standalone) finds its standard library
            # only through PYTHONHOME.
            home = {'PYTHONHOME': sys.base_prefix + os.pathsep + sys.base_exec_prefix}
            processes[case] = subprocess.Popen([title, '-c', 'import sys; sys.stdin.read()'], executable=sys.executable,
                                               stdin=subprocess.PIPE, cwd='/',
                                               env={**{key: value for key, value in os.environ.items() if key != 'PGDATA'}, **home})
            self.addCleanup(processes[case].wait)
            self.addCleanup(processes[case].stdin.close)
        found = launcher.leftover_processes(dict(data=data))
        self.assertEqual({}, {case: process.returncode for case, process in processes.items() if process.poll() is not None},
                         'a titled process ended before the cleanup looked')
        for case, (_, left) in titles.items():
            with self.subTest(case):
                self.assertIs(left, processes[case].pid in found)

    def test_what_the_cleanup_cannot_see_is_named(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-unseen-'))
        self.addCleanup(shutil.rmtree, root)
        data = root / 'data'
        data.mkdir()
        info = dict(data=str(data))

        def child():
            """A child that runs until the test ends: it stands in for a process the cleanup looks at."""
            process = subprocess.Popen([sys.executable, '-c', 'import sys; sys.stdin.read()'], stdin=subprocess.PIPE)
            self.addCleanup(process.wait)
            self.addCleanup(process.stdin.close)
            return process
        # A child stands in for the postmaster, so it exists; what root may read of it in /proc is faked below.
        live = child()
        gone = subprocess.Popen([sys.executable, '-c', 'pass'])
        gone.wait()  # and reaped: a postmaster already gone
        read_bytes, readlink, glob = Path.read_bytes, os.readlink, Path.glob
        everything = ('cmdline', 'environ', 'cwd')

        def proc(entries, own=None):
            """/proc/<pid>/ for each pid in entries ({pid: (refused, missing[, command line])}): the reads of the entries
            in refused are refused (PermissionError), and those in missing fail as for no such process
            (FileNotFoundError); the rest show the postmaster of this data directory, or the command line given.
            /proc/self names own (this process's PID, unless this /proc is another PID namespace's). Reads elsewhere
            are real."""
            contents = {'cmdline': b'postgres\0-D\0%s\0' % str(data).encode(), 'environ': b'PGDATA=%s\0' % str(data).encode(),
                        'cwd': str(data)}

            def faked(path, real):
                pid, _, name = str(path)[len('/proc/'):].partition('/') if str(path).startswith('/proc/') else ('', '', '')
                if not pid.isdigit() or int(pid) not in entries or not name:
                    return real()
                refused, missing, *command = entries[int(pid)]
                if name in missing:
                    raise FileNotFoundError(2, 'No such file or directory (synthetic)')
                if name in refused:
                    raise PermissionError(13, 'Permission denied (synthetic)')
                return command[0] if command and name == 'cmdline' else contents[name]
            def listed(path, pattern, *arguments, **options):  # the faked entries are listed first, whatever /proc this is
                real = list(glob(path, pattern, *arguments, **options))
                return [Path('/proc/%d' % pid) for pid in entries if Path('/proc/%d' % pid) not in real] + real if str(path) == '/proc' else real
            stack = contextlib.ExitStack()
            stack.enter_context(patch.object(Path, 'glob', listed))
            stack.enter_context(patch.object(Path, 'read_bytes', lambda path: faked(path, lambda: read_bytes(path))))
            stack.enter_context(patch.object(launcher.os, 'readlink', lambda path, *arguments, **options: (
                own or str(os.getpid())) if str(path) == '/proc/self' else faked(path, lambda: readlink(path, *arguments, **options))))
            return stack

        def no_pidfds(pid):
            raise OSError(errno.ENOSYS, 'Function not implemented (synthetic)')
        # Each case runs with pidfds, where this system has them, and as on a system without (signal 0 alone).
        modes = (('', contextlib.nullcontext), (' (without pidfds)', lambda: patch.object(launcher.os, 'pidfd_open', no_pidfds, create=True)))
        real_ended = launcher.ended

        def dies_after_the_first_check(process):
            """ended() as the launcher has it, but the process is killed and reaped as soon as the first call has
            returned: a postmaster that dies just after server_unseen() first saw it running."""
            calls = []

            def ending(pid):
                answer = real_ended(pid)
                if not calls:
                    process.kill()
                    process.wait()
                calls.append(answer)
                return answer
            return patch.object(launcher, 'ended', ending)

        def assertSays(said, unseen):
            if said is None:
                self.assertIsNone(unseen, 'reported as unseen')
            else:
                self.assertIn(said, unseen or '')
        cases = (('everything readable', live, (), (), None),
                 # A program the server started is found through either, where it keeps what that one shows.
                 ('environment refused', live, ('environ',), (), None),
                 ('working directory refused (root without CAP_SYS_PTRACE)', live, ('cwd',), (), None),
                 ("both refused (root in Docker's default set)", live, ('environ', 'cwd'), (), 'a program the server started'),
                 # The postmaster runs, but /proc hides it: none of the server's processes is found.
                 ('hidden (hidepid=ptraceable, or invisible with a gid= that root is not in)', live, (), everything, 'hidden in /proc'),
                 ('every read refused (hidepid=noaccess with a gid= that root is not in)', live, everything, (), 'hidden in /proc'),
                 # Not a reason to warn: the setup fails at its next step anyway.
                 ('already gone', gone, (), everything, None),
                 ('gone just after its command line was read', live, (), ('environ', 'cwd'), None),
                 ('gone just after its environment was read', live, (), ('cwd',), None),
                 ('environment refused, then gone', live, ('environ',), ('cwd',), None))
        # This /proc is an ancestor PID namespace's: postmaster.pid's PID is not the postmaster's there (each case
        # shows it as no process, unless it says otherwise), and the postmaster is looked up by its command line
        # instead. A second child stands for it there.
        there = child()
        other = str(os.getpid() + 1000003)
        decoy = os.getpid() + 1000005  # a PID that only the faked /proc lists
        foreign = (("a /proc of another PID namespace, the postmaster found there", live, {there.pid: ((), ())}, None),
                   ('a /proc of another PID namespace, its environment and working directory refused there', live,
                    {there.pid: (('environ', 'cwd'), ())}, 'a program the server started'),
                   ('a /proc of another PID namespace that hides the server', live, {there.pid: ((), everything)}, 'hidden in /proc'),
                   # Nothing there names the data directory any more, as in a real run.
                   ('a /proc of another PID namespace, the server already gone', gone, {}, None),
                   # The server is gone, so whatever still names its data directory there is not looked at.
                   ('a /proc of another PID namespace, the server already gone, its data directory still named there', gone,
                    {there.pid: (('environ', 'cwd'), ())}, None),
                   # Only an argument that is the data directory names it: a path inside it does not.
                   ('a /proc of another PID namespace, a process there naming a path inside the data directory', live,
                    {decoy: (('environ', 'cwd'), (), b'tail\0%s/pg_wal\0' % str(data).encode()), there.pid: ((), ())}, None))
        with patch.object(launcher.os.path, 'isdir', lambda path: path == '/proc/self'):  # as on Linux
            self.assertIsNone(launcher.server_unseen(info), 'no server runs, so there is nothing to miss')
            for suffix, pidfds in modes:
                for case, postmaster, refused, missing, said in cases:
                    with self.subTest(case + suffix):
                        (data / 'postmaster.pid').write_text('%d\n%s\n' % (postmaster.pid, data))
                        with proc({postmaster.pid: (refused, missing)}), pidfds():
                            assertSays(said, launcher.server_unseen(info))
                for case, postmaster, entries, said in foreign:
                    with self.subTest(case + suffix):
                        (data / 'postmaster.pid').write_text('%d\n%s\n' % (postmaster.pid, data))
                        with proc({postmaster.pid: ((), everything), **entries}, own=other), pidfds():
                            assertSays(said, launcher.server_unseen(info))
                with self.subTest("a /proc of another PID namespace, postmaster.pid's PID another process there" + suffix):
                    # That process (live, shown as it is) is not the postmaster, which /proc hides there.
                    (data / 'postmaster.pid').write_text('%d\n%s\n' % (live.pid, data))
                    with proc({there.pid: ((), everything)}, own=other), pidfds():
                        assertSays('hidden in /proc', launcher.server_unseen(info))
                for case, own in (('gone just before its command line was read', None),
                                  ('a /proc of another PID namespace, the server gone just before it was looked up there', other)):
                    with self.subTest(case + suffix):
                        dying = child()
                        (data / 'postmaster.pid').write_text('%d\n%s\n' % (dying.pid, data))
                        with proc({dying.pid: ((), everything)}, own=own), pidfds(), dies_after_the_first_check(dying):
                            assertSays(None, launcher.server_unseen(info))
            # A zombie still answers signal 0, and an ancestor's /proc shows it with no command line: its pidfd tells.
            with self.subTest('a /proc of another PID namespace, the server a zombie'):
                if not hasattr(os, 'pidfd_open') or not hasattr(os, 'waitid'):
                    self.skipTest('needs pidfds (Linux 5.3 or later)')
                zombie = subprocess.Popen([sys.executable, '-c', 'pass'])
                self.addCleanup(zombie.wait)
                os.waitid(os.P_PID, zombie.pid, os.WEXITED | os.WNOWAIT)  # it has exited, and is not reaped yet
                (data / 'postmaster.pid').write_text('%d\n%s\n' % (zombie.pid, data))
                with proc({zombie.pid: ((), everything)}, own=other):
                    assertSays(None, launcher.server_unseen(info))
            kill = os.kill

            def refused(pid, signum):  # as for root without CAP_KILL: another account's process may not be signalled
                if pid == live.pid:
                    raise PermissionError(1, 'Operation not permitted (synthetic)')
                return kill(pid, signum)
            with self.subTest('hidden, and signal 0 to it refused (without pidfds)'):
                (data / 'postmaster.pid').write_text('%d\n%s\n' % (live.pid, data))
                with proc({live.pid: ((), everything)}), modes[1][1](), patch.object(launcher.os, 'kill', refused):
                    self.assertIn('hidden in /proc', launcher.server_unseen(info) or '', 'a running postmaster was taken for gone')
        with patch.object(launcher.os.path, 'isdir', lambda path: False):  # no /proc
            self.assertIn('no /proc here', launcher.server_unseen(info) or '')

    @unittest.skipUnless(os.path.exists('/proc/self/maps'), 'needs Linux /proc')
    def test_the_checks_read_memory_maps_whose_paths_are_not_text(self):
        # unmapped() names the /dev/shm files a killed server left; a mapped file whose name is not UTF-8 must not stop it.
        root = Path(tempfile.mkdtemp(prefix='launcher-maps-'))
        self.addCleanup(shutil.rmtree, root)
        odd = Path(os.fsdecode(os.fsencode(root) + b'/synthetic-\xff\xfe-segment'))
        odd.write_bytes(b'\0' * 4096)
        mapping = subprocess.Popen([sys.executable, '-c', 'import mmap, sys\nwith open(sys.argv[1], "rb") as file:\n'
                                    '    view = mmap.mmap(file.fileno(), 0, prot=mmap.PROT_READ)\nprint("mapped", flush=True)\nsys.stdin.read()',
                                    str(odd)], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True)
        self.addCleanup(mapping.wait)
        self.addCleanup(mapping.stdout.close)
        self.addCleanup(mapping.stdin.close)
        self.assertEqual('mapped', mapping.stdout.readline().strip())
        self.assertEqual([str(root / 'not-mapped')], unmapped([odd, root / 'not-mapped']))

    def test_the_checks_find_the_segment_of_a_killed_postmaster_whatever_the_sign_of_its_key(self):
        # /proc/sysvipc/shm prints a key of 2**31 or more signed; postmaster.pid line 7 has it unsigned, in 64 bits.
        rows = ('       key      shmid perms       size  cpid  lpid nattch   uid   gid  cuid  cgid      atime      dtime      ctime\n'
                '-1698950604    1638406   600         56 23173 23181      0   102   104   102   104          0          0 1700000000\n'
                '    674929    1638401   600         56 23246 23250      0   102   104   102   104          0          0 1700000000\n')
        read_text = Path.read_text
        with patch.object(Path, 'read_text', lambda path, *arguments, **options: rows if str(path) == '/proc/sysvipc/shm'
                          else read_text(path, *arguments, **options)):
            self.assertEqual('23173', (segment(2**64 - 1698950604, 1638406) or [None] * 5)[4], 'a negative key was not matched')
            self.assertEqual('23246', (segment(674929, 1638401) or [None] * 5)[4])
            self.assertIsNone(segment(674929, 1638406), 'a segment with another key was taken for it')
            self.assertIsNone(segment(2**64 - 1698950604, 1638401), 'a segment with another id was taken for it')

    def test_the_cleanup_waits_for_processes_that_still_name_the_cluster(self):
        left, slept = iter([[4242], [4242], []]), []
        with patch.object(launcher, 'running', lambda info: False), patch.object(launcher, 'leftover_processes', lambda info: next(left)), \
                patch.object(launcher.time, 'sleep', slept.append):
            self.assertEqual([], launcher.stop_server(dict(bindir='/synthetic/bin', data='/synthetic/data', run_as=None)))
        self.assertEqual(2, len(slept))


def restore_signals(test):
    """Put SIGNALS and SIGALRM back as they are now once the test is over, and HELD as it was."""
    for signum in SIGNALS + (signal.SIGALRM,):
        test.addCleanup(signal.signal, signum, signal.getsignal(signum))
    test.addCleanup(launcher.HELD.update, dict(launcher.HELD))


def interrupt(signum, frame):
    raise KeyboardInterrupt(signum)


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class SignalTests(unittest.TestCase):
    def test_the_handler_stops_the_run_and_ignores_further_signals(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)
        with self.assertRaises(KeyboardInterrupt) as stop:
            launcher.interrupted(signal.SIGTERM, None)
        self.assertEqual((signal.SIGTERM,), stop.exception.args)
        self.assertEqual([signal.SIG_IGN] * len(SIGNALS), [signal.getsignal(signum) for signum in SIGNALS])

    def test_while_the_command_starts_the_handler_holds_the_signal_and_ignores_nothing(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=True, signum=None)
        self.assertIsNone(launcher.interrupted(signal.SIGHUP, None))
        self.assertEqual(signal.SIGHUP, launcher.HELD['signum'])
        # Ignored now, the signals would stay ignored in the command, which could then not be stopped.
        self.assertEqual([launcher.interrupted] * len(SIGNALS), [signal.getsignal(signum) for signum in SIGNALS])

    def test_of_two_signals_held_while_a_process_starts_the_first_decides(self):
        restore_signals(self)  # release() ignores the signals from then on
        for first, second in ((signal.SIGTERM, signal.SIGHUP), (signal.SIGHUP, signal.SIGTERM)):
            with self.subTest(first=first.name, second=second.name):
                launcher.HELD.update(starting=True, signum=None)
                launcher.interrupted(first, None)
                launcher.interrupted(second, None)
                # As when none is held: the first stops the run, and its number is the one reported (128+N).
                with self.assertRaises(KeyboardInterrupt) as stop:
                    launcher.release()
                self.assertEqual((first,), stop.exception.args)

    def test_a_status_line_nobody_reads_leaves_the_exit_status_alone(self):
        import pty  # POSIX only, like the termios module it needs
        for stream, buffered in itertools.product(('pipe', 'terminal'), (True, False)):
            with self.subTest(stream=stream, buffered=buffered):
                environment = {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer = pty.openpty() if stream == 'terminal' else os.pipe()
                process = subprocess.Popen([sys.executable, '-c', CLOSED_STREAM, str(HERE)], stdin=subprocess.PIPE, stderr=writer,
                                           env=environment, text=True)
                os.close(writer)
                os.close(reader)  # nobody reads its stderr any more
                process.stdin.write('go\n')
                process.stdin.close()
                # Python's final flush of a buffered stderr would fail, and the exit status would be 120.
                self.assertEqual(143, process.wait(60))

    def test_an_error_before_the_first_status_line_keeps_its_exit_status_when_nobody_reads_stderr(self):
        import pty  # POSIX only, like the termios module it needs
        failures = (('usage error', [str(HERE / 'postgresql_throwaway.py'), '--discard'], 2),
                    # Reported by argparse itself, from inside parse_args().
                    ('invalid option', [str(HERE / 'postgresql_throwaway.py'), '--locale', 'synthetic-bogus', '--discard', '--', 'true'], 2),
                    ('error message', ['-c', EARLY_ERROR, str(HERE), 'message', *RUN_AS, '--discard', '--', 'true'], 1),
                    ('uncaught exception', ['-c', EARLY_ERROR, str(HERE), 'exception', *RUN_AS, '--discard', '--', 'true'], 1))
        for (name, argv, expected), stream, buffered in itertools.product(failures, ('pipe', 'terminal'), (True, False)):
            with self.subTest(name, stream=stream, buffered=buffered):
                environment = {key: value for key, value in os.environ.items() if key != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer = pty.openpty() if stream == 'terminal' else os.pipe()
                os.close(reader)  # nobody reads its stderr
                process = subprocess.Popen([sys.executable, *argv], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=writer,
                                           env=environment)
                os.close(writer)
                # Not 120: the usage message or traceback is still buffered when Python's final flush fails.
                self.assertEqual(expected, process.wait(60))

    def assertStillWaiting(self, process, failed_at, message, expected):
        """The launcher, which printed "failed" at failed_at (time.monotonic()), has not exited yet. If it has and the
        whole exit wait could have passed by now, this check was starved (a stopped or overloaded test process): its
        timing tells nothing then, so only the exit status, which does not depend on it, is checked before the skip."""
        status = process.poll()
        late = time.monotonic() - failed_at
        if status is not None and late >= launcher.EXIT_FLUSH_GRACE:
            self.assertEqual(expected, status, 'starved, but the exit status is still wrong')
            self.skipTest('starved: it looked %.2f s after the launcher failed, past the whole exit wait (exit status %d, '
                          'as expected)' % (late, status))
        self.assertIsNone(status, message)

    def test_ctrl_c_while_the_exit_waits_for_a_full_stderr_is_held_off_and_the_early_errors_exit_status_kept(self):
        for name, failure, options, expected in EARLY_ERRORS:
            with self.subTest(name):
                _, writer, _ = full_pipe(self)  # and nobody drains it
                process = subprocess.Popen([sys.executable, '-c', EARLY_ERROR, str(HERE), failure, *options, '--discard', '--', 'true'],
                                           stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=writer, env=buffered_environment(),
                                           text=True)
                os.close(writer)
                self.addCleanup(process.stdout.close)
                self.addCleanup(process.kill)
                self.assertTrue(select.select([process.stdout], [], [], 30)[0], 'the launcher did not fail')
                word, failed_at = process.stdout.readline().split()
                self.assertEqual('failed', word)
                time.sleep(launcher.EXIT_FLUSH_GRACE / 4)  # well inside the grace
                self.assertStillWaiting(process, float(failed_at), 'the exit had stopped waiting for the full stderr before the signal',
                                        expected)
                # Ctrl-C twice, as from a terminal and a wrapper that passes it on: main() never got to install its handlers.
                process.send_signal(signal.SIGINT)
                process.send_signal(signal.SIGINT)
                time.sleep(launcher.EXIT_FLUSH_GRACE / 10)
                # Held off: a second KeyboardInterrupt could otherwise land before stderr is silenced.
                self.assertStillWaiting(process, float(failed_at), 'Ctrl-C cut the exit wait short', expected)
                # Not 120: after the grace, stderr is silenced before Python's final flush.
                self.assertEqual(expected, process.wait(60))

    def test_a_ctrl_c_just_as_the_exit_begins_keeps_the_early_errors_exit_status(self):
        try:  # EARLY_ERROR's "+ctrl-c" raises it through ctypes, from the C library's raise()
            import ctypes
            getattr(ctypes.CDLL(None), 'raise')
        except (ImportError, OSError, AttributeError) as missing:
            self.skipTest("needs ctypes and the C library's raise(): %s" % missing)
        for name, failure, options, expected in EARLY_ERRORS:
            with self.subTest(name):
                _, writer, _ = full_pipe(self)  # and nobody drains it
                process = subprocess.Popen([sys.executable, '-c', EARLY_ERROR, str(HERE), failure + '+ctrl-c', *options, '--discard', '--',
                                            'true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=writer,
                                           env=buffered_environment())
                os.close(writer)
                self.addCleanup(process.kill)
                # Pending as flush_stderr() would be entered, the Ctrl-C would be raised ahead of its try, leave stderr as it
                # is, and make Python's final flush fail (120); main() blocks SIGINT in C before that, so it is raised there.
                self.assertEqual(expected, process.wait(60))

    def test_main_has_sigint_blocked_in_c_before_the_exit_flush(self):
        registered = []
        with patch.object(launcher.atexit, 'register', lambda func, *args: registered.append((func, args))), \
                patch.object(sys, 'argv', ['postgresql_throwaway.py', '--locale', 'synthetic-bogus']), \
                contextlib.redirect_stderr(io.StringIO()), self.assertRaises(SystemExit):
            launcher.main()
        self.assertEqual([launcher.flush_stderr], [func for func, _ in registered[:1]])
        # Registered after flush_stderr(), so atexit calls it first. A C function, as signal.pthread_sigmask() (a Python
        # function) is not: Python then runs no instruction, and so handles no signal, between the two.
        self.assertEqual(2, len(registered), registered)
        self.assertIsInstance(registered[1][0], types.BuiltinFunctionType)
        self.assertEqual((signal.SIG_BLOCK, [signal.SIGINT]), registered[1][1])

    def test_a_ctrl_c_before_main_installs_its_handlers_gives_exit_status_130(self):
        process = subprocess.run([sys.executable, '-c', EARLY_ERROR, str(HERE), 'interrupt', *RUN_AS, '--discard', '--', 'true'],
                                 stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL, timeout=60)
        # 128+2, as for a Ctrl-C after them: SIGINT is blocked as the exit begins and stays blocked (see flush_stderr()),
        # so Python's own exit by the signal, for an unhandled KeyboardInterrupt, does not happen.
        self.assertEqual(130, process.returncode)

    def test_a_signal_handled_as_soon_as_main_installs_its_handler_gives_128_plus_its_number(self):
        for signum in SIGNALS:
            with self.subTest(signal=signum.name):
                process = subprocess.run([sys.executable, '-c', SIGNAL_AS_HANDLED, str(HERE), signum.name, *RUN_AS, '--discard', '--',
                                          'true'], stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE, text=True,
                                         timeout=60)
                # Not 130 for SIGTERM and SIGHUP: handled inside main()'s try, like any later signal, not past it.
                self.assertEqual(128 + signum, process.returncode, process.stderr)
                self.assertIn('Interrupted by signal %d; stopping the throwaway server.' % signum, process.stderr)

    def test_an_interrupt_that_cuts_the_exit_flush_short_still_silences_stderr(self):
        silenced = []
        # Registered without main()'s C call (as FULL_AT_EXIT registers it), flush_stderr() blocks SIGINT itself: a Ctrl-C
        # already pending then is raised by that very call.
        with patch.object(launcher.signal, 'pthread_sigmask', side_effect=KeyboardInterrupt), \
                patch.object(launcher, 'silence_stderr', lambda: silenced.append(1)), self.assertRaises(KeyboardInterrupt):
            launcher.flush_stderr()
        self.assertEqual([1], silenced, 'stderr was left as it is, so Python\'s final flush could fail and exit 120')

    def test_a_full_non_blocking_stderr_is_not_silenced_and_buffered_keeps_the_line_that_met_it(self):
        for buffered in (True, False):
            with self.subTest(buffered=buffered):
                environment = buffered_environment()
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                reader, writer, filled = full_pipe(self)
                process = subprocess.Popen([sys.executable, '-c', FULL_STREAM, str(HERE)], stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                                           stderr=writer, env=environment, text=True)
                os.close(writer)
                self.addCleanup(process.kill)
                # A launcher that waited for the reader here would never say this, as the pipe is drained only after it.
                self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'blocked on its full stderr')
                self.assertEqual('said', process.stdout.readline().strip())  # its first status line met the full pipe
                drained = 0
                while drained < filled:
                    drained += len(os.read(reader, filled - drained))
                process.stdin.write('go\n')
                process.stdin.close()
                process.stdout.read()
                process.stdout.close()
                self.assertEqual(0, process.wait(60))
                text = b''
                for chunk in iter(lambda: os.read(reader, 65536), b''):
                    text += chunk
                # The reader is still there, so nothing may silence stderr. Unbuffered, Python itself drops line 1.
                expected = ['status line 1'] * buffered + ['status line 2', 'status line 3', 'command stderr line']
                self.assertEqual(expected, text.decode().splitlines())

    def test_at_exit_a_full_non_blocking_stderr_gets_time_to_take_the_last_line(self):
        reader, writer, filled = full_pipe(self)
        process = subprocess.Popen([sys.executable, '-c', FULL_AT_EXIT, str(HERE)], stdout=subprocess.PIPE, stderr=writer,
                                   env=buffered_environment(), text=True)  # unbuffered, the line would be dropped at once
        os.close(writer)
        self.addCleanup(process.kill)
        self.assertTrue(select.select([process.stdout], [], [], 10)[0], 'blocked on its full stderr')
        self.assertEqual('exiting', process.stdout.readline().strip())
        time.sleep(0.3)  # the reader is slow, not gone: it drains the pipe well within EXIT_FLUSH_GRACE
        drained = 0
        while drained < filled:
            drained += len(os.read(reader, filled - drained))
        self.assertEqual(0, process.wait(60))
        process.stdout.close()
        text = b''
        for chunk in iter(lambda: os.read(reader, 65536), b''):
            text += chunk
        self.assertEqual('last status line\n', text.decode())

    def test_at_exit_a_stderr_still_full_after_the_grace_is_silenced_and_the_exit_status_kept(self):
        _, writer, _ = full_pipe(self)  # and nobody drains it
        started = time.monotonic()
        process = subprocess.Popen([sys.executable, '-c', FULL_AT_EXIT, str(HERE)], stdout=subprocess.DEVNULL, stderr=writer,
                                   env=buffered_environment())
        os.close(writer)
        self.addCleanup(process.kill)
        # Not 120: after EXIT_FLUSH_GRACE the line is given up and stderr silenced before Python's final flush.
        self.assertEqual(0, process.wait(60))
        self.assertGreaterEqual(time.monotonic() - started, launcher.EXIT_FLUSH_GRACE, 'the exit did not wait for the reader')

    def test_a_signal_held_while_a_start_fails_stops_the_run_instead_of_the_failure(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)

        class Signalled(subprocess.Popen):
            def __init__(self, *arguments, **options):
                os.kill(os.getpid(), signal.SIGTERM)  # held: no process is known yet
                super().__init__(*arguments, **options)
        with patch.object(launcher.subprocess, 'Popen', Signalled), self.assertRaises(KeyboardInterrupt) as stop:
            launcher.start(['/nonexistent-synthetic-command'])
        self.assertEqual((signal.SIGTERM,), stop.exception.args)
        self.assertFalse(launcher.HELD['starting'])

    def test_a_step_signalled_just_as_its_popen_returns_is_still_supervised(self):
        restore_signals(self)
        for signum in SIGNALS:
            signal.signal(signum, launcher.interrupted)
        launcher.HELD.update(starting=False, signum=None)
        started = []

        class Signalled(subprocess.Popen):
            def __init__(self, *arguments, **options):
                super().__init__(*arguments, **options)
                started.append(self)
                os.kill(os.getpid(), signal.SIGTERM)  # handled before run() has bound the process
        self.addCleanup(lambda: [process.kill() for process in started if process.poll() is None])
        step = [sys.executable, '-c', 'import sys; sys.stdin.read()']  # like tee: it ends only when its input ends
        with patch.object(launcher.subprocess, 'Popen', Signalled), self.assertRaises(KeyboardInterrupt):
            launcher.run(step, stdin='synthetic settings\n')
        self.assertEqual(1, len(started))
        # Supervised: its input was closed and it was waited for, so it ended; unsupervised it would wait for more.
        self.assertEqual(0, started[0].returncode, 'the step was left waiting for its input')


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class StepTests(unittest.TestCase):
    """An interrupted setup step that outlives STEP_GRACE is stopped, and the wait for it stays bounded."""
    def interrupted_run(self, command, step_grace=0.3, term_grace=1):
        """run() with a SIGTERM to this process 0.5 s in, while the step runs, handled by the launcher's own
        handler: so a run() that still held signals while it waited for the step would wait it out."""
        restore_signals(self)
        launcher.HELD.update(starting=False, signum=None)
        signal.signal(signal.SIGTERM, launcher.interrupted)
        signal.signal(signal.SIGALRM, lambda signum, frame: os.kill(os.getpid(), signal.SIGTERM))
        started = time.monotonic()
        with patch.object(launcher, 'STEP_GRACE', step_grace), patch.object(launcher, 'TERM_GRACE', term_grace):
            signal.setitimer(signal.ITIMER_REAL, 0.5)  # the step is running by then
            try:
                with self.assertRaises(KeyboardInterrupt):
                    launcher.run(command)
            finally:
                signal.setitimer(signal.ITIMER_REAL, 0)
        return time.monotonic() - started

    def pids(self, path):
        deadline = time.monotonic() + 10
        while not (path.exists() and path.read_text().endswith('\n')) and time.monotonic() < deadline:
            time.sleep(0.01)
        return [int(pid) for pid in path.read_text().split()]

    def gone(self, pid):
        try:
            os.kill(pid, 0)
        except ProcessLookupError:
            return True
        return False

    def kill_later(self, pids=None, marker=None):
        """Once the test is over, SIGKILL what the pids file names and every process whose command line
        holds the marker. Registered before the run, so a check that fails early leaves no step behind.
        Where /proc is another PID namespace's, its PIDs would name other processes here, so only the pids
        file is used (its processes wrote this namespace's PIDs into it themselves)."""
        def kill():
            found = set()
            if pids is not None and pids.exists():
                found.update(int(pid) for pid in pids.read_text().split())
            if marker is not None and not FOREIGN_PROC:
                found.update(int(entry.name) for entry in Path('/proc').glob('[0-9]*') if marker.encode() in _cmdline(entry))
            for pid in found:
                try:
                    os.kill(pid, signal.SIGKILL)
                except OSError:
                    pass
        self.addCleanup(kill)

    def scratch(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-step-'))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        return root

    def test_a_step_that_ignores_sigterm_is_killed(self):
        pids = self.scratch() / 'pids'
        step = ("import os, signal, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "open(%r, 'w').write('%%d\\n' %% os.getpid())\ntime.sleep(30)\n" % str(pids))
        self.kill_later(pids, marker=str(pids))
        elapsed = self.interrupted_run([sys.executable, '-c', step])
        self.assertLess(elapsed, 15, 'run() waited for the step instead of stopping it')
        self.assertTrue(self.gone(self.pids(pids)[0]), 'the step was left running')

    def test_a_step_that_finishes_within_the_grace_is_left_to_finish(self):
        done = self.scratch() / 'done'
        step = "import time; time.sleep(1.5); open(%r, 'w').write('finished')" % str(done)
        self.kill_later(marker=str(done))
        elapsed = self.interrupted_run([sys.executable, '-c', step], step_grace=launcher.STEP_GRACE, term_grace=launcher.TERM_GRACE)
        self.assertTrue(done.exists(), 'the interrupted step was stopped instead of left to finish')
        self.assertLess(elapsed, 15)

    def test_the_wait_ends_even_when_another_process_still_holds_the_steps_output(self):
        pids = self.scratch() / 'pids'
        holder = "import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)  # " + str(pids)
        step = ("import os, signal, subprocess, sys, time\nsignal.signal(signal.SIGTERM, signal.SIG_IGN)\n"
                "holder = subprocess.Popen([sys.executable, '-c', %r])\n"
                "open(%r, 'w').write('%%d %%d\\n' %% (os.getpid(), holder.pid))\ntime.sleep(30)\n" % (holder, str(pids)))
        self.kill_later(pids, marker=str(pids))
        elapsed = self.interrupted_run([sys.executable, '-c', step])
        step_pid, holder_pid = self.pids(pids)
        self.assertLess(elapsed, 15, 'run() kept reading output that another process still held')
        self.assertTrue(self.gone(step_pid), 'the step was left running')

    @unittest.skipUnless(ROOT and sys.platform.startswith('linux') and os.path.exists('/proc/self/cmdline') and shutil.which('runuser'),
                         'needs Linux with /proc, root and runuser')
    def test_as_root_runuser_stops_the_step_itself(self):
        interpreter = os.path.realpath(sys.executable)
        if subprocess.run(['runuser', '-u', 'postgres', '--', 'test', '-x', interpreter]).returncode:
            self.skipTest('the postgres account cannot run %s' % interpreter)
        marker = 'launcher-step-under-runuser-%d' % os.getpid()
        step = 'import signal, time; signal.signal(signal.SIGTERM, signal.SIG_IGN); time.sleep(30)  # ' + marker
        self.kill_later(marker=marker)
        # runuser kills its child 2 s after a SIGTERM; a SIGKILL any earlier would end runuser alone, so the
        # launcher's own TERM_GRACE must leave it that long.
        elapsed = self.interrupted_run(['runuser', '-u', 'postgres', '--', interpreter, '-c', step], term_grace=launcher.TERM_GRACE)
        left = [entry.name for entry in Path('/proc').glob('[0-9]*') if marker.encode() in _cmdline(entry)]
        self.assertLess(elapsed, 15, 'run() waited for the step under runuser')
        self.assertEqual([], left, 'the step under runuser was left running')


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class SetupTests(unittest.TestCase):
    def long_cluster(self):
        """A cluster path too long for an in-cluster socket, with SHORT_SOCKET_BASE in a temporary root."""
        root = Path(tempfile.mkdtemp(prefix='launcher-socket-'))
        self.addCleanup(shutil.rmtree, root, ignore_errors=True)
        base = root / 'short'
        base.mkdir()
        cluster = root / ('c' * 90) / 'postgresql-fixtures' / 'synthetic'
        self.assertGreater(len(str(cluster / 's' / '.s.PGSQL.5432').encode()), launcher.SOCKET_PATH_LIMIT)
        for target, value in (('SHORT_SOCKET_BASE', str(base)), ('run', lambda command, stdin=None, detached=False: '')):
            patcher = patch.object(launcher, target, value)
            patcher.start()
            self.addCleanup(patcher.stop)
        return cluster, base

    def test_a_signal_while_the_short_socket_directory_is_made_waits_until_cluster_json_names_it(self):
        restore_signals(self)
        mkdtemp = tempfile.mkdtemp
        for signum in SIGNALS:
            with self.subTest(signal=signum.name):
                cluster, base = self.long_cluster()
                signal.signal(signum, interrupt)
                made = []

                def signalled(*arguments, **options):
                    made.append(mkdtemp(*arguments, **options))
                    os.kill(os.getpid(), signum)
                    return made[-1]
                with patch.object(launcher.tempfile, 'mkdtemp', signalled), self.assertRaises(KeyboardInterrupt):
                    launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), None, 'c')
                self.assertEqual(1, len(made))
                self.assertEqual(base, Path(made[0]).parent)
                self.assertTrue((cluster / 'cluster.json').exists(), 'interrupted before cluster.json named the socket directory')
                self.assertEqual(made[0], json.loads((cluster / 'cluster.json').read_text())['sockets'])

    def test_a_cluster_json_that_cannot_be_written_takes_the_short_socket_directory_with_it(self):
        cluster, base = self.long_cluster()
        write_text = Path.write_text
        def full(path, *arguments, **options):
            if path.name == 'cluster.json':
                raise OSError(28, 'No space left on device (synthetic)')
            return write_text(path, *arguments, **options)
        with patch.object(Path, 'write_text', full), self.assertRaises(OSError):
            launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), None, 'c')
        self.assertEqual([], list(base.iterdir()), 'the temporary socket directory was left behind')

    def test_the_server_account_appends_the_settings_and_root_writes_nothing_in_its_directories(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-config-'))
        self.addCleanup(shutil.rmtree, root)
        import pwd
        account = pwd.getpwuid(os.getuid()).pw_name  # any existing account: os.chown is recorded, not run
        cluster, commands = root / 'postgresql-fixtures' / 'synthetic', []
        with patch.object(launcher, 'run', lambda command, stdin=None, detached=False: commands.append((command, stdin)) or ''), \
                patch.object(launcher.os, 'chown', lambda *arguments: None), \
                patch.object(launcher, 'SHORT_SOCKET_BASE', str(root)):  # a long TMPDIR: any short socket directory goes here
            info = launcher.create(cluster, Path('/usr/lib/postgresql/16/bin'), account, 'c')
        config = str(Path(info['data']) / 'postgresql.conf')
        appends = [(command, stdin) for command, stdin in commands if config in command]
        self.assertEqual([['runuser', '-u', account, '--', 'tee', '-a', config]], [command for command, _ in appends])
        self.assertIn("listen_addresses = ''", appends[0][1])
        # In data/pg_dynshmem, so a killed server leaves it in the cluster, not in /dev/shm.
        self.assertIn('\ndynamic_shared_memory_type = mmap\n', appends[0][1])
        self.assertFalse(Path(config).exists(), 'the launcher wrote the server configuration itself')

    def test_a_server_that_does_not_start_reports_the_end_of_its_log(self):
        root = Path(tempfile.mkdtemp(prefix='launcher-start-'))
        self.addCleanup(shutil.rmtree, root)
        (root / 'bin').mkdir()
        (root / 'bin' / 'pg_ctl').write_text('#!/bin/sh\necho "pg_ctl: could not start server"\nexit 1\n')
        (root / 'bin' / 'pg_ctl').chmod(0o755)
        cluster = root / 'fixtures' / 'synthetic'
        cluster.mkdir(parents=True)
        log = cluster / 'server.log'
        log.write_text('\n'.join('synthetic log line %d' % number for number in range(30)) + '\nFATAL:  synthetic start failure\n')
        (cluster / 'cluster.json').write_text(json.dumps(dict(bindir=str(root / 'bin'), data=str(cluster / 'data'),
                                                              sockets=str(cluster / 's'), log=str(log), run_as=None)))
        with patch.object(launcher, 'FIXTURES', root / 'fixtures'):
            with self.assertRaises(RuntimeError) as failure:
                launcher.control(cluster, 'start')
        self.assertIn('could not start server', str(failure.exception))
        self.assertIn('FATAL:  synthetic start failure', str(failure.exception))
        self.assertNotIn('synthetic log line 5\n', str(failure.exception))


@unittest.skipUnless(os.name == 'posix', 'needs POSIX')
class OtherPythonTests(unittest.TestCase):
    """The checks that need no server, under every other CPython 3 installed here. Python before 3.13 flushes a
    closed stdin in communicate() and raises ValueError, which run() must not let replace an interrupt. Of each
    version, the first interpreter found that has the standard library modules these checks use is run, whatever
    the version; a version none of whose interpreters has them (Debian's python3.N-minimal alone has no unittest,
    pty or shutil) is skipped, with the reason."""
    def test_the_checks_without_a_server_pass_under_every_other_installed_python(self):
        pythons = other_pythons()
        if not pythons:
            self.skipTest('no other CPython 3 in /usr/bin or /usr/local/bin')
        names = ['tests.postgresql_launcher_checks.' + case for case in ('VersionChoiceTests', 'CleanupHelperTests', 'SignalTests',
                                                                          'StepTests', 'SetupTests')]
        for version, (python, missing) in pythons.items():
            with self.subTest(python='%d.%d' % version):
                if missing:
                    self.skipTest('%s lacks part of its standard library (and no other %d.%d here has it all): %s'
                                  % ((python,) + version + (missing,)))
                # These checks need the standard library only (-s: not even the caller's own site-packages). -v names each
                # check as it starts, so a run that hangs still says which one it was in.
                try:
                    result = subprocess.run([str(python), '-s', '-m', 'unittest', '-v', *names], cwd=HERE.parent,
                                            stdin=subprocess.DEVNULL, capture_output=True, text=True, timeout=600)
                except subprocess.TimeoutExpired as hung:
                    partial = hung.stderr or b''  # bytes, even with text=True
                    text = partial.decode(errors='replace') if isinstance(partial, bytes) else partial
                    lines = text.splitlines()
                    failed = [line for line in lines if line.endswith((' ... ERROR', ' ... FAIL'))]
                    # The last check named whose line has no result yet; it may have printed subtest lines (indented) since.
                    headers = [line for line in lines if re.match(r'test\w* \(', line)]
                    running = ['still running: ' + headers[-1]] if headers and headers[-1].rstrip().endswith(' ...') else []
                    self.fail('\n'.join(['%s: no result after %d s' % (python, hung.timeout)] + failed + running + ['...', text[-4000:]]))
                # unittest reports every error before every failure: name them all ahead of the tail.
                failing = [line for line in result.stderr.splitlines() if line.startswith(('ERROR: ', 'FAIL: '))]
                self.assertEqual(0, result.returncode, '\n'.join(failing + ['...', result.stderr[-4000:]]))


@unittest.skipUnless(sys.platform.startswith('linux') and binaries_available(), 'needs Linux and the PostgreSQL server binaries')
class LauncherChecks(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if FOREIGN_PROC:  # the launch checks compare PIDs that commands print with PIDs that /proc gives
            raise unittest.SkipTest(FOREIGN_PROC_WHY)

    def launch(self, *arguments, **options):
        process = launch(*arguments, **options)
        self.addCleanup(self.end, process)  # after a failed check: stop the launcher before the copy goes
        return process

    @staticmethod
    def end(process):
        if process.poll() is None:
            process.kill()
            process.wait()
        for stream in (process.stdout, process.stderr):
            if stream:
                stream.close()

    def assertNothingLeft(self, root, fixtures, sockets, errors):
        self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
        self.assertEqual([], list(fixtures.iterdir()) if fixtures.exists() else [], 'a cluster directory was left behind')
        if sockets:
            self.assertFalse(Path(sockets).exists(), 'the temporary socket directory was left behind')
        self.assertEqual([], processes_naming(root), 'a process from the run was left running')

    def socket_directory(self, line):
        self.assertIsNotNone(line, 'the launcher never named its temporary socket directory')
        path = line.rsplit(': ', 1)[1]
        self.addCleanup(shutil.rmtree, path, ignore_errors=True)
        return path

    def test_a_terminated_or_hung_up_launcher_stops_the_server_and_discards_the_cluster(self):
        for signum, whole_group in ((signal.SIGTERM, True), (signal.SIGHUP, False), (signal.SIGINT, True)):
            with self.subTest(signal=signum.name):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, WAITING + '# %s\n' % root)  # named, so a failed check can stop it
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                cluster = Path(line.rsplit(': ', 1)[1])
                child = int(process.stdout.readline())
                (os.killpg if whole_group else os.kill)(process.pid, signum)
                try:
                    process.wait(120)
                except subprocess.TimeoutExpired:
                    pass
                left = Path('/proc/%d' % child).exists()
                if left:  # it holds the launcher's output, so reading that would wait for it
                    os.kill(child, signal.SIGKILL)
                _, errors = finish(process, timeout=120)
                self.assertEqual(128 + signum, process.returncode, errors)
                self.assertFalse(left, 'the command was left running')
                self.assertFalse(cluster.exists(), 'the cluster directory was left behind')
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_while_the_command_starts_still_stops_the_command(self):
        for when, startable in (('after', True), ('before', True), ('before', False)):
            with self.subTest(signal=when + ' Popen starts the command', command='runs' if startable else 'cannot be started'):
                root, script, fixtures = private_copy(self)
                wrapper = root / 'signal_while_starting.py'
                wrapper.write_text(SIGNAL_WHILE_STARTING)
                if startable:
                    marker = 'signalled command %s' % root  # names root, so assertNothingLeft also looks for the command
                    command, command_line = 'import time  # %s\ntime.sleep(30)\n' % marker, None
                else:
                    marker = str(root / 'missing-command')
                    command, command_line = None, [marker]
                started = time.monotonic()
                process = self.launch(script, command, wrapper=[str(wrapper), when, marker], command_line=command_line)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process, timeout=90)
                # Also when Popen then fails: the held signal, not the failure to start, decides the exit status.
                self.assertEqual(143, process.returncode, errors)
                self.assertIn('Interrupted by signal 15', errors)
                # Not the 30 s that terminate() would wait for a command that inherited an ignored SIGTERM.
                self.assertLess(time.monotonic() - started, 25, errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_just_as_a_setup_step_starts_still_lets_the_step_finish(self):
        for step, marker in (('tee', "'tee'"), ('initdb', "bin/initdb'")):
            for when in ('after', 'before'):
                with self.subTest(step=step, signal=when + ' Popen starts it'):
                    root, script, fixtures = private_copy(self)
                    wrapper = root / 'signal_while_starting.py'
                    wrapper.write_text(SIGNAL_WHILE_STARTING)
                    started = time.monotonic()
                    process = self.launch(script, WAITING, wrapper=[str(wrapper), when, marker])
                    seen = []
                    sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                    _, errors = finish(process, timeout=120)
                    self.assertEqual(143, process.returncode, errors)
                    # Supervised like any interrupted step: tee's input is closed, initdb finishes before the stop.
                    self.assertNotIn('Command failed', errors)
                    self.assertLess(time.monotonic() - started, 25, errors)  # not STOP_GRACE spent on a tee left waiting
                    self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_command_ended_by_a_signal_gives_128_plus_its_number(self):
        for signum in (signal.SIGTERM, signal.SIGKILL):
            with self.subTest(signal=signum.name):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, 'import os, signal; os.kill(os.getpid(), signal.%s)' % signum.name)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process)
                self.assertEqual(128 + signum, process.returncode, errors)
                self.assertNotIn('Interrupted by signal', errors)  # the command was, not the launcher
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_process_that_uses_the_cluster_without_naming_it_is_waited_for(self):
        for how in WAYS_TO_USE_THE_CLUSTER:
            with self.subTest(how=how):
                root, script, fixtures = private_copy(self)
                release = root / 'release'
                # Cleanups run last first: the release (after a failed check too), then a bounded wait until the waiter has
                # seen it, so it ends before remove_copy() would have to stop it (its SIGQUIT dumps core where enabled).
                self.addCleanup(ended, release)
                self.addCleanup(release.touch)
                process = self.launch(script, LEAVES_A_PROCESS % (str(release), how))
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                data = Path(line.rsplit(': ', 1)[1]) / 'data'
                waiter = int(process.stdout.readline())
                deadline = time.monotonic() + 60
                while processes_naming(data) and time.monotonic() < deadline:  # until the cleanup has stopped the server
                    time.sleep(0.05)
                time.sleep(2)  # a cleanup that overlooked the waiter would have reported and exited by now
                self.assertTrue(Path('/proc/%d' % waiter).exists())
                self.assertIsNone(process.poll(), 'the cleanup ended while a process still used the cluster (%s)' % how)
                release.touch()
                _, errors = finish(process)
                self.assertEqual(0, process.returncode, errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_process_still_using_the_cluster_after_the_wait_is_reported_and_the_cluster_kept(self):
        for how in WAYS_TO_USE_THE_CLUSTER:
            with self.subTest(how=how):
                root, script, fixtures = private_copy(self)
                release = root / 'release'
                self.addCleanup(ended, release)  # as in the check above: the waiter outlives this check's launcher
                self.addCleanup(release.touch)
                wrapper = root / 'short_stop_grace.py'
                wrapper.write_text(SHORT_STOP_GRACE)
                process = self.launch(script, LEAVES_A_PROCESS % (str(release), how), wrapper=[str(wrapper), str(script.parent)])
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                line = read_until(process.stderr, STARTED, seen)
                self.assertIsNotNone(line, 'the launcher never reported its cluster')
                cluster = Path(line.rsplit(': ', 1)[1])
                waiter = int(process.stdout.readline())
                _, errors = finish(process, timeout=120)
                # The waiter outlives the 3 s wait: the cleanup must say so and must not delete what it still uses.
                self.assertEqual(1, process.returncode, errors)
                self.assertIn('Throwaway PostgreSQL stopped and exited: NO (pids [%d])' % waiter, errors)
                self.assertNotIn('Discarded', errors)
                self.assertTrue(cluster.exists(), 'the cluster was deleted while a process still used it')
                self.assertTrue(Path(sockets).exists(), 'the socket directory was removed while the cluster is kept')

    def test_the_cleanup_sees_every_process_of_the_server(self):
        capabilities = [('as this account', None)]
        if ROOT:  # without CAP_SYS_PTRACE, root cannot read the server account's working directories; in Docker's set, nor environments
            capabilities += [('without CAP_SYS_PTRACE', [CAP_SYS_PTRACE]), ("in Docker's default capability set", outside_dockers_default())]
        for name, drop in capabilities:
            with self.subTest(name):
                prefix, why_not = without_capabilities(drop) if drop else ((), None)
                if why_not:
                    self.skipTest(why_not)
                root, script, fixtures = private_copy(self)
                output, errors = finish(self.launch(script, SEES_THE_SERVER % str(script.parent), prefix=prefix))
                sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
                if sockets:
                    self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                seen = json.loads(output)
                self.assertTrue(seen['children'], 'the server had no other process (checkpointer, background writer, ...)')
                # Its children name neither the data directory nor PGDATA any more, but their titles name the cluster.
                self.assertEqual([], sorted(set([seen['postmaster']] + seen['children']) - set(seen['found'])), seen)
                self.assertNothingLeft(root, fixtures, sockets, errors)
                # With either readable, a program the server started is found where it keeps what that one shows.
                readable = any(seen['readable'].values())
                if 'Docker' in name and readable:
                    self.skipTest("root reads the server account's %s even in Docker's default capability set here"
                                  % ' and '.join(what for what, can in seen['readable'].items() if can))
                # Said exactly where the cleanup could not find a program the server started.
                (self.assertNotIn if readable else self.assertIn)(UNSEEN, errors)

    def test_a_proc_that_hides_the_servers_processes_is_reported(self):
        if not ROOT:
            self.skipTest('needs root, to mount a /proc that hides other accounts\' processes')
        # Root without CAP_SYS_PTRACE may ptrace none of the server account's processes, so that /proc lists none of them.
        ptrace = bool(effective_capabilities() >> CAP_SYS_PTRACE & 1)
        for name, drop in (("in Docker's default capability set", outside_dockers_default()), ("with this process's capabilities", [])):
            with self.subTest(name):
                hidden = CAP_SYS_PTRACE in drop or not ptrace
                prefix, why_not = hiding_processes(drop)
                if why_not:
                    self.skipTest(why_not)
                root, script, fixtures = private_copy(self)
                process = self.launch(script, 'pass', prefix=prefix)
                _, errors = finish(process)
                sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
                if sockets:
                    self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertEqual(0, process.returncode, errors)
                self.assertNothingLeft(root, fixtures, sockets, errors)
                (self.assertIn if hidden else self.assertNotIn)(HIDDEN, errors)
                self.assertNotIn(UNSEEN, errors)  # which would claim that the cleanup still finds the server's own processes

    def test_a_proc_of_another_pid_namespace_brings_the_true_status_line(self):
        # The launcher in a PID namespace of its own on the parent's /proc (unshare --pid without --mount-proc), sh its
        # init: postmaster.pid's PID is not the postmaster's in that /proc, and the launcher looks the postmaster up
        # there by its command line. The command prints whether that PID names any process in that /proc.
        cases = (('with this process\'s capabilities', [], False, None),
                 ("in Docker's default capability set", outside_dockers_default(), False, UNSEEN),
                 ("in Docker's default set, on a /proc with hidepid=ptraceable", outside_dockers_default(), True, HIDDEN))
        for name, drop, hide, said in cases:
            with self.subTest(name):
                prefix, why_not = in_pid_namespace(drop, hide)
                if why_not:
                    self.skipTest(why_not)
                root, script, fixtures = private_copy(self)
                process = self.launch(script, NAMES_A_PROCESS, prefix=prefix)
                output, errors = finish(process)
                sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
                if sockets:
                    self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertEqual(0, process.returncode, errors)
                self.assertNothingLeft(root, fixtures, sockets, errors)
                for line in (UNSEEN, HIDDEN):
                    (self.assertIn if line == said else self.assertNotIn)(line, errors)
                if not drop and output.strip() != 'False':
                    # Where that PID names some process here too, a launcher that did not look the postmaster up would
                    # read that process instead, and might well print nothing either.
                    self.skipTest("postmaster.pid's PID names a process in this /proc as well")

    @unittest.skipUnless(shutil.which('ipcrm'), 'needs ipcrm, to remove the shared memory segment that a killed postmaster leaves')
    def test_a_killed_postmaster_is_reported_and_its_cluster_kept(self):
        root, script, fixtures = private_copy(self)
        shared = set(Path('/dev/shm').glob('PostgreSQL.*'))  # to name, should the check fail, what the killed server left
        process = self.launch(script, KILLS_THE_POSTMASTER)
        seen = []
        sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
        line = read_until(process.stderr, STARTED, seen)
        self.assertIsNotNone(line, 'the launcher never reported its cluster')
        cluster = Path(line.rsplit(': ', 1)[1])
        killed = json.loads(process.stdout.readline())
        postmaster, (key, shmid) = killed['postmaster'], killed['segment']
        self.addCleanup(remove_segment, key, shmid, postmaster)  # once its other processes have ended (finish() waits)
        _, errors = finish(process, timeout=180)
        errors = ''.join(seen) + errors
        # Its other processes ended by themselves, but no stop removed its postmaster.pid: that is not a verified stop.
        self.assertEqual(1, process.returncode, errors)
        self.assertIn('Throwaway PostgreSQL stopped and exited: NO\n', errors)
        self.assertNotIn('Discarded', errors)
        self.assertTrue((cluster / 'data' / 'postmaster.pid').exists(), 'the cluster was deleted although its server never stopped')
        self.assertTrue(Path(sockets).exists(), 'the socket directory was removed while the cluster is kept')
        self.assertEqual([], processes_naming(root), 'a process from the run was left running')
        # Its dynamic shared memory, which no stop removed either, is in the cluster that is kept, not in /dev/shm.
        if killed['mapped'] is not None:
            if [path for path in killed['mapped'] if path.startswith('/dev/shm/PostgreSQL.')]:
                self.fail('the server kept its dynamic shared memory in /dev/shm, where its kill left (mapped by no process): %s'
                          % unmapped(set(Path('/dev/shm').glob('PostgreSQL.*')) - shared))
            self.assertTrue([path for path in killed['mapped'] if '/pg_dynshmem/' in path], killed['mapped'])
        self.assertTrue([path for path in (cluster / 'data' / 'pg_dynshmem').iterdir() if path.name.startswith('mmap.')],
                        "the killed server's dynamic shared memory is not in its cluster")
        remove_segment(key, shmid, postmaster)
        self.assertIsNone(segment(key, shmid), 'the shared memory segment that the killed postmaster left was not removed')

    def test_a_failed_setup_leaves_nothing_behind(self):
        runs = [('as the calling account', None)]
        if ROOT:
            runs.append(('as the server account without --run-as', 'postgres'))
        for name, user in runs:
            with self.subTest(name):
                interpreter = os.path.realpath(sys.executable) if user else sys.executable
                if user and subprocess.run(['runuser', '-u', user, '--', 'test', '-x', interpreter]).returncode:
                    self.skipTest('%s cannot run %s' % (user, interpreter))
                root, script, fixtures = private_copy(self, owner=server_uid() if user else None)
                process = self.launch(script, 'pass', '--bindir', '/nonexistent-postgresql-bindir',
                                 run_as=[] if user else RUN_AS, interpreter=interpreter, user=user)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                _, errors = finish(process)
                self.assertNotEqual(0, process.returncode)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_closed_terminal_or_broken_pipe_neither_stops_the_cleanup_nor_changes_the_exit_status(self):
        import pty  # POSIX only, like the termios module it needs
        variants = (('pipe', signal.SIGTERM), ('terminal', signal.SIGHUP), ('pipe, the command succeeds', None))
        for (variant, signum), buffered in itertools.product(variants, (True, False)):
            # Buffered is stderr as an ordinary shell gives it; PYTHONUNBUFFERED=1 makes it unbuffered.
            with self.subTest(variant, buffered=buffered):
                root, script, fixtures = private_copy(self)
                environment = {name: value for name, value in os.environ.items() if name != 'PYTHONUNBUFFERED'}
                if not buffered:
                    environment['PYTHONUNBUFFERED'] = '1'
                go = root / 'go'
                reader, writer = pty.openpty() if variant == 'terminal' else os.pipe()
                process = self.launch(script, WAITING if signum else UNTIL % str(go), stderr=writer, env=environment)
                os.close(writer)
                try:
                    text, deadline = '', time.monotonic() + 120
                    while STARTED not in text and time.monotonic() < deadline:
                        if select.select([reader], [], [], 1)[0]:
                            text += os.read(reader, 65536).decode(errors='replace')
                finally:
                    os.close(reader)  # nobody reads the launcher's stderr any more
                self.assertIn(STARTED, text)
                sockets = self.socket_directory(next((line.strip() for line in text.splitlines() if line.startswith(SOCKETS)), None))
                if signum:
                    child = int(process.stdout.readline())
                    os.killpg(process.pid, signum)
                else:
                    child = None
                    go.touch()  # the command finishes; every later status line meets the closed stream
                self.assertEqual(128 + signum if signum else 0, process.wait(180))
                process.stdout.close()
                self.assertEqual([], list(fixtures.iterdir()), 'a cluster directory was left behind')
                self.assertFalse(Path(sockets).exists(), 'the temporary socket directory was left behind')
                self.assertEqual([], processes_naming(root))
                if child:
                    self.assertFalse(Path('/proc/%d' % child).exists(), 'the command was left running')

    def test_a_signal_during_initdb_leaves_nothing_behind(self):
        for signum, whole_group in ((signal.SIGTERM, False), (signal.SIGHUP, False), (signal.SIGINT, True)):
            with self.subTest(signal=signum.name, whole_group=whole_group):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, WAITING)
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                deadline, family = time.monotonic() + 60, {}
                while not family and process.poll() is None and time.monotonic() < deadline:
                    family = initdb_with_its_backend(root)
                    time.sleep(0 if family else 0.002)
                self.assertTrue(family, 'initdb was never seen running its backend')
                (os.killpg if whole_group else os.kill)(process.pid, signum)
                lost = set()
                while process.poll() is None and any(process_state(pid) for pid in family) and time.monotonic() < deadline + 60:
                    lost.update(orphaned(family))
                    time.sleep(0.002)
                _, errors = finish(process)
                self.assertEqual(128 + signum, process.returncode, errors)
                # run() waited for initdb (StepTests pin that a step within STEP_GRACE is left to finish): after a signal
                # to the launcher alone, initdb and the process running its backend (popen's sh -c) kept their parent
                # (runuser as root, else the launcher) until they ended, which a step killed at once would not, and the
                # cleanup's stop never met the backend. A step sent SIGTERM at once (StepTests catch that) may well keep
                # it: as root, runuser lets initdb finish its phase.
                if not whole_group:  # then initdb, its backend and runuser are signalled themselves
                    self.assertEqual([], sorted(lost), 'initdb or the process running its backend lost its parent while it still ran')
                self.assertNotIn('Command failed', errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_repeated_group_signal_does_not_cut_the_cleanup_short(self):
        root, script, fixtures = private_copy(self)
        process = self.launch(script, WAITING)
        seen = []
        sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
        self.assertIsNotNone(read_until(process.stderr, STARTED, seen))
        int(process.stdout.readline())
        os.killpg(process.pid, signal.SIGTERM)
        self.assertIsNotNone(read_until(process.stderr, 'Interrupted by signal', seen))
        deadline = time.monotonic() + 120
        while process.poll() is None and time.monotonic() < deadline:  # every 20 ms until the cleanup is over
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except ProcessLookupError:
                break
            time.sleep(0.02)
        _, errors = finish(process)
        self.assertEqual(143, process.returncode, errors)
        # The cleanup's stop ran in its own session: no repeated SIGTERM made runuser kill it.
        self.assertNotIn('Command failed', errors)
        self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_signal_during_the_cleanup_after_the_command_succeeded_is_ignored(self):
        for whole_group in (False, True):
            with self.subTest(whole_group=whole_group):
                root, script, fixtures = private_copy(self)
                process = self.launch(script, 'pass')
                seen = []
                sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
                deadline, stopping = time.monotonic() + 60, False
                while not stopping and process.poll() is None and time.monotonic() < deadline:
                    stopping = bool(processes_naming(root, b'\0stop\0'))  # the cleanup's pg_ctl stop
                    time.sleep(0 if stopping else 0.002)
                self.assertTrue(stopping, 'the cleanup was never seen stopping the server')
                try:
                    (os.killpg if whole_group else os.kill)(process.pid, signal.SIGTERM)
                except ProcessLookupError:
                    pass
                _, errors = finish(process)
                self.assertEqual(0, process.returncode, errors)
                self.assertNotIn('Command failed', errors)
                self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_a_hangup_ignored_at_start_stays_ignored_for_the_whole_run(self):
        root, script, fixtures = private_copy(self)
        process = self.launch(script, HANGUP_PROBE, ignore=(signal.SIGHUP,))
        seen = []
        sockets = self.socket_directory(read_until(process.stderr, SOCKETS, seen))
        self.assertEqual('SIGHUP ignored: True', process.stdout.readline().strip())
        os.killpg(process.pid, signal.SIGHUP)  # as when the terminal of a nohup run closes
        output, errors = finish(process)
        self.assertEqual(0, process.returncode, errors)
        self.assertIn('finished normally', output)
        self.assertNothingLeft(root, fixtures, sockets, ''.join(seen) + errors)

    def test_libpq_variables_never_reach_a_client_and_only_the_server_account_owns_its_files(self):
        hostile = dict(os.environ, PGHOSTADDR='192.0.2.1', PGPORT='1', PGSERVICE='synthetic-missing-service')
        for layout in ('deep', 'short'):
            with self.subTest(checkout=layout):
                root, script, fixtures = private_copy(self, short=layout == 'short')
                output, errors = finish(self.launch(script, PROBE, env=hostile))
                sockets = next((line.rsplit(': ', 1)[1] for line in errors.splitlines() if line.startswith(SOCKETS)), None)
                if sockets:
                    self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertNothingLeft(root, fixtures, sockets, errors)
                seen = json.loads(output)
                self.assertEqual([], seen['libpq'])
                self.assertTrue(seen['dsn'].startswith('host=/'), seen['dsn'])
                if layout == 'deep':  # too long for a socket in the cluster: a private directory under /tmp
                    self.assertEqual(Path('/tmp'), Path(seen['sockets']).parent)
                    self.assertEqual(sockets, seen['sockets'], 'the socket directory was not named on stderr')
                else:  # <cluster>/s
                    self.assertEqual(fixtures, Path(seen['sockets']).parent.parent)
                    self.assertIsNone(sockets, 'a socket directory inside the cluster was announced as temporary')
                self.assertEqual([0o700, server_uid()], seen['socket_dir'])
                if RUN_AS:
                    server = server_uid()
                    self.assertEqual({'cluster': 0, 'metadata': 0, 'data': server, 'config': server, 'log': server}, seen['owners'])

    def test_a_deep_checkout_gets_a_private_socket_directory_under_tmp_whatever_tmpdir_says(self):
        launcher.FIXTURES.parent.mkdir(parents=True, exist_ok=True)  # build/ is gitignored, so a fresh clone has none
        scratch = Path(tempfile.mkdtemp(prefix='launcher-tmpdir-', dir=launcher.FIXTURES.parent))
        self.addCleanup(shutil.rmtree, scratch, ignore_errors=True)
        closed = scratch / 'closed'
        closed.mkdir(mode=0o700)  # like a per-user TMPDIR (pam_tmpdir): the server account cannot enter it
        long = scratch / ('t' * 90)
        long.mkdir()
        for name, tmpdir in (('unset', None), ('closed to the server account', closed), ('long', long)):
            with self.subTest(TMPDIR=name):
                root, script, fixtures = private_copy(self)
                environment = {key: value for key, value in os.environ.items() if key not in ('TMPDIR', 'TEMP', 'TMP')}
                if tmpdir:
                    environment['TMPDIR'] = str(tmpdir)
                output, errors = finish(self.launch(script, PROBE, env=environment))
                self.assertIn('Throwaway PostgreSQL stopped and exited: yes', errors)
                seen = json.loads(output)
                sockets = Path(seen['sockets'])
                self.addCleanup(shutil.rmtree, sockets, ignore_errors=True)
                self.assertEqual(Path('/tmp'), sockets.parent)
                self.assertTrue(sockets.name.startswith('mhpg-'))
                self.assertEqual([0o700, server_uid()], seen['socket_dir'])
                self.assertNothingLeft(root, fixtures, str(sockets), errors)


if __name__ == '__main__':
    unittest.main()

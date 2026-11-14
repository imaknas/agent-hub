"""The Linux backend for a sandboxed project's own services and command jobs (bin/sandboxed-run, through
isolation.py): neutral policy (policy.py) → Landlock rules + a small seccomp filter, applied to the sandboxed-run
process itself right before it execs the command (inherited by every child, can't be undone). Unprivileged, standard
library only (ctypes); no bubblewrap, no user namespaces. Design and what was measured: docs/design/capabilities.md,
"10-31 (Linux)".

Why Landlock and not the unit's own sandboxing or bubblewrap (measured 10-31, Ubuntu kernel 6.8 with
kernel.apparmor_restrict_unprivileged_userns=1, the Ubuntu 24.04 default): unprivileged user namespaces are refused,
so bubblewrap can't start, and a systemd *user* unit silently drops every mount-namespace directive (ProtectSystem,
ProtectHome, ReadWritePaths, InaccessiblePaths, PrivateTmp, PrivateUsers: "Failed to set up namespace, assuming
containerized execution and ignoring") as well as SocketBindAllow/Deny and IPAddressAllow/Deny (no BPF for a user
manager) and starts the service anyway. Landlock needs neither and works the same for services and scheduler jobs.

What it enforces:
  write    only its own runtime/workspace, policy.write (allow_write), the temp areas and a few devices; not the bus
           (its letters go through its outbox: docs/design/bus-sender.md)
  read     everything except: workspace_root and services_root (own folders allowed again), other projects, platform
           state (.state, policy.platform_state: .logs, the data and evals dirs) and the bus directory, policy.deny_read (the memory copies' raw notes, the platform's secrets),
           credentials (directories and files). Landlock only allows, it
           can't carve a hole in an allowed tree, so a denied path is cut out by allowing its siblings at every level
           instead (the walk below); anything created later next to a denied path is not readable (fail closed)
  network  bind: only expose.local_port (TCP; Landlock ABI ≥ 4 = kernel 6.7); outbound open, like macOS (no domain
           filtering without a network namespace + proxy, which needs user namespaces)
  other    unix-domain sockets can't be created (seccomp: socket(AF_UNIX) → EACCES; socketpair still works), which
           keeps it off the user's D-Bus and systemd's private socket (otherwise `systemd-run --user` would start an
           unsandboxed program; Landlock on 6.8 doesn't cover connecting to a pathname socket); io_uring is refused (it
           can create sockets without socket()); no_new_privs (setuid/setgid programs such as crontab gain nothing);
           ptrace and /proc/<pid>/environ of processes outside the sandbox are refused by Landlock itself (measured);
           signals to other processes and abstract unix sockets are scoped only with ABI ≥ 6 (kernel 6.12)
Can't enforce (the adapter's limits): policy.protect and protect_roots (what the project's sessions load settings and
hooks from, and the runtime's own folder in the project, inside
its own writable folder: Landlock can only allow, and a write right on a folder covers everything under it, so it
doesn't declare runtime-files-locked; there the platform's renderer removes hooks it didn't write), policy.links (no
symbolic link in its own checkouts: Landlock has a make-symlink right, but it is granted per folder that exists when
the rules are made, so the tool folders made later (a new node_modules, .venv) could get no links; not built, so it
doesn't declare links-blocked, docs/design/git-broker.md r3 B-1), the listening address (a service may bind 0.0.0.0:<its port>; macOS accepts
only localhost), UDP binds, the network allowlist (policy.network), signals to the owner's other processes below ABI 6.

  can_sandbox() → None | why not       guarantees() → set[str]       plan(pol, dirs, port) → {read, write, bind}
  exec_service(pol, dirs, port, argv)
"""
from __future__ import annotations

import ctypes
import glob
import os
import pathlib
import platform
import struct
import sys

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent))
import hubconf  # noqa: E402

# syscalls (the same numbers on every architecture: added after the unified table)
SYS_CREATE_RULESET, SYS_ADD_RULE, SYS_RESTRICT_SELF = 444, 445, 446
SYS_IO_URING_SETUP = 425
CREATE_RULESET_VERSION = 1
RULE_PATH_BENEATH, RULE_NET_PORT = 1, 2

FS = {"execute": 1 << 0, "write_file": 1 << 1, "read_file": 1 << 2, "read_dir": 1 << 3, "remove_dir": 1 << 4,
      "remove_file": 1 << 5, "make_char": 1 << 6, "make_dir": 1 << 7, "make_reg": 1 << 8, "make_sock": 1 << 9,
      "make_fifo": 1 << 10, "make_block": 1 << 11, "make_sym": 1 << 12, "refer": 1 << 13, "truncate": 1 << 14,
      "ioctl_dev": 1 << 15}
FS_BY_ABI = {1: (1 << 13) - 1, 2: (1 << 14) - 1, 3: (1 << 15) - 1, 5: (1 << 16) - 1}   # 4 adds net only, 6 scopes
NET_BIND_TCP = 1
SCOPE_ABSTRACT_UNIX, SCOPE_SIGNAL = 1, 2
FILE_ONLY = FS["execute"] | FS["write_file"] | FS["read_file"] | FS["truncate"] | FS["ioctl_dev"]
READ = FS["execute"] | FS["read_file"] | FS["read_dir"]
MIN_ABI = 4   # TCP bind rules
SIGNAL_ABI = 6   # signals and abstract unix sockets scoped to the sandbox

TMP = ["/tmp", "/var/tmp", "/dev/shm"]
DEVICES = ["/dev/null", "/dev/zero", "/dev/random", "/dev/urandom", "/dev/tty", "/dev/full"]

# seccomp: per architecture (audit arch, socket's number)
PR_SET_NO_NEW_PRIVS, PR_SET_SECCOMP, SECCOMP_MODE_FILTER = 38, 22, 2
ARCH = {"x86_64": (0xC000003E, 41), "aarch64": (0xC00000B7, 198)}
X32_BIT = 0x40000000
AF_UNIX = 1
EACCES = 13

_libc = None


def libc():
    global _libc
    if _libc is None:
        _libc = ctypes.CDLL(None, use_errno=True)
        _libc.syscall.restype = ctypes.c_long
    return _libc


def abi() -> int:
    """The kernel's Landlock ABI version; 0 = none (not Linux, compiled out, or disabled at boot)."""
    if not sys.platform.startswith("linux"):
        return 0
    r = libc().syscall(SYS_CREATE_RULESET, None, ctypes.c_size_t(0), ctypes.c_uint32(CREATE_RULESET_VERSION))
    return int(r) if r > 0 else 0


def can_sandbox() -> str | None:
    """None when services and jobs can run under these rules here; otherwise why not (the caller refuses)."""
    if not sys.platform.startswith("linux"):
        return f"Landlock is Linux only ({sys.platform})"
    if platform.machine() not in ARCH:
        return f"the seccomp filter has no entry for {platform.machine()}"
    v = abi()
    if v < MIN_ABI:
        return (f"the kernel's Landlock ABI is {v or 'missing'}, ports need ≥ {MIN_ABI} (Linux 6.7, and landlock in "
                "the lsm= boot list)")
    return None


def confined_now(marked: bool = False) -> bool:
    """isolation.py's backend interface: whether this process runs under this adapter's rules, as far as the kernel
    shows it: no_new_privs and a seccomp filter, both of which sandboxed-run sets before it execs (Landlock itself
    can't be queried). Flatpak, bubblewrap and hardened systemd units show the same, so it counts only when the process
    was also started through sandboxed-run (marked); isolation.confined_now then probes the reads too. Landlock stacks,
    so a "no" only nests a second sandbox."""
    if not marked:
        return False
    try:
        status = pathlib.Path("/proc/self/status").read_text()
    except OSError:
        return False
    fields = dict(line.split(":", 1) for line in status.splitlines() if ":" in line)
    return fields.get("NoNewPrivs", "").strip() == "1" and fields.get("Seccomp", "").strip() == "2"


def guarantees() -> set[str]:
    """isolation.py's backend interface: what plan + the seccomp filter enforce on this kernel (capabilities.md,
    "What each OS enforces"); not the listening address, so not localhost-inbound-only."""
    return {"own-writes-only", "isolated-reads", "declared-port-only", "unix-sockets-blocked",
            *(["signals-blocked"] if abi() >= SIGNAL_ABI else [])}


def fs_handled(v: int) -> int:
    return FS_BY_ABI[max(k for k in FS_BY_ABI if k <= v)]


# ---------- the plan: pure path computation, testable anywhere ----------

def _under(p: pathlib.Path, root: pathlib.Path) -> bool:
    return p == root or root in p.parents


def denied(pol: dict) -> list[pathlib.Path]:
    """Every path the service may not read (resolved), globs expanded now; paths that don't exist yet still count: their
    parents are walked instead of allowed whole, so they stay unreadable if created later."""
    # .state in its own fixed list as well as the policy's platform_state (.logs, the data and evals dirs): a plan built
    # from any policy still denies it
    out = [hubconf.p("workspace_root"), hubconf.p("services_root"), *pol["other_projects"], *pol["credentials"],
           *pol["credential_files"], hubconf.STATE, hubconf.BUS_DIR, *(pol.get("platform_state") or [])]
    for p in pol["deny_read"]:
        # a glob: what matches now, plus the pattern itself, so the folder holding the matches is walked rather than
        # allowed whole even while nothing matches (a later match stays unreadable)
        out += [p, *glob.glob(str(p))]
    return sorted({pathlib.Path(os.path.realpath(x)) for x in out if x})


def read_roots(deny: list[pathlib.Path], root: pathlib.Path = pathlib.Path("/")) -> list[pathlib.Path]:
    """The complement of `deny` under `root` as a list of paths to allow whole: a path with no denied path inside it is
    allowed; one that is denied is skipped; one with something denied inside is walked into (its entries, not itself).
    A symlink is allowed only if what it points to is allowed on the same terms (Landlock follows it)."""
    out: list[pathlib.Path] = []

    def ok_target(p: pathlib.Path) -> bool:
        t = pathlib.Path(os.path.realpath(p))
        return not any(_under(t, d) or _under(d, t) for d in deny)

    def walk(p: pathlib.Path):
        if any(_under(p, d) for d in deny):
            return
        if not any(_under(d, p) for d in deny):
            if not p.is_symlink() or ok_target(p):
                out.append(p)
            return
        if p.is_symlink() or not p.is_dir():
            return
        try:
            entries = sorted(p.iterdir())
        except OSError:
            return
        for e in entries:
            walk(e)

    walk(root)
    return out


def plan(pol: dict, dirs: list, port: int | None = None) -> dict:
    """{read: [Path] (read + execute), write: [Path] (everything), bind: [port]}: what exec_service turns into rules."""
    own = [pathlib.Path(os.path.realpath(d)) for d in dirs]
    extra = [pathlib.Path(os.path.realpath(hubconf.path(w))) for w in pol["write"]]
    write = [*own, *extra, *[pathlib.Path(t) for t in TMP]]
    readable = [pathlib.Path(os.path.realpath(r)) for r in pol.get("read") or []]   # the registry index (policy.read)
    return {"read": [*read_roots(denied(pol)), *readable], "write": write, "devices": [pathlib.Path(d) for d in DEVICES],
            "bind": [int(port)] if port else []}


# ---------- applying it ----------

def _add_path(fd: int, path: pathlib.Path, access: int) -> None:
    try:
        pfd = os.open(path, os.O_PATH | os.O_CLOEXEC)
    except OSError:
        return   # gone between planning and now: nothing to allow
    try:
        if not os.path.isdir(path):
            access &= FILE_ONLY
        attr = struct.pack("=Qi", access, pfd)   # landlock_path_beneath_attr is packed
        buf = ctypes.create_string_buffer(attr, len(attr))
        if libc().syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_PATH_BENEATH), buf, ctypes.c_uint32(0)):
            e = ctypes.get_errno()
            raise OSError(e, f"landlock_add_rule {path}: {os.strerror(e)}")
    finally:
        os.close(pfd)


def _seccomp() -> None:
    """socket(AF_UNIX) → EACCES, io_uring_setup → EACCES, a foreign architecture or x32 → EACCES, all else allowed."""
    audit, nr_socket = ARCH[platform.machine()]
    ld, jeq, jge, ret = 0x20, 0x15, 0x35, 0x06   # BPF_LD|W|ABS, BPF_JMP|JEQ|K, BPF_JMP|JGE|K, BPF_RET|K
    allow, deny = 0x7FFF0000, 0x00050000 | EACCES
    prog = [
        (ld, 0, 0, 4),                       # arch
        (jeq, 1, 0, audit),
        (ret, 0, 0, deny),
        (ld, 0, 0, 0),                       # nr
        (jge, 5, 0, X32_BIT),                # x32 on x86_64 (never reached on aarch64)
        (jeq, 4, 0, SYS_IO_URING_SETUP),
        (jeq, 1, 0, nr_socket),
        (ret, 0, 0, allow),
        (ld, 0, 0, 16),                      # args[0], low 32 bits
        (jeq, 0, 1, AF_UNIX),
        (ret, 0, 0, deny),
        (ret, 0, 0, allow),
    ]
    raw = b"".join(struct.pack("=HBBI", *i) for i in prog)
    buf = ctypes.create_string_buffer(raw, len(raw))

    class Fprog(ctypes.Structure):
        _fields_ = [("len", ctypes.c_ushort), ("filter", ctypes.c_void_p)]
    fp = Fprog(len(prog), ctypes.cast(buf, ctypes.c_void_p))
    if libc().prctl(PR_SET_SECCOMP, SECCOMP_MODE_FILTER, ctypes.byref(fp), 0, 0):
        e = ctypes.get_errno()
        raise OSError(e, f"seccomp: {os.strerror(e)}")


def restrict(p: dict) -> None:
    """Apply a plan to this process (and everything it starts). Raises OSError when anything can't be applied: the
    caller refuses instead of running the command."""
    v = abi()
    if v < MIN_ABI:
        raise OSError(0, can_sandbox() or "no Landlock")
    fs = fs_handled(v)
    scoped = (SCOPE_ABSTRACT_UNIX | SCOPE_SIGNAL) if v >= SIGNAL_ABI else 0
    attr = struct.pack("=QQQ", fs, NET_BIND_TCP, scoped) if v >= SIGNAL_ABI else struct.pack("=QQ", fs, NET_BIND_TCP)
    buf = ctypes.create_string_buffer(attr, len(attr))
    fd = libc().syscall(SYS_CREATE_RULESET, buf, ctypes.c_size_t(len(attr)), ctypes.c_uint32(0))
    if fd < 0:
        e = ctypes.get_errno()
        raise OSError(e, f"landlock_create_ruleset: {os.strerror(e)}")
    try:
        for r in p["read"]:
            _add_path(fd, r, READ & fs)
        for w in p["write"]:
            _add_path(fd, w, fs)
        for d in p["devices"]:
            _add_path(fd, d, (FS["read_file"] | FS["write_file"] | FS["ioctl_dev"]) & fs)
        for port in p["bind"]:
            a = struct.pack("=QQ", NET_BIND_TCP, port)
            b = ctypes.create_string_buffer(a, len(a))
            if libc().syscall(SYS_ADD_RULE, ctypes.c_int(fd), ctypes.c_int(RULE_NET_PORT), b, ctypes.c_uint32(0)):
                e = ctypes.get_errno()
                raise OSError(e, f"landlock_add_rule port {port}: {os.strerror(e)}")
        if libc().prctl(PR_SET_NO_NEW_PRIVS, 1, 0, 0, 0):
            e = ctypes.get_errno()
            raise OSError(e, f"no_new_privs: {os.strerror(e)}")
        _seccomp()
        if libc().syscall(SYS_RESTRICT_SELF, ctypes.c_int(fd), ctypes.c_uint32(0)):
            e = ctypes.get_errno()
            raise OSError(e, f"landlock_restrict_self: {os.strerror(e)}")
    finally:
        os.close(fd)


def exec_service(pol: dict, dirs: list, port: int | None, argv: list[str]):
    """Restrict this process and exec argv (looked up on PATH). Raises OSError when the sandbox can't be applied
    (nothing was run); a command that can't start under it exits 127."""
    # a rule needs the path to exist (an fd is opened): create a missing writable folder whose parent exists, not
    # whole trees that belong to another OS (~/Library/Caches/… on Linux)
    for w in pol["write"]:
        w = hubconf.path(w)
        if not w.exists() and w.parent.is_dir():
            try:
                w.mkdir()
            except OSError:
                pass
    restrict(plan(pol, dirs, port))
    try:
        os.execvp(argv[0], argv)
    except OSError as e:   # sandboxed fine, the command itself can't start: say that, like a shell would
        print(f"sandboxed-run: {argv[0]}: {e.strerror}", file=sys.stderr)
        os._exit(127)


if __name__ == "__main__":  # landlock.py <project> [port]: print the plan (for debugging)
    import policy
    import registry
    doc = hubconf.projects()[sys.argv[1]]
    d = registry.dirs(doc)
    pl = plan(policy.for_project(sys.argv[1]), [x for x in (d["runtime"], d["workspace"]) if x],
              int(sys.argv[2]) if len(sys.argv) > 2 else None)
    for k, v in pl.items():
        print(f"{k}: {len(v)}")
        for x in v:
            print(f"  {x}")

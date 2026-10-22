#!/usr/bin/env python3
"""ssh-registry.py: device + location alias registry with a dry-run path resolver.

Two levels: a device alias maps a short name to an ssh target (host, optional
user, optional port); a location alias maps a short name to an absolute
directory path on that device. A location is a leaf; nothing nests under it.

This tool never moves a byte: no push, pull, scp, ssh, or any network call
anywhere in this file. `resolve` is pure string and path computation, and the
transfer command it prints is explicitly a dry run.

Standard library only, matching tools/close-turn.py's own constraint.
"""

from __future__ import annotations

import argparse
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

DEFAULT_CONFIG = Path.home() / ".ssh-registry.toon"
DEFAULT_PORT = 22
DRY_RUN_MARKER = "NOT EXECUTED"

_THIS_SCRIPT = Path(__file__).resolve()


# ---------------------------------------------------------------------------
# TOON-shaped config reader / writer
# ---------------------------------------------------------------------------

def parse_config(text: str) -> dict:
    """Parse a TOON-shaped nested mapping of scalars into a dict.

    Hand-rolled minimal reader for exactly this nested-mapping shape (devices
    -> aliases -> scalar fields, plus a locations mapping); it is NOT a full
    implementation of the TOON spec at https://toonformat.dev/. There is no
    TOON library available to this stdlib-only script and no `toon` CLI is
    shelled out to; spec compliance is limited to what this reader and writer
    actually implement and test. Only `key: value` lines are supported: a line
    with no value begins a nested mapping, a line with a value is a scalar.
    Lists, quoted strings, inline comments, and the rest of TOON are not
    implemented.
    """
    root = {}
    stack = [(0, root)]
    for raw in text.splitlines():
        stripped = raw.strip()
        if not stripped or stripped.startswith("#"):
            continue
        indent = len(raw) - len(raw.lstrip(" \t"))
        if ":" not in stripped:
            raise ValueError(f"not a 'key: value' line: {raw!r}")
        key, _, value = stripped.partition(":")
        key = key.strip()
        value = value.strip()
        while len(stack) > 1 and indent <= stack[-1][0]:
            stack.pop()
        parent = stack[-1][1]
        if value:
            parent[key] = value
        else:
            child = {}
            parent[key] = child
            stack.append((indent, child))
    return root


def dump_config(data: dict) -> str:
    """Serialize the nested mapping back to TOON-shaped text, 2-space indent.

    The same hand-rolled minimal writer as the reader above: it handles only
    nested mappings of scalars and claims no full TOON spec compliance.
    """
    return "\n".join(_dump_mapping(data)) + "\n"


def _dump_mapping(mapping: dict, indent: int = 0) -> list[str]:
    lines = []
    for key, value in mapping.items():
        pad = "  " * indent
        if isinstance(value, dict):
            lines.append(f"{pad}{key}:")
            lines.extend(_dump_mapping(value, indent + 1))
        else:
            lines.append(f"{pad}{key}: {value}")
    return lines


def load_config(path: Path) -> dict:
    """Load config from disk. A missing file reads as an empty registry."""
    if not path.is_file():
        return {"devices": {}}
    data = parse_config(path.read_text(encoding="utf-8"))
    if "devices" not in data:
        data["devices"] = {}
    devices = data["devices"]
    if not isinstance(devices, dict):
        raise ValueError("config 'devices' must be a mapping")
    for alias, dev in devices.items():
        if not isinstance(dev, dict):
            raise ValueError(f"device {alias!r} must be a mapping")
        if "port" in dev:
            try:
                dev["port"] = int(dev["port"])
            except (TypeError, ValueError):
                raise ValueError(f"device {alias!r} has a non-integer port {dev['port']!r}")
        if "locations" not in dev:
            dev["locations"] = {}
        if not isinstance(dev["locations"], dict):
            raise ValueError(f"device {alias!r} 'locations' must be a mapping")
    return data


def save_config(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(dump_config(data), encoding="utf-8")


# ---------------------------------------------------------------------------
# errors
# ---------------------------------------------------------------------------

def die(message: str) -> None:
    print(f"error: {message}", file=sys.stderr)
    sys.exit(1)


# ---------------------------------------------------------------------------
# device commands
# ---------------------------------------------------------------------------

def cmd_device_add(args, config_path: Path) -> None:
    data = load_config(config_path)
    devices = data["devices"]
    if args.alias in devices:
        die(f"device {args.alias!r} already exists; refusing to overwrite")
    if not args.host:
        die("--host is required and must be non-empty")
    port = None
    if args.port is not None:
        if not (1 <= args.port <= 65535):
            die(f"invalid port {args.port}: must be 1-65535")
        if args.port != DEFAULT_PORT:
            port = args.port
    ordered = {"host": args.host}
    if args.user:
        ordered["user"] = args.user
    if port is not None:
        ordered["port"] = port
    ordered["locations"] = {}
    devices[args.alias] = ordered
    save_config(config_path, data)
    print(f"registered device {args.alias!r} (host={args.host})")


def cmd_device_list(config_path: Path) -> None:
    devices = load_config(config_path)["devices"]
    if not devices:
        print("no devices registered")
        return
    for alias, dev in devices.items():
        print(alias)
        print(f"  host: {dev['host']}")
        print(f"  user: {dev.get('user', '(none)')}")
        print(f"  port: {dev.get('port', DEFAULT_PORT)}")
        locs = dev.get("locations", {})
        if locs:
            print("  locations:")
            for name, path in locs.items():
                print(f"    {name}: {path}")
        else:
            print("  locations: (none)")


# ---------------------------------------------------------------------------
# location commands
# ---------------------------------------------------------------------------

def cmd_location_add(args, config_path: Path) -> None:
    data = load_config(config_path)
    devices = data["devices"]
    if args.device_alias not in devices:
        die(f"unknown device {args.device_alias!r} (register it first with 'device add')")
    if not args.path:
        die("location path must be non-empty")
    locs = devices[args.device_alias]["locations"]
    if args.location_alias in locs:
        die(f"location {args.location_alias!r} already exists under device "
            f"{args.device_alias!r}; refusing to overwrite")
    locs[args.location_alias] = args.path
    save_config(config_path, data)
    print(f"registered location {args.location_alias!r} under device "
          f"{args.device_alias!r} ({args.path})")


def cmd_location_list(args, config_path: Path) -> None:
    devices = load_config(config_path)["devices"]
    if args.device_alias not in devices:
        die(f"unknown device {args.device_alias!r}")
    locs = devices[args.device_alias]["locations"]
    print(f"locations under {args.device_alias}:")
    if not locs:
        print("  (none)")
    for name, path in locs.items():
        print(f"  {name}: {path}")


# ---------------------------------------------------------------------------
# resolve (pure string and path computation; never touches the network)
# ---------------------------------------------------------------------------

def parse_spec(spec: str) -> tuple[str, str, str | None]:
    """Parse '<device>:<location>[/<relpath>]'. Errors are fatal."""
    if ":" not in spec:
        die(f"invalid spec {spec!r}: expected <device>:<location>[/<relpath>]")
    device, rest = spec.split(":", 1)
    device = device.strip()
    if not device or not rest:
        die(f"invalid spec {spec!r}: expected <device>:<location>[/<relpath>]")
    location, sep, relpath = rest.partition("/")
    location = location.strip()
    if not location:
        die(f"invalid spec {spec!r}: missing location")
    if sep and not relpath.strip():
        relpath = None
    return device, location, relpath


def guard_relpath(relpath: str | None) -> None:
    """Refuse a relpath that walks up out of the registered location."""
    if not relpath:
        return
    segments = re.split(r"[\\/]", relpath)
    if ".." in segments:
        die(f"refusing relpath {relpath!r}: contains a '..' segment (path traversal)")


def base_target(dev: dict) -> str:
    host = dev["host"]
    user = dev.get("user")
    return f"{user}@{host}" if user else host


def ssh_target(dev: dict) -> str:
    target = base_target(dev)
    port = dev.get("port")
    if port and port != DEFAULT_PORT:
        target = f"{target} -p {port}"
    return target


def resolve_side(spec: str, data: dict, which: str):
    """Resolve one --from/--to spec.

    Returns (resolved_abs_path, ssh_target, base_target, port_or_None).
    """
    device, location, relpath = parse_spec(spec)
    devices = data["devices"]
    if device not in devices:
        die(f"unknown device {device!r} in {which} spec {spec!r}")
    locs = devices[device].get("locations", {})
    if location not in locs:
        die(f"unknown location {location!r} under device {device!r} "
            f"in {which} spec {spec!r}")
    guard_relpath(relpath)
    dev = devices[device]
    resolved = locs[location]
    if relpath:
        resolved = posixpath_join(resolved, relpath)
    return resolved, ssh_target(dev), base_target(dev), dev.get("port")


def posixpath_join(base: str, rel: str) -> str:
    """Join with forward slashes regardless of this host's OS, so the resolved
    path reads correctly for both a POSIX and a Windows-style registered path."""
    if not base.endswith("/"):
        base = base + "/"
    return base + rel


def cmd_resolve(args, config_path: Path) -> None:
    data = load_config(config_path)
    f_path, f_target, f_base, f_port = resolve_side(args.from_spec, data, "--from")
    t_path, t_target, t_base, t_port = resolve_side(args.to_spec, data, "--to")
    print("from:")
    print(f"  path: {f_path}")
    print(f"  target: {f_target}")
    print("to:")
    print(f"  path: {t_path}")
    print(f"  target: {t_target}")
    port_flag = ""
    for p in (f_port, t_port):
        if p and p != DEFAULT_PORT:
            port_flag = f" -P {p}"
            break
    transfer = f"scp{port_flag} {f_base}:{f_path} {t_base}:{t_path}"
    print(f"transfer (dry run): the command below is {DRY_RUN_MARKER}")
    print(f"  {transfer}")


# ---------------------------------------------------------------------------
# argparse
# ---------------------------------------------------------------------------

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="ssh-registry",
        description="device + location alias registry with a dry-run path resolver")
    sub = parser.add_subparsers(dest="command")

    dev = sub.add_parser("device", help="manage device aliases")
    dev_sub = dev.add_subparsers(dest="device_command", required=True)
    da = dev_sub.add_parser("add", help="register a device")
    da.add_argument("alias")
    da.add_argument("--host", required=True)
    da.add_argument("--user")
    da.add_argument("--port", type=int)
    da.add_argument("--config", default=None)
    dl = dev_sub.add_parser("list", help="list devices")
    dl.add_argument("--config", default=None)

    loc = sub.add_parser("location", help="manage location aliases under a device")
    loc_sub = loc.add_subparsers(dest="location_command", required=True)
    la = loc_sub.add_parser("add", help="register a location under a device")
    la.add_argument("device_alias")
    la.add_argument("location_alias")
    la.add_argument("path")
    la.add_argument("--config", default=None)
    ll = loc_sub.add_parser("list", help="list locations under a device")
    ll.add_argument("device_alias")
    ll.add_argument("--config", default=None)

    res = sub.add_parser("resolve", help="resolve two specs to paths and a dry-run transfer")
    res.add_argument("--from", dest="from_spec", required=True)
    res.add_argument("--to", dest="to_spec", required=True)
    res.add_argument("--config", default=None)
    return parser


# ---------------------------------------------------------------------------
# self-test (the gate)
# ---------------------------------------------------------------------------

def run_cli(*args: str) -> tuple[int, str, str]:
    """Invoke this script's real CLI as a subprocess via its own interpreter."""
    proc = subprocess.run(
        [sys.executable, str(_THIS_SCRIPT), *args],
        capture_output=True, text=True, encoding="utf-8", errors="replace",
        timeout=60)
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def self_test() -> int:
    """The gate: temp fixture only, real CLI as a subprocess, no network anywhere."""
    checks = 0

    def check(cond, label, detail=""):
        nonlocal checks
        if not cond:
            raise AssertionError(f"{label}{(': ' + detail) if detail else ''}")
        checks += 1
        print(f"ok: {label}")

    tmp = Path(tempfile.mkdtemp(prefix="ssh-registry-selftest-"))
    try:
        cfg = tmp / "fixture.toon"

        # 2. two devices via the real CLI subprocess: one with a user and a
        #    non-default port, one with neither.
        rc, out, err = run_cli("device", "add", "test-pc.invalid",
                               "--host", "test-pc.invalid",
                               "--user", "example", "--port", "2222",
                               "--config", str(cfg))
        check(rc == 0, "device add test-pc.invalid", err)
        rc, out, err = run_cli("device", "add", "test-mac.invalid",
                               "--host", "test-mac.invalid",
                               "--config", str(cfg))
        check(rc == 0, "device add test-mac.invalid", err)
        rc, out, err = run_cli("device", "add", "test-pc.invalid",
                               "--host", "other.invalid", "--config", str(cfg))
        check(rc != 0 and "already exists" in err,
              "device add duplicate alias fails clearly", err)

        # 3. two locations under each device, including the same alias name on
        #    both devices pointing at different paths.
        for device, name, path in (
            ("test-pc.invalid", "incoming", "C:/data/incoming"),
            ("test-pc.invalid", "downloads", "C:/data/Downloads"),
            ("test-mac.invalid", "incoming", "/srv/incoming"),
            ("test-mac.invalid", "archive", "/srv/archive"),
        ):
            rc, out, err = run_cli("location", "add", device, name, path,
                                   "--config", str(cfg))
            check(rc == 0, f"location add {device}:{name}", err)
        rc, out, err = run_cli("location", "add", "ghost.invalid", "x", "/tmp",
                               "--config", str(cfg))
        check(rc != 0 and "ghost.invalid" in err,
              "location add unknown device fails clearly", err)
        rc, out, err = run_cli("location", "add", "test-pc.invalid", "incoming",
                               "C:/other", "--config", str(cfg))
        check(rc != 0 and "already exists" in err,
              "location add duplicate alias fails clearly", err)

        # 3. same alias on both devices resolves independently, no collision.
        rc, out, err = run_cli("resolve",
                               "--from", "test-pc.invalid:incoming",
                               "--to", "test-mac.invalid:incoming",
                               "--config", str(cfg))
        check(rc == 0, "resolve same alias on both devices", err)
        check("C:/data/incoming" in out, "pc incoming resolves to its own path")
        check("/srv/incoming" in out, "mac incoming resolves to its own path")

        # 4/5. resolve across the two devices with a relative path.
        rc, out, err = run_cli("resolve",
                               "--from", "test-mac.invalid:incoming/docs/report.pdf",
                               "--to", "test-pc.invalid:downloads/new",
                               "--config", str(cfg))
        check(rc == 0, "resolve across devices with relpath", err)
        check("/srv/incoming/docs/report.pdf" in out,
              "from resolved absolute path", out)
        check("C:/data/Downloads/new" in out, "to resolved absolute path", out)
        check("test-mac.invalid" in out, "from ssh target (host, no user)", out)
        check("example" + "@" + "test-pc.invalid" + " -p 2222" in out,
              "to ssh target (user + port)", out)
        check("NOT EXECUTED" in out, "dry-run marker on transfer command", out)

        # list commands print decoded values.
        rc, out, err = run_cli("device", "list", "--config", str(cfg))
        check(rc == 0, "device list runs", err)
        check("host: test-pc.invalid" in out and "user: example" in out
              and "port: 2222" in out, "device list shows decoded host/user/port", out)
        check("locations:" in out and "incoming:" in out, "device list shows locations", out)
        rc, out, err = run_cli("location", "list", "test-mac.invalid", "--config", str(cfg))
        check(rc == 0, "location list runs", err)
        check("archive: /srv/archive" in out,
              "location list shows decoded paths", out)
        rc, out, err = run_cli("location", "list", "nope.invalid", "--config", str(cfg))
        check(rc != 0 and "nope.invalid" in err,
              "location list unknown device fails clearly", err)

        # 7. path-traversal guard refuses a relpath containing '..'.
        rc, out, err = run_cli("resolve",
                               "--from", "test-pc.invalid:incoming/../../etc/passwd",
                               "--to", "test-mac.invalid:archive",
                               "--config", str(cfg))
        check(rc != 0, "path-traversal relpath refused")
        check(".." in err, "traversal error names the '..' segment", err)

        # 8. unknown device and unknown location each fail clearly, non-zero.
        rc, out, err = run_cli("resolve",
                               "--from", "ghost.invalid:incoming",
                               "--to", "test-mac.invalid:archive",
                               "--config", str(cfg))
        check(rc != 0 and "ghost.invalid" in err,
              "resolve unknown device fails clearly", err)
        rc, out, err = run_cli("resolve",
                               "--from", "test-pc.invalid:nosuch",
                               "--to", "test-mac.invalid:archive",
                               "--config", str(cfg))
        check(rc != 0 and "nosuch" in err,
              "resolve unknown location fails clearly", err)

        # 9. round-trip fidelity: parse back from disk in a fresh call.
        data = load_config(cfg)
        devices = data["devices"]
        check(set(devices) == {"test-pc.invalid", "test-mac.invalid"},
              "round-trip: device set survives")
        pc = devices["test-pc.invalid"]
        check(pc["host"] == "test-pc.invalid", "round-trip: pc host")
        check(pc["user"] == "example", "round-trip: pc user")
        check(pc["port"] == 2222, "round-trip: pc port")
        check(pc["locations"]["incoming"] == "C:/data/incoming",
              "round-trip: pc incoming path")
        check(pc["locations"]["downloads"] == "C:/data/Downloads",
              "round-trip: pc downloads path")
        mac = devices["test-mac.invalid"]
        check(mac["host"] == "test-mac.invalid", "round-trip: mac host")
        check("user" not in mac, "round-trip: mac has no user")
        check("port" not in mac, "round-trip: mac has no port")
        check(mac["locations"]["incoming"] == "/srv/incoming",
              "round-trip: mac incoming path")
        check(mac["locations"]["archive"] == "/srv/archive",
              "round-trip: mac archive path")
        text = cfg.read_text(encoding="utf-8")
        check("devices:" in text and "\n  test-pc.invalid:" in text,
              "writer emits 2-space TOON shape")
        check("    port: 2222" in text, "writer emits a non-default port")
        check(all(ln.strip() != "port: 22" for ln in text.splitlines()),
              "writer omits the default port")
        check("      incoming: C:/data/incoming" in text,
              "writer emits a location line under its device")

        # 6. no network path anywhere: no socket import, and no subprocess call
        #    that names ssh, scp or rsync outside the dry-run string.
        src = _THIS_SCRIPT.read_text(encoding="utf-8")
        sock_import = "import " + "socket"
        sock_from = "from " + "socket"
        os_system = "os." + "system"
        os_popen = "os." + "popen"
        os_spawn = "os." + "spawn"
        check(sock_import not in src, "source has no socket import at module level")
        check(sock_from not in src, "source has no socket import")
        check(os_system not in src and os_popen not in src
              and os_spawn not in src, "source has no os shell/exec calls")
        bad = []
        for line in src.splitlines():
            if re.search(r"\bsubprocess\.(run|Popen|call|check_output|check_call)\s*\(", line):
                for tool in ("ssh", "scp", "rsync"):
                    if f'"{tool}"' in line or f"'{tool}'" in line:
                        bad.append(line)
        check(not bad, "no subprocess call names ssh/scp/rsync", "; ".join(bad))

        # 1/10. fixture built under a temp path only; temp dir is removed below.
        print(f"self-test: {checks} assertions passed, temp fixture removed")
        return 0
    except AssertionError as e:
        print(f"FAIL: {e}", file=sys.stderr)
        return 1
    finally:
        shutil.rmtree(tmp, ignore_errors=True)


# ---------------------------------------------------------------------------
# main
# ---------------------------------------------------------------------------

def main() -> int:
    if "--self-test" in sys.argv:
        return self_test()
    parser = build_parser()
    args = parser.parse_args()
    config_path = Path(args.config) if args.config else DEFAULT_CONFIG
    if args.command == "device":
        if args.device_command == "add":
            cmd_device_add(args, config_path)
        else:
            cmd_device_list(config_path)
    elif args.command == "location":
        if args.location_command == "add":
            cmd_location_add(args, config_path)
        else:
            cmd_location_list(args, config_path)
    elif args.command == "resolve":
        cmd_resolve(args, config_path)
    else:
        parser.print_help()
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
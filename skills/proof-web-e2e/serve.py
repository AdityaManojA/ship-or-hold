#!/usr/bin/env python3
"""Start local dev servers, wait for their ports, run a command, then stop them all.

Standard library only, no network beyond localhost TCP checks. Cleans up the whole
process tree on every exit path, including Ctrl-C, on Windows and POSIX.

    # start a server, wait for the port, run tests, then tear down
    python3 serve.py --run "npm run dev" --port 3000 -- python3 flows.py status

    # two servers (backend and frontend)
    python3 serve.py --run "python app.py" --port 5001 --run "npm run dev" --port 3000 -- <cmd...>

    # just wait for something already starting
    python3 serve.py --wait --port 3000 --startup-timeout 60

Exit code: the inner command's, or 3 on a startup timeout, or 0 for --wait success.
"""

import argparse
import os
import signal
import socket
import subprocess
import sys
import time

HERE = os.path.dirname(os.path.abspath(__file__))
sys.path.insert(0, os.path.join(HERE, "..", "proof-core"))
import ledger  # noqa: E402

IS_WIN = os.name == "nt"


def _port_open(port, host="127.0.0.1"):
    try:
        with socket.create_connection((host, port), timeout=0.5):
            return True
    except OSError:
        return False


def _wait_for_port(port, timeout):
    deadline = time.time() + timeout
    while time.time() < deadline:
        if _port_open(port):
            return True
        time.sleep(0.5)
    return False


def _spawn(cmd, cwd, log_handle):
    kwargs = {"cwd": cwd or None, "stdout": log_handle, "stderr": subprocess.STDOUT, "shell": True}
    if IS_WIN:
        kwargs["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP
    else:
        kwargs["preexec_fn"] = os.setsid
    return subprocess.Popen(cmd, **kwargs)


def _kill_tree(proc):
    if proc.poll() is not None:
        return
    try:
        if IS_WIN:
            subprocess.run(["taskkill", "/T", "/F", "/PID", str(proc.pid)],
                           stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        else:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
    except (OSError, subprocess.SubprocessError):
        pass


def main(argv=None):
    ledger.console()
    ap = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    ap.add_argument("--run", action="append", default=[], help="a server command (repeatable)")
    ap.add_argument("--port", action="append", type=int, default=[], help="the port for each --run (repeatable, in order)")
    ap.add_argument("--cwd", help="working directory for the server commands")
    ap.add_argument("--startup-timeout", type=int, default=90)
    ap.add_argument("--wait", action="store_true", help="only wait for --port, start nothing")
    ap.add_argument("--log", help="server output log (default under proof/evidence/proof-web-e2e/)")
    ap.add_argument("inner", nargs=argparse.REMAINDER, help="-- then the command to run once servers are up")
    a = ap.parse_args(argv)

    if a.wait:
        if not a.port:
            print("error: --wait needs at least one --port", file=sys.stderr)
            return 2
        for port in a.port:
            if not _wait_for_port(port, a.startup_timeout):
                print("%s port %d did not open within %ds" % (ledger.BAD, port, a.startup_timeout), file=sys.stderr)
                return 3
        print("%s port(s) open: %s" % (ledger.OK, ", ".join(map(str, a.port))))
        return 0

    if len(a.run) != len(a.port):
        print("error: give one --port per --run (got %d runs, %d ports)" % (len(a.run), len(a.port)), file=sys.stderr)
        return 2
    inner = a.inner[1:] if a.inner and a.inner[0] == "--" else a.inner
    if not inner:
        print("error: nothing to run. Put the command after --", file=sys.stderr)
        return 2

    log_path = a.log or os.path.join(ledger.root("proof"), "evidence", "proof-web-e2e", "server.log")
    os.makedirs(os.path.dirname(log_path), exist_ok=True)
    servers = []
    rc = 0
    with open(log_path, "w", encoding="utf-8") as log_handle:
        try:
            for cmd, port in zip(a.run, a.port):
                print("starting on :%d  %s" % (port, cmd))
                servers.append(_spawn(cmd, a.cwd, log_handle))
            for port in a.port:
                if not _wait_for_port(port, a.startup_timeout):
                    print("%s port %d did not open within %ds; see %s" % (ledger.BAD, port, a.startup_timeout, log_path), file=sys.stderr)
                    _print_log_tail(log_path)
                    return 3
            print("%s all ports open; running inner command" % ledger.OK)
            proc = subprocess.run(inner)
            rc = proc.returncode
        except KeyboardInterrupt:
            print("\ninterrupted; tearing down", file=sys.stderr)
            rc = 130
        finally:
            for s in reversed(servers):
                _kill_tree(s)
    return rc


def _print_log_tail(path, lines=20):
    try:
        with open(path, encoding="utf-8", errors="replace") as fh:
            tail = fh.readlines()[-lines:]
        sys.stderr.write("".join(tail))
    except OSError:
        pass


if __name__ == "__main__":
    sys.exit(main())

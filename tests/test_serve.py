import contextlib
import io
import os
import socket
import sys
import time
import unittest

from _load import load_tool

serve = load_tool("proof-web-e2e", "serve.py")


def free_port():
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()
    return port


def run(argv):
    out, err = io.StringIO(), io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(err):
        rc = serve.main(argv)
    return rc, out.getvalue() + err.getvalue()


class Serve(unittest.TestCase):
    def test_starts_waits_propagates_and_cleans_up(self):
        port = free_port()
        log = os.path.join(os.path.dirname(os.path.abspath(__file__)), "_serve_test.log")
        argv = ["--run", "%s -m http.server %d --bind 127.0.0.1" % (sys.executable, port),
                "--port", str(port), "--startup-timeout", "15", "--log", log,
                "--", sys.executable, "-c", "import sys; sys.exit(7)"]
        try:
            rc, out = run(argv)
            self.assertEqual(rc, 7, out)
            # server should be gone: port closed within a few seconds
            closed = False
            for _ in range(10):
                try:
                    socket.create_connection(("127.0.0.1", port), timeout=0.5).close()
                    time.sleep(0.5)
                except OSError:
                    closed = True
                    break
            self.assertTrue(closed, "server port still open after teardown")
        finally:
            if os.path.exists(log):
                os.remove(log)

    def test_wait_timeout_exits_3(self):
        port = free_port()  # nothing listening
        start = time.time()
        rc, out = run(["--wait", "--port", str(port), "--startup-timeout", "2"])
        self.assertEqual(rc, 3)
        self.assertLess(time.time() - start, 8)

    def test_mismatched_run_and_port(self):
        rc, out = run(["--run", "echo hi", "--", sys.executable, "-c", "pass"])
        self.assertEqual(rc, 2)


if __name__ == "__main__":
    unittest.main()

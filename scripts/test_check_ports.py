"""Regression test for scripts/check_ports.sh conflict path.

Binds all five host ports to local listeners, runs the script, and expects
exit code 1 with each busy port and its .env variable named in the output.
"""
import os
import socket
import subprocess
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCRIPT = os.path.join(ROOT, "scripts", "check_ports.sh")

PORT_VARS = [
    "WEB_HOST_PORT",
    "API_HOST_PORT",
    "POSTGRES_HOST_PORT",
    "REDIS_HOST_PORT",
    "OPENCODE_HOST_PORT",
]


class CheckPortsConflictTest(unittest.TestCase):
    def test_busy_ports_exit_1_and_name_env_var(self):
        listeners = []
        ports = {}
        env = dict(os.environ)
        try:
            for var in PORT_VARS:
                s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                s.bind(("127.0.0.1", 0))
                s.listen(1)
                listeners.append(s)
                ports[var] = s.getsockname()[1]
                env[var] = str(ports[var])
            proc = subprocess.run(
                ["bash", SCRIPT],
                cwd=ROOT,
                env=env,
                capture_output=True,
                text=True,
                timeout=60,
            )
        finally:
            for s in listeners:
                s.close()

        out = proc.stdout + proc.stderr
        self.assertEqual(
            proc.returncode,
            1,
            f"expected exit code 1 when ports are busy, got {proc.returncode}: {out}",
        )
        for var, port in ports.items():
            self.assertIn(var, out)
            self.assertIn(f"端口 {port} 已被占用", out)


if __name__ == "__main__":
    unittest.main()

import contextlib
import io
import os
import unittest

from _load import TempProof, load_tool

infra = load_tool("proof-infra", "infra.py")
ledger = load_tool("proof-core", "ledger.py")

FILES = {
    "Dockerfile": "FROM node:latest\nRUN curl -fsSL https://get.example.sh | sh\nENV DB_PASSWORD=hunter2hunter2\nCMD [\"node\", \"app.js\"]\n",
    "good/Dockerfile": "FROM python:3.12-slim\nRUN useradd -m app\nUSER app\nCMD [\"python\", \"app.py\"]\n",
    "docker-compose.yml": ("services:\n  db:\n    image: postgres:16\n    ports:\n      - \"5432:5432\"\n"
                           "    environment:\n      POSTGRES_PASSWORD: supersecret1\n  cache:\n    image: redis:7\n"
                           "    ports:\n      - \"127.0.0.1:6379:6379\"\n    privileged: true\n"),
    "k8s/deploy.yaml": ("apiVersion: apps/v1\nkind: Deployment\nmetadata:\n  name: web\nspec:\n  template:\n"
                        "    spec:\n      hostNetwork: true\n      containers:\n        - name: web\n"
                        "          image: shop/web:latest\n          securityContext:\n            privileged: true\n"
                        "---\napiVersion: v1\nkind: Service\nmetadata:\n  name: web\n"),
    "main.tf": ("resource \"aws_security_group\" \"ssh\" {\n  ingress {\n    from_port = 22\n    to_port = 22\n"
                "    cidr_blocks = [\"0.0.0.0/0\"]\n  }\n  egress {\n    cidr_blocks = [\"0.0.0.0/0\"]\n  }\n}\n"
                "resource \"aws_s3_bucket_acl\" \"b\" {\n  acl = \"public-read\"\n}\n"
                "resource \"aws_db_instance\" \"d\" {\n  publicly_accessible = true\n  storage_encrypted = false\n}\n"),
    ".github/workflows/pr.yml": ("on:\n  pull_request_target:\njobs:\n  build:\n    permissions: write-all\n    steps:\n"
                                 "      - uses: actions/checkout@v4\n        with:\n          ref: ${{ github.event.pull_request.head.sha }}\n"
                                 "      - uses: someone/deploy-action@main\n      - run: echo ${{ secrets.TOKEN }}\n"),
    ".env": "API_KEY=abc\n",
    ".env.example": "API_KEY=\n",
}


def run(*argv):
    out = io.StringIO()
    with contextlib.redirect_stdout(out), contextlib.redirect_stderr(out):
        rc = infra.main(list(argv))
    return rc, out.getvalue()


class Infra(unittest.TestCase):
    def setUp(self):
        self.tp = TempProof().__enter__()
        self.d = self.tp.dir
        self.proj = os.path.join(self.tp.base, "proj")
        for rel, text in FILES.items():
            full = os.path.join(self.proj, rel)
            os.makedirs(os.path.dirname(full), exist_ok=True)
            with open(full, "w", encoding="utf-8", newline="") as fh:
                fh.write(text)
        self.hits, self.kinds = infra.scan(self.proj)
        self.rules = {}
        for h in self.hits:
            self.rules.setdefault(h["rule"], []).append(h)

    def tearDown(self):
        self.tp.__exit__(None, None, None)

    def test_kinds(self):
        self.assertEqual(self.kinds, {"dockerfile", "compose", "kubernetes", "terraform", "ci", "env"})

    def test_dockerfile(self):
        for r in ("docker-latest", "docker-pipe-shell", "docker-secret", "docker-root"):
            self.assertIn(r, self.rules)
        self.assertTrue(all(h["file"] == "Dockerfile" for h in self.rules["docker-root"]))  # good/ has USER app
        self.assertNotIn("hunter2", " ".join(h["text"] for h in self.rules["docker-secret"]))

    def test_compose(self):
        exposed = [h["text"] for h in self.rules["compose-db-exposed"]]
        self.assertEqual(len(exposed), 1)
        self.assertIn("5432", exposed[0])  # the 127.0.0.1-bound redis is fine
        self.assertIn("compose-privileged", self.rules)
        self.assertIn("compose-secret", self.rules)

    def test_kubernetes(self):
        for r in ("k8s-privileged", "k8s-host", "k8s-latest", "k8s-nonroot", "k8s-limits"):
            self.assertIn(r, self.rules)
        self.assertEqual(len(self.rules["k8s-nonroot"]), 1)  # the Service is not a workload

    def test_terraform(self):
        self.assertEqual(len(self.rules["tf-open-ingress"]), 1)  # egress 0.0.0.0/0 is not flagged
        for r in ("tf-public-bucket", "tf-public-db", "tf-unencrypted"):
            self.assertIn(r, self.rules)

    def test_ci(self):
        for r in ("ci-untrusted-checkout", "ci-secret-echo", "ci-write-all", "ci-unpinned-action"):
            self.assertIn(r, self.rules)

    def test_env(self):
        files = [h["file"] for h in self.rules["env-committed"]]
        self.assertEqual(files, [".env"])

    def test_record(self):
        rc, out = run("--dir", self.d, "record", self.proj)
        self.assertEqual(rc, 0, out)
        fs = ledger.load_findings(self.d, "proof-infra")
        self.assertEqual(len(fs), len(self.hits))
        self.assertTrue(all(f["status"] == "verified" for f in fs))
        cov = ledger.load_coverage(self.d, "proof-infra")
        self.assertTrue(all(c["status"] == "covered" for c in cov["checks"]))

    def test_record_empty_project(self):
        empty = os.path.join(self.tp.base, "empty")
        os.makedirs(empty)
        run("--dir", self.d, "record", empty)
        cov = {c["id"]: c["status"] for c in ledger.load_coverage(self.d, "proof-infra")["checks"]}
        self.assertEqual(cov["terraform"], "not_applicable")


if __name__ == "__main__":
    unittest.main()

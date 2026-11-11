"""Independent v1.0.3 checks adapted for the v1.0.4 publish primitive.

Original A01-A10 and B01 are byte-for-byte unchanged. Only B02 moved its
injection point from os.replace (no longer called) to the real os.link, with
additional assertions. See no_clobber_during_publish().

Only files under this script's fresh fixtures directory are mutated. The
reviewed sources and all previous audit evidence remain untouched.
"""
import contextlib
import hashlib
import importlib.util
import io
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import tempfile
from types import SimpleNamespace

sys.dont_write_bytecode = True
os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
BASE = Path(__file__).resolve().parent
# v1.1.0 fix: default to the flat layout (progress.py sits next to this
# script); the old staging layout used BASE/source/project-protocol/.
SOURCE = Path(os.environ.get("PP_SCRIPT", str(BASE / "progress.py")))
# Fixtures live under the OS temp dir by default so this script can be
# re-run at will without straying into the skill's own tree (v1.1.0 fix:
# previous version dried into BASE and forced cleanup between runs).
_PP_FIXTURES = os.environ.get("PP_FIXTURES")
if _PP_FIXTURES:
    FIXTURES = Path(_PP_FIXTURES)
    FIXTURES.mkdir(parents=True, exist_ok=True)
else:
    FIXTURES = Path(tempfile.mkdtemp(prefix="pp-acceptance-"))
spec = importlib.util.spec_from_file_location("reviewed_v103", SOURCE)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)
RESULTS = []


def cli(*args):
    p = subprocess.run([sys.executable, "-X", "utf8", str(SOURCE), *map(str, args)],
                       capture_output=True, text=True, encoding="utf-8", timeout=15)
    return {"rc": p.returncode, "stdout": p.stdout, "stderr": p.stderr}


def root(name, initialized=True):
    p = FIXTURES / name
    p.mkdir()
    if initialized:
        r = cli("init", p)
        assert r["rc"] == 0, r
    return p


def commands(p, text="probe"):
    return {
        "add": cli("add", "--root", p, "--status", "DONE", "--text", text),
        "tail": cli("tail", "--root", p),
        "check": cli("check", text, "--strict", "--root", p),
    }


def capture_init(p):
    so, se, rc = io.StringIO(), io.StringIO(), 0
    with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
        try:
            mod.cmd_init(SimpleNamespace(directory=str(p)))
        except SystemExit as exc:
            rc = int(exc.code or 0)
    return {"rc": rc, "stdout": so.getvalue(), "stderr": se.getvalue()}


def case(name, fn):
    try:
        passed, details = fn()
        r = {"name": name, "passed": bool(passed), "details": details}
    except Exception as exc:
        r = {"name": name, "passed": False, "harness_error": repr(exc)}
    RESULTS.append(r)
    print(json.dumps({k: v for k, v in r.items() if k != "details"}, ensure_ascii=False), flush=True)


def unicode_fields():
    p = root("unicode-fields")
    details = []
    separators = ["\r", "\n", "\r\n", "\v", "\f", "\x1c", "\x1d", "\x1e", "\x85", "\u2028", "\u2029"]
    for i, separator in enumerate(separators):
        token = "unicode-token-%d-end" % i
        added = cli("add", "--root", p, "--status", "REJECTED", "--tid", "T01",
                    "--text", "begin" + separator + token, "--path", "a" + separator + "b.txt",
                    "--next", "next" + separator + "action")
        found = cli("check", token, "--strict", "--root", p)
        details.append({"separator_repr": repr(separator), "add_rc": added["rc"], "strict_rc": found["rc"]})
    rows = [line for line in (p / "06_记忆" / "L0_LOG.md").read_text(encoding="utf-8").splitlines()
            if line.startswith("- [")]
    valid = len(rows) == len(separators) and all(len(line.split(" | ")) == 5 for line in rows)
    return valid and all(r["add_rc"] == 0 and r["strict_rc"] == 1 for r in details), {
        "rows": len(rows), "valid_five_fields": valid, "cases": details}


def idea_variants():
    results = []
    for i, status in enumerate(["REJECTED", '"REJECTED"', "'REJECTED'", "REJECTED # note"]):
        p = root("idea-spelling-" + str(i))
        d = p / "01_任务" / "待定"
        d.mkdir(parents=True)
        (d / "20261106-idea.md").write_text("---\nstatus: " + status + "\nreason: disproven\n---\n# spelling-token\n", encoding="utf-8")
        results.append({"input": status, "check": cli("check", "spelling-token", "--strict", "--root", p)})
    return all(r["check"]["rc"] == 1 for r in results), results


def invalid_ideas():
    inputs = [
        "---\nstatus: REJECTD\n---\n# token\n",
        "---\nstatus: REJECTED\nstatus: PROPOSED\n---\n# token\n",
        "---\nstatus: REJECTED\n# token\n",
        "---\n" + "# padding\n" * 70 + "status: REJECTED\n---\n# token\n",
    ]
    results = []
    for i, body in enumerate(inputs):
        p = root("invalid-idea-" + str(i))
        d = p / "01_任务" / "待定"
        d.mkdir(parents=True)
        (d / "20261106-idea.md").write_text(body, encoding="utf-8")
        r = cli("check", "token", "--strict", "--root", p)
        results.append(r)
    return all(r["rc"] == 2 and "STRICT: CLEAN" not in r["stdout"] for r in results), results


def bom_compatibility():
    results = []
    for mode in ["log", "idea"]:
        p = root("bom-" + mode)
        if mode == "log":
            f = p / "06_记忆" / "L0_LOG.md"
            body = "- [2026-11-06 10:00] REJECTED | T01 | bom-token | - | NEXT -\n"
        else:
            d = p / "01_任务" / "待定"
            d.mkdir(parents=True)
            f = d / "20261106-idea.md"
            body = "---\nstatus: REJECTED\nreason: disproven\n---\n# bom-token\n"
        f.write_text(body, encoding="utf-8-sig")
        before = f.read_bytes()
        found = cli("check", "bom-token", "--strict", "--root", p)
        results.append({"mode": mode, "rc": found["rc"], "unchanged": f.read_bytes() == before})
    return all(r["rc"] == 1 and r["unchanged"] for r in results), results


class InjectedFile:
    def __init__(self, inner, stage):
        self.inner, self.stage = inner, stage
    def __enter__(self):
        self.inner.__enter__()
        return self
    def __exit__(self, *args):
        result = self.inner.__exit__(*args)
        if self.stage == "close":
            raise OSError("audit close fault after real close")
        return result
    def write(self, text):
        if self.stage == "partial":
            self.inner.write(text[:32])
            self.inner.flush()
            raise OSError("audit fault after partial real write")
        return self.inner.write(text)
    def flush(self):
        self.inner.flush()
        if self.stage == "flush":
            raise OSError("audit fault after real flush")
    def fileno(self):
        return self.inner.fileno()


def init_failure(name, rel, stage):
    p = root(name, initialized=False)
    target = p / rel
    original_open = Path.open
    def patched(self, mode="r", *args, **kwargs):
        inner = original_open(self, mode, *args, **kwargs)
        return InjectedFile(inner, stage) if self == target and mode == "x" else inner
    Path.open = patched
    try:
        r = capture_init(p)
    finally:
        Path.open = original_open
    guarded = commands(p)
    exists = target.exists()
    in_manifest = "\n  " + rel + "\n" in r["stderr"]
    marker_absent = not (p / "AGENTS.md").exists()
    return r["rc"] == 2 and exists and in_manifest and marker_absent and all(c["rc"] == 2 for c in guarded.values()), {
        "stage": stage, "target": rel, "init": r, "target_exists": exists,
        "target_in_manifest": in_manifest, "agents_absent": marker_absent,
        "guard_rcs": {k: v["rc"] for k, v in guarded.items()},
        "target_content": target.read_text(encoding="utf-8") if exists else None}


def timeout_validation():
    p = root("timeouts")
    lock = p / "06_记忆" / "L0_LOG.md.lock"
    results = []
    for value in ["nan", "inf", "-inf", "0", "-1"]:
        r = cli("add", "--root", p, "--status", "DONE", "--text", "x", "--lock-timeout=" + value)
        results.append({"value": value, "rc": r["rc"], "lock_absent": not lock.exists()})
    return all(r["rc"] == 2 and r["lock_absent"] for r in results), results


def normal_handoff():
    p = root("normal-handoff")
    (p / "package.json").write_text("{}\n", encoding="utf-8")
    outcome = commands(p, "normal-token")
    init_again = cli("init", p)
    return all(r["rc"] == 0 for r in outcome.values()) and init_again["rc"] == 2, {
        "handoff": outcome, "reinit": init_again}


def reject_truncated_current():
    p = root("truncated-current")
    # This is a REAL prefix of the current generated file, not a forged marker.
    agents = p / "AGENTS.md"
    full = agents.read_text(encoding="utf-8")
    cut = full.index(mod.LEGACY_AGENTS_TAIL) + len(mod.LEGACY_AGENTS_TAIL)
    prefix = full[:cut]
    agents.write_text(prefix, encoding="utf-8")
    outcome = commands(p, "truncated-token")
    return all(r["rc"] == 2 for r in outcome.values()), {
        "full_characters": len(full), "retained_prefix_characters": len(prefix),
        "truncated_characters": len(full) - len(prefix), "completion_marker_present": mod.AGENTS_COMPLETION_MARKER in prefix,
        "v103_header_present": "project-protocol v1.0.3" in prefix,
        "legacy_heading_present": mod.LEGACY_AGENTS_MARKER in prefix,
        "legacy_tail_prefix_present": mod.LEGACY_AGENTS_TAIL in prefix,
        "actual": outcome}


def no_clobber_during_publish():
    """B02 adapted to the v1.0.4 publish primitive.

    The original case patched os.replace, which v1.0.4 no longer calls, so its
    injection point disappeared (same class of issue as the earlier I10). The
    adapted case patches the REAL publish primitive os.link, still creates a
    real competing file immediately before the real publish action, and ADDS
    stronger assertions: byte-for-byte preservation, accurate temp manifest,
    competitor not listed as our product, and add/tail/check all rejected.
    The original success conditions are kept; nothing was deleted or weakened.
    """
    p = root("publish-no-clobber", initialized=False)
    destination = p / "AGENTS.md"
    user_content = "# Existing user AGENTS\n\nKeep this rule; created by another writer during init.\n"
    original_link = mod.os.link
    scheduling = {}
    def publish_with_concurrent_file(src, dst):
        assert Path(dst) == destination
        # Deterministic scheduling only: create a real competing file, then let
        # the reviewed implementation call the REAL os.link. No mocked return.
        with destination.open("xb") as f:
            f.write(user_content.encode("utf-8"))
        scheduling["before_publish_sha256"] = hashlib.sha256(destination.read_bytes()).hexdigest()
        scheduling["real_link_called"] = True
        return original_link(src, dst)
    mod.os.link = publish_with_concurrent_file
    try:
        r = capture_init(p)
    finally:
        mod.os.link = original_link
    actual = destination.read_bytes()
    expected = user_content.encode("utf-8")
    guards = {k: v["rc"] for k, v in commands(p).items()}
    ok = (r["rc"] == 2 and actual == expected
          and "不属于本次 init 产物" in r["stderr"]
          and "\n  AGENTS.md.init.tmp\n" in r["stderr"]
          and "\n  AGENTS.md\n" not in r["stderr"]
          and all(v == 2 for v in guards.values()))
    return ok, {
        "init": r, "existing_user_content": user_content,
        "user_file_preserved": actual == expected,
        "before_sha256": scheduling["before_publish_sha256"],
        "after_sha256": hashlib.sha256(actual).hexdigest(),
        "real_link_called": scheduling.get("real_link_called", False),
        "after_prefix": actual[:240].decode("utf-8", "replace"),
        "reviewed_generated_content_replaced_user_file": actual == mod.COMPLETION_CONTENT.encode("utf-8"),
        "guard_rcs": guards,
        "method": "controlled scheduling immediately before the real os.link publish; real competing file, real publish action, no mocked return value; original assumptions kept plus manifest and guard checks"}


CASES = [
    ("A01 unicode record fields and negative detection", unicode_fields),
    ("A02 four status scalar spellings", idea_variants),
    ("A03 malformed status never returns CLEAN", invalid_ideas),
    ("A04 BOM logs and ideas detected without rewriting", bom_compatibility),
    ("A05 partial ordinary write manifest", lambda: init_failure("partial-reader", "00_定义/READER.md", "partial")),
    ("A06 partial temporary completion write", lambda: init_failure("partial-temp", "AGENTS.md.init.tmp", "partial")),
    ("A07 temporary completion flush fault", lambda: init_failure("flush-temp", "AGENTS.md.init.tmp", "flush")),
    ("A08 temporary completion close fault", lambda: init_failure("close-temp", "AGENTS.md.init.tmp", "close")),
    ("A09 non-finite and non-positive timeout rejected before lock open", timeout_validation),
    ("A10 initialized nonempty project handoff and re-init rejection", normal_handoff),
    ("B01 truncated CURRENT AGENTS cannot use LEGACY bypass", reject_truncated_current),
    ("B02 newly existing user AGENTS survives completion publish", no_clobber_during_publish),
]
for name, fn in CASES:
    case(name, fn)

summary = {"cases": len(RESULTS), "passed": sum(r["passed"] for r in RESULTS),
           "failed": sum(not r["passed"] for r in RESULTS),
           "harness_errors": sum("harness_error" in r for r in RESULTS), "results": RESULTS}
RESULT_PATH = Path(os.environ.get("PP_RESULTS",
                                   str(Path(tempfile.gettempdir())
                                       / "pp-independent-results-v104.json")))
RESULT_PATH.write_text(json.dumps(summary, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
print(json.dumps({k: v for k, v in summary.items() if k != "results"}, ensure_ascii=False), flush=True)
# Self-cleanup: this script created FIXTURES via mkdtemp; remove it after the
# run so repeated runs stay clean. An explicitly configured PP_FIXTURES dir is
# left untouched (user-owned).
if not _PP_FIXTURES:
    shutil.rmtree(FIXTURES, ignore_errors=True)
sys.exit(0 if summary["failed"] == 0 else 1)

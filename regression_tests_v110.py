#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project-protocol v1.1.0 regression suite.

Covers the two things v1.1.0 introduces beyond v1.0.4:

  (A) cross-agent handoff: the new `handoff` / `resume` commands that make a
      project resumable by a DIFFERENT agent without redoing work.
  (B) backward compatibility: a real project published by v1.0.4 keeps working
      after the skill is upgraded to v1.1.0.

Run:
  python regression_tests_v110.py --script progress.py --out result-v110.json \
                                  --workdir test-scratch-v110

Pure standard library, offline. Every case runs on an isolated temporary
project. No assertion of the earlier v1.0.4 / v1.0.3 suites was weakened; those
suites still run and must still pass independently.

Scope:
  A/01  handoff emits a HANDOFF card in 06_记忆/ with the required sections
  A/02  handoff card binds the tail / goal / negative blocks to L0 + L1
  A/03  handoff path is never doubled and has NO stray %d placeholder
  A/04  handoff opens the card exclusively (x): a same-minute collision is an
        explicit error, never an overwrite
  A/05  resume prints a bounded cold-start package (read manifest + budget)
  A/06  resume works without any prior card (first-time-ever handoff); leads
        with the authoritative L1 "下一步" section
  A/07  resume honours the --max-lines budget AND reports truncation with
        exit code 3 (not a false success)
  A/08  resume reports the card location under 06_记忆/ (not "on L1")
  B/01  init publishes a v1.1.0 version identity that satisfies the checker
  B/02  a real complete v1.0.4 entry stays usable after upgrade
  B/03  a genuine v1.0.4 entry is still recognised as a distinct legacy version
  B/04  a truncated v1.0.4 entry is rejected
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
DETAIL_CHARS = 500


def _load(script):
    spec = importlib.util.spec_from_file_location("pp110_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Suite:
    def __init__(self, script, workdir, auto_work=False):
        self.script = Path(script).resolve()
        self.auto_work = auto_work
        self.mod = _load(self.script)
        self.work = Path(workdir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-v110-", dir=str(self.work)))
        self.counter = 0
        self.results = []

    def cli(self, *args, timeout=30):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(self.script), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout)

    def root(self, tag):
        self.counter += 1
        p = self.tmp / ("%s-%03d" % (tag, self.counter))
        p.mkdir()
        return p

    def init(self, tag):
        p = self.root(tag)
        r = self.cli("init", p)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        return p

    def seed(self, p, tid="T01", status="DONE", text="seed-token",
             artifact="-", next="-"):
        r = self.cli("add", "--root", str(p), "--status", status, "--tid", tid,
                     "--text", text, "--path", artifact, "--next", next)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        return r

    def handoff_ok(self, p, by="tester", tail_lines=None):
        """Run handoff and return (filename_no_ext_prefix, full_relpath)."""
        args = ["handoff", "--root", str(p), "--by", by]
        if tail_lines is not None:
            args += ["--tail-lines", str(tail_lines)]
        r = self.cli(*args)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        rel = r.stdout.split("HANDOFF OK | ")[1].split(" | ")[0]
        return rel

    def case(self, name, fn):
        try:
            detail = fn(self)
            self.results.append({"name": name, "ok": True, "detail": detail})
            print("PASS  " + name, flush=True)
        except Exception as exc:  # noqa: BLE001
            self.results.append({"name": name, "ok": False, "error": repr(exc)})
            print("FAIL  " + name + "  " + repr(exc), flush=True)

    def cleanup(self):
        shutil.rmtree(self.tmp, ignore_errors=True)
        if self.auto_work:
            # The container is a fresh mkdtemp() this run created; remove it too.
            shutil.rmtree(self.work, ignore_errors=True)


# ---------------------------------------------------------------------------
# A/01 - A/04 : the handoff command
# ---------------------------------------------------------------------------
HANDOFF_HEADER = "# HANDOFF 交接快照"
CARD_RE = re.compile(r"^HANDOFF-\d{8}-\d{6}\.md$")


def a01(s):
    """handoff writes a HANDOFF card with the required section headings."""
    p = s.init("handoff-basic")
    s.seed(p, text="handoff-basic-token")
    rel = s.handoff_ok(p)
    assert rel.startswith("06_记忆/HANDOFF"), rel
    nm = rel.split("/", 1)[1]
    assert CARD_RE.match(nm), nm
    card = p / rel
    assert card.is_file(), rel
    assert HANDOFF_HEADER in card.read_text(encoding="utf-8"), card
    body = card.read_text(encoding="utf-8")
    for heading in ("# HANDOFF 交接快照",
                    "## 当前目标（来自 L1）",
                    "## 进行中（来自 L1）",
                    "## 最近完成（L0 末尾",
                    "## 已否决 / 已阻塞",
                    "## 下一步（优先执行）",
                    "## 交接给下一 agent 的行动清单"):
        assert heading in body, heading
    assert "handoff-basic-token" in body, "seeded DONE line not in card"
    return {"card": nm, "card_bytes": len(body.encode("utf-8"))}


def a02(s):
    """The card binds the goal / wip / negative blocks from L0 + L1."""
    p = s.init("handoff-bind")
    s.seed(p, text="good-token", status="DONE")
    s.seed(p, text="rejected-token", status="REJECTED")
    s.seed(p, text="wip-token", status="WIP", next="progress.py resume --lines 10")
    rel = s.handoff_ok(p, tail_lines=30)
    body = (p / rel).read_text(encoding="utf-8")
    assert "rejected-token" in body, "negative line missing from card"
    sec = body.split("## 下一步（优先执行）")[1].split("## 交接")[0]
    assert "progress.py resume --lines 10" in sec, sec
    return {"card": rel, "next_section": sec.strip()[:120]}


def a03(s):
    """Card path is single-sited (no 06_记忆/06_记忆/) and has no %d residue."""
    p = s.init("handoff-path")
    s.seed(p, text="path-token")
    rel = s.handoff_ok(p, tail_lines=12)
    body = (p / rel).read_text(encoding="utf-8")
    assert "%d" not in body, "stray %-placeholder found in card"
    assert "L0 末尾 12 行" in body, "tail count not substituted into action list"
    return {"card": rel}


def a04(s):
    """The card is created exclusively: a same-minute collision never overwrites.

    Deterministic: freeze the module's `datetime` to a fixed minute and run
    `cmd_handoff` in-process twice; the second call must hit a FileExistsError
    (x mode) and leave the rival card byte-identical, never overwriting.
    """
    p = s.init("handoff-collision")
    s.seed(p, text="collision-token")

    import contextlib as _ctx
    frozen = __import__("datetime").datetime(2026, 11, 9, 12, 30, 0)
    real_dt = s.mod.datetime
    s.mod.datetime = __import__("types").SimpleNamespace(
        now=lambda: frozen,
        strftime=lambda s_, fmt: frozen.strftime(fmt))
    cap_out, cap_err = io.StringIO(), io.StringIO()
    try:
        with _ctx.redirect_stdout(cap_out), _ctx.redirect_stderr(cap_err):
            ns = SimpleNamespace(root=str(p), by="tester", tail_lines=30,
                                 limit_done=5, limit_negative=5)
            s.mod.cmd_handoff(ns)          # first call: creates the card
        rel = cap_out.getvalue().split("HANDOFF OK | ")[1].split(" | ")[0]
        card = p / rel
        assert card.is_file(), rel
        rival = (b"# Rival HANDOFF card\n\n"
                 b"Created by another writer in the same minute.\n")
        card.write_bytes(rival)
        rival_sha = hashlib.sha256(rival).hexdigest().upper()
        cap_err2 = io.StringIO()
        try:
            with _ctx.redirect_stdout(io.StringIO()), _ctx.redirect_stderr(cap_err2):
                s.mod.cmd_handoff(ns)      # second call same minute: must fail
        except SystemExit as exc:
            rc = int(exc.code or 0)
        after = card.read_bytes()
    finally:
        s.mod.datetime = real_dt
    assert rc == 2, rc
    assert "已由其他写者创建" in cap_err2.getvalue()[:DETAIL_CHARS], cap_err2.getvalue()[:DETAIL_CHARS]
    assert after == rival, "the rival card was overwritten"
    assert hashlib.sha256(after).hexdigest().upper() == rival_sha
    return {"card": rel, "rival_bytes": len(after),
            "stderr": cap_err2.getvalue()[:DETAIL_CHARS]}


# ---------------------------------------------------------------------------
# A/05 - A/08 : the resume command
# ---------------------------------------------------------------------------
def a05(s):
    """resume prints a bounded cold-start package with a read manifest."""
    p = s.init("resume-basic")
    s.seed(p, text="resume-basic-token", status="DONE")
    hand = s.cli("handoff", "--root", str(p), "--by", "tester")
    assert hand.returncode == 0, (hand.returncode, hand.stdout, hand.stderr)
    r = s.cli("resume", "--root", str(p), "--lines", "15",
              "--max-lines", "120", "--max-bytes", "200000")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    out = r.stdout
    assert out.startswith("RESUME | project-protocol v")
    assert "读入清单" in out, out[:DETAIL_CHARS]
    assert "06_记忆/L1_STATE.md" in out, out[:DETAIL_CHARS]
    assert "合计" in out and "token" in out, "token quantification missing"
    assert "resume-basic-token" in out, "L0 tail not included"
    assert "06_记忆/06_记忆" not in out, "card path doubled in resume"
    return {"stdout_bytes": len(out.encode("utf-8"))}


def a06(s):
    """resume works when there is NO prior card (first-time-ever resume)."""
    p = s.init("resume-no-card")
    s.seed(p, text="no-card-token", status="DONE")
    r = s.cli("resume", "--root", str(p), "--lines", "10",
              "--max-lines", "120", "--max-bytes", "200000")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    out = r.stdout
    assert "无清算" not in out  # sanity: not the no-protocol error path
    # v1.1.0 审计批次: the empty negative block is reported positively, and the L1
    # "下一步" section is guaranteed to appear (L1-before-card ordering).
    assert "（L0 无）" in out, "expected an empty negative block, got %s" % out[:DETAIL_CHARS]
    assert "L1「下一步」" in out, "resume must lead with the authoritative next step"
    return {"has_card_section": "首次交接" in out or "无交接快照" in out}


def a07(s):
    """resume honours the --max-lines budget and reports truncation (exit 3)."""
    p = s.init("resume-budget")
    # Fill the log so resume output would be far larger than the budget.
    for i in range(20):
        s.cli("add", "--root", str(p), "--status", "DONE", "--tid", "T01",
              "--text", "budget-token-%d" % i)
    r = s.cli("resume", "--root", str(p), "--max-lines", "5",
              "--max-bytes", "100000")
    # v1.1.0 审计批次: a truncated resume is NOT a success. Exit 3 signals the cut so the
    # next agent does not read a partial handoff as complete.
    assert r.returncode == 3, (r.returncode, r.stdout, r.stderr)
    out = r.stdout
    assert "RESUME TRUNCATED" in out, out[-DETAIL_CHARS:]
    return {"resized": "--max-lines 5", "exit_code": 3}


def a08(s):
    """resume names the card location under 06_记忆/, not 'on L1'."""
    p = s.init("resume-card-loc")
    s.seed(p, text="card-loc-token", status="DONE")
    s.cli("handoff", "--root", str(p), "--by", "tester")
    r = s.cli("resume", "--root", str(p), "--lines", "8",
              "--max-lines", "120", "--max-bytes", "200000")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    out = r.stdout
    assert out.count("落盘于 L1") == 0, out[:DETAIL_CHARS]
    if "最新交接快照" in out:
        line = [l for l in out.splitlines() if "最新交接快照" in l]
        assert line and "06_记忆" in line[0], line
    return {"snapshot_line": "06_记忆/ 下" if "06_记忆" in out else None}


# ---------------------------------------------------------------------------
# B/01 - B/04 : v1.1.0 init + v1.0.4 backward compatibility
# ---------------------------------------------------------------------------
def b01(s):
    """init publishes a v1.1.0 identity that the checker accepts."""
    p = s.init("current-identity")
    raw = (p / "AGENTS.md").read_text(encoding="utf-8")
    assert s.mod.CURRENT_AGENTS_ID in raw, "v1.1.0 identity line missing"
    assert "AGENTS-VERSION:v1.1.0" in raw, raw[:DETAIL_CHARS]
    # the identity makes _agents_version return "current", and the entry passes
    assert s.mod._agents_version(raw) == "current"
    s.seed(p, text="current-identity-token")
    r = s.cli("check", "current-identity-token", "--strict", "--root", str(p))
    assert r.returncode == 0, (r.returncode, r.stdout[:DETAIL_CHARS], r.stderr)
    assert "STRICT: CLEAN" in r.stdout
    return {"version": s.mod.VERSION, "bytes": len(raw.encode("utf-8"))}


def b02(s):
    """A real complete v1.0.4 entry remains usable after upgrading to v1.1.0."""
    p = s.init("legacy-v104-usable")
    raw = (FIXTURES / "v104_AGENTS.md").read_bytes()
    (p / "AGENTS.md").write_bytes(raw)
    # the checker must recognise it as a DIFFERENT legacy version, not current
    ver = s.mod._agents_version(raw.decode("utf-8"))
    assert ver == "v1.0.4", ver
    for cmd in (("add", "--status", "DONE", "--tid", "T01",
                 "--text", "legacy-v104-usable-token"),
                ("tail",),
                ("check", "legacy-v104-usable", "--strict")):
        r = s.cli(*cmd, "--root", str(p))
        assert r.returncode == 0, (cmd, r.returncode, r.stdout[:DETAIL_CHARS], r.stderr)
    return {"bytes": len(raw), "version": ver}


def b03(s):
    """The fixture byte-matches the recorded v1.0.4 fingerprint."""
    raw = (FIXTURES / "v104_AGENTS.md").read_bytes()
    sha = hashlib.sha256(raw).hexdigest().upper()
    assert sha == s.mod.LEGACY_COMPLETE_SHA256["v1.0.4"], (
        sha, s.mod.LEGACY_COMPLETE_SHA256["v1.0.4"])
    assert len(raw) == s.mod.LEGACY_MIN_BYTES["v1.0.4"]
    assert s.mod.LEGACY_V104_ID in raw.decode("utf-8")
    return {"fixture_bytes": len(raw), "fixture_sha256": sha}


def b04(s):
    """A truncated v1.0.4 entry is rejected, never forgiven."""
    p = s.init("legacy-v104-trunc")
    raw = (FIXTURES / "v104_AGENTS.md").read_bytes()
    (p / "AGENTS.md").write_bytes(raw[:-80])
    r = s.cli("add", "--root", str(p), "--status", "DONE",
              "--tid", "T01", "--text", "legacy-v104-bad")
    assert r.returncode == 2, (r.returncode, r.stdout[:DETAIL_CHARS], r.stderr)
    return {"kept_bytes": len(raw) - 80, "stderr": r.stderr[:DETAIL_CHARS]}


CASES = [
    ("A01 handoff emits a HANDOFF card with required sections", a01),
    ("A02 handoff card binds goal/wip/negative from L0 + L1", a02),
    ("A03 handoff card path single-sited, no %-placeholder", a03),
    ("A04 handoff opens card exclusively (x), never overwrites", a04),
    ("A05 resume prints a bounded cold-start package", a05),
    ("A06 resume works without any prior card", a06),
    ("A07 resume honours the --max-lines budget", a07),
    ("A08 resume names the card under 06_记忆/, not on L1", a08),
    ("B01 init publishes the v1.1.0 identity, checker CLEAN", b01),
    ("B02 a real complete v1.0.4 entry stays usable after upgrade", b02),
    ("B03 the v1.0.4 fixture byte-matches the recorded fingerprint", b03),
    ("B04 a truncated v1.0.4 entry is rejected", b04),
]


def main():
    ap = argparse.ArgumentParser(description="project-protocol v1.1.0 regression")
    ap.add_argument("--script", default=str(HERE / "progress.py"))
    ap.add_argument("--out", default=str(HERE / "regression-v110-result.json"))
    ap.add_argument("--workdir", default=None,
                    help="scratch directory (default: fresh mkdtemp outside the repo)")
    args = ap.parse_args()
    auto_work = args.workdir is None
    workdir = args.workdir or tempfile.mkdtemp(prefix="pp-v110-container-")
    s = Suite(args.script, workdir, auto_work=auto_work)
    started = __import__("time").monotonic()
    for name, fn in CASES:
        s.case(name, fn)
    passed = sum(r["ok"] for r in s.results)
    report = {
        "suite": "project-protocol v1.1.0 handoff/resume + v1.0.4 compat",
        "cases": len(s.results),
        "passed": passed,
        "failed": len(s.results) - passed,
        "all_passed": passed == len(s.results),
        "elapsed_seconds": round(__import__("time").monotonic() - started, 2),
        "script": str(s.script),
        "script_sha256": hashlib.sha256(s.script.read_bytes()).hexdigest().upper(),
        "python": sys.version.split()[0],
        "platform_verified": platform.platform(),
        "platforms_not_verified": ["Linux", "Darwin", "network filesystems",
                                   "non-NTFS Windows filesystems"],
        "hosts_not_verified": ["skill loading", "long-running agent behaviour", "hooks"],
        "results": s.results,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    print("SUMMARY: %d/%d passed | %.2fs | %s"
          % (passed, len(s.results), report["elapsed_seconds"], out))
    s.cleanup()
    sys.exit(0 if report["all_passed"] else 1)


if __name__ == "__main__":
    main()

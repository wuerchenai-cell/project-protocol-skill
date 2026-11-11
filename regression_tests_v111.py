#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project-protocol v1.1.0+ review regression suite (v111).

Covers the eight behaviours introduced by the post-v1.1.0 review batch, all of
which are deterministic and offline. Every case runs on an isolated temporary
project under the system temp dir (the default workdir is a fresh mkdtemp()
container that self-cleans), so running the suite never dirties the repo.

Scope:
  N01  0 / negative semantics are ONE rule across tail / resume / handoff / check
  N02  a WIP is ended ONLY by a later same-TID DONE / REJECTED / BLOCKED
  N03  artifact paths with spaces survive normalisation as a single record
  N04  a default resume leads with the authoritative L1「下一步」(L1 before card)
  N05  publish_completion falls back to O_EXCL copy when os.link raises EINVAL
  N06  a 16-byte / UTF-16 / UTF-32 / raw-NUL binary fixture is rejected, not poisoned
  N07  CJK survives the cp1252-capable subprocess pipe (utf-8 reconfigure)
  N08  handoff reads L0/L1 without taking the write lock (no deadlock under lock)

No assertion of the earlier v1.0.2 / v1.0.3 / v1.0.4 / v1.1.0 suites was
weakened; those suites still run and must still pass independently.

Run:
  python regression_tests_v111.py --script progress.py --out result-v111.json
                                  [--workdir <container>]
"""
import argparse
import contextlib
import errno
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
import time
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
FIXTURES = HERE / "fixtures"
DETAIL_CHARS = 400


def _load(script):
    spec = importlib.util.spec_from_file_location("pp111_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def worker_hold(argv):
    """Hold the real cross-process lock for N seconds; parent kills/kills-waits."""
    # argv = ["hold", lockpath, seconds, ready]
    _, lockpath, seconds, ready = argv
    mod = _load(Path(__file__).resolve().parent / "progress.py")
    with mod.CrossProcessLock(Path(lockpath), timeout=10.0):
        Path(ready).write_text("locked", encoding="utf-8")
        time.sleep(float(seconds))


class Suite:
    def __init__(self, script, workdir, auto_work=False):
        self.script = Path(script).resolve()
        self.mod = _load(self.script)
        self.auto_work = auto_work
        self.work = Path(workdir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-v111-", dir=str(self.work)))
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
        args = ["handoff", "--root", str(p), "--by", by]
        if tail_lines is not None:
            args += ["--tail-lines", str(tail_lines)]
        r = self.cli(*args)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        rel = r.stdout.split("HANDOFF OK | ")[1].split(" | ")[0]
        return p / rel

    def set_next(self, p, text):
        """Write a probeable body into the L1「下一步」section."""
        lp = p / "06_记忆" / "L1_STATE.md"
        t = lp.read_text(encoding="utf-8")
        assert "## 下一步" in t, "L1 template missing 下一步 section"
        t = t.replace("## 下一步\n\n", "## 下一步\n\n" + text + "\n\n", 1)
        lp.write_text(t, encoding="utf-8")

    def capture_init(self, p):
        """Run cmd_init in-process (so in-process patches apply) and capture."""
        so, se = io.StringIO(), io.StringIO()
        rc = 0
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            try:
                self.mod.cmd_init(SimpleNamespace(directory=str(p)))
            except SystemExit as exc:
                rc = int(exc.code or 0)
        return rc, so.getvalue(), se.getvalue()

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
# N01 : 0 / negative semantics — ONE rule everywhere
# ---------------------------------------------------------------------------
def n01a(s):
    """tail --lines 0 prints ZERO rows; negative is a usage error (exit 2)."""
    p = s.init("zero-tail")
    s.seed(p, text="zero-tail-token")
    r0 = s.cli("tail", "--root", str(p), "--lines", "0")
    assert r0.returncode == 0, (r0.returncode, r0.stdout, r0.stderr)
    assert r0.stdout == "", repr(r0.stdout)
    rneg = s.cli("tail", "--root", str(p), "--lines", "-3")
    assert rneg.returncode == 2, (rneg.returncode, rneg.stdout, rneg.stderr)
    return {"zero_rows": "stdout empty", "negative_rc": rneg.returncode}


def n01b(s):
    """resume --lines 0 yields an empty L0 tail; negative is a usage error."""
    p = s.init("zero-resume")
    s.seed(p, text="zero-resume-token")
    r0 = s.cli("resume", "--root", str(p), "--lines", "0",
               "--max-lines", "200", "--max-bytes", "60000")
    assert r0.returncode == 0, (r0.returncode, r0.stdout, r0.stderr)
    assert "末尾 0 条" in r0.stdout, r0.stdout[:DETAIL_CHARS]
    rneg = s.cli("resume", "--root", str(p), "--lines", "-1")
    assert rneg.returncode == 2, (rneg.returncode, rneg.stdout, rneg.stderr)
    return {"zero_tail_present": "末尾 0 条", "negative_rc": rneg.returncode}


def n01c(s):
    """handoff limit flags: 0 = empty block, negative = usage error (exit 2)."""
    p = s.init("zero-handoff")
    s.seed(p, text="zero-handoff-token", status="DONE")
    card = s.handoff_ok(p, tail_lines=0)
    body = card.read_text(encoding="utf-8")
    done_blk = body.split("## 最近完成")[1].split("##")[0]
    # limit_done default 5, but tail_lines 0 -> no DONE in the sample window
    assert "zero-handoff-token" not in done_blk, done_blk[:DETAIL_CHARS]
    # negative limits are rejected at argparse time
    assert s.cli("handoff", "--root", str(p), "--limit-done", "-1").returncode == 2
    assert s.cli("handoff", "--root", str(p), "--limit-negative", "-1").returncode == 2
    assert s.cli("handoff", "--root", str(p), "--tail-lines", "-2").returncode == 2
    return {"tail0_empty_done": True, "negative_rc": 2}


def n01d(s):
    """check --max-lines 0 gives statistics only; negative is rejected."""
    p = s.init("zero-check")
    s.seed(p, text="zero-check-token")
    r0 = s.cli("check", "zero-check", "--max-lines", "0", "--root", str(p))
    assert r0.returncode == 0, (r0.returncode, r0.stdout, r0.stderr)
    assert "shown: 0" in r0.stdout, r0.stdout[:DETAIL_CHARS]
    assert "zero-check-token" not in r0.stdout.split("CHECK ")[-1], r0.stdout[:DETAIL_CHARS]
    rneg = s.cli("check", "zero-check", "--max-lines", "-1", "--root", str(p))
    assert rneg.returncode == 2, (rneg.returncode, rneg.stdout, rneg.stderr)
    return {"stats_only": "shown: 0", "negative_rc": rneg.returncode}


# ---------------------------------------------------------------------------
# N02 : a WIP is ended ONLY by a later same-TID DONE / REJECTED / BLOCKED
# ---------------------------------------------------------------------------
def _wip_block(card, token):
    """Extract the 下一步 block of a card and check whether `token` is its source."""
    body = card.read_text(encoding="utf-8")
    sec = body.split("## 下一步（优先执行）")[1].split("## 交接给")[0]
    return sec


def n02a(s):
    """WIP T01 then DONE T01: the WIP is superseded -> no '从最近 WIP' line."""
    p = s.init("wip-done")
    s.seed(p, tid="T01", status="WIP", text="wip-token", next="progress.py resume")
    s.seed(p, tid="T01", status="DONE", text="done-token")
    s.set_next(p, "L1-PLAN-DONE")
    card = s.handoff_ok(p, tail_lines=30)
    sec = _wip_block(card, "wip-token")
    assert "从最近 WIP" not in sec, sec[:DETAIL_CHARS]
    assert "L1-PLAN-DONE" in sec, sec[:DETAIL_CHARS]
    return {"superseded_by_same_tid_done": True}


def n02b(s):
    """WIP T01 then DONE T02 (different TID): the WIP T01 stays active."""
    p = s.init("wip-other-tid")
    s.seed(p, tid="T01", status="WIP", text="wipT01", next="progress.py resume --lines 10")
    s.seed(p, tid="T02", status="DONE", text="doneT02")
    s.set_next(p, "L1-PLAN-OTHER")
    card = s.handoff_ok(p, tail_lines=30)
    sec = _wip_block(card, "wipT01")
    assert "从最近 WIP：progress.py resume --lines 10" in sec, sec[:DETAIL_CHARS]
    return {"different_tid_keeps_wip": True}


def n02c(s):
    """DECIDED on the SAME TID must NOT end the WIP (only DONE/REJECTED/BLOCKED)."""
    p = s.init("wip-decided")
    s.seed(p, tid="T01", status="WIP", text="wipT01", next="progress.py resume")
    s.seed(p, tid="T01", status="DECIDED", text="decidedT01")
    s.set_next(p, "L1-PLAN-DECIDED")
    card = s.handoff_ok(p, tail_lines=30)
    sec = _wip_block(card, "wipT01")
    assert "从最近 WIP" in sec, sec[:DETAIL_CHARS]
    return {"decided_does_not_end_wip": True}


def n02d(s):
    """REJECTED / BLOCKED on the SAME TID supersede the WIP."""
    for neg in ("REJECTED", "BLOCKED"):
        p = s.init("wip-neg-" + neg.lower())
        s.seed(p, tid="T01", status="WIP", text="wipT01", next="progress.py resume")
        s.seed(p, tid="T01", status=neg, text="negT01")
        s.set_next(p, "L1-PLAN-NEG")
        card = s.handoff_ok(p, tail_lines=30)
        sec = _wip_block(card, "wipT01")
        assert "从最近 WIP" not in sec, (neg, sec[:DETAIL_CHARS])
    return {"rejected_blocked_supersede": ["REJECTED", "BLOCKED"]}


# ---------------------------------------------------------------------------
# N03 : artifact paths with spaces survive normalisation as one record
# ---------------------------------------------------------------------------
def n03(s):
    """A path containing spaces is logged verbatim on one line and read back."""
    p = s.init("space-path")
    art = "04_成果/T01/交付/我的 文件 带 空格.pptx"
    s.seed(p, text="space-token", artifact=art)
    r = s.cli("tail", "--root", str(p), "--lines", "5")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    line = [l for l in r.stdout.splitlines() if "space-token" in l]
    assert len(line) == 1, line
    assert art in line[0], line[0][:DETAIL_CHARS]
    # the record is still exactly ONE physical line (no separator splitting)
    assert line[0].count("|") == 4, line[0][:DETAIL_CHARS]
    # check can still find it
    c = s.cli("check", "space-token", "--strict", "--root", str(p))
    assert c.returncode == 0, (c.returncode, c.stdout, c.stderr)
    return {"path": art, "pipe_count": line[0].count("|")}


# ---------------------------------------------------------------------------
# N04 : a default resume leads with the authoritative L1「下一步」
# ---------------------------------------------------------------------------
def n04(s):
    """Even with a large card, the L1 next-step is printed BEFORE the card."""
    p = s.init("resume-l1-first")
    for i in range(15):
        s.seed(p, text="l1first-token-%d" % i)
    s.set_next(p, "L1-NEXT-PLAN-TOP")
    s.cli("handoff", "--root", str(p), "--by", "tester")
    r = s.cli("resume", "--root", str(p), "--lines", "30",
              "--max-lines", "400", "--max-bytes", "262144")
    assert r.returncode == 0, (r.returncode, r.stdout[:DETAIL_CHARS], r.stderr)
    out = r.stdout
    i_next = out.find("=== 先读这里：L1「下一步」（权威，按此执行）===")
    i_card = out.find("=== 最新交接快照（仅供导航")
    assert i_next != -1, out[:DETAIL_CHARS]
    assert i_card != -1, out[:DETAIL_CHARS]
    assert i_next < i_card, "L1 next-step must be printed before the card"
    assert "L1-NEXT-PLAN-TOP" in out.split("=== 最新交接快照")[0], "L1 next-step hidden by card"
    return {"i_next": i_next, "i_card": i_card}


# ---------------------------------------------------------------------------
# N05 : publish_completion falls back to O_EXCL copy when os.link raises EINVAL
# ---------------------------------------------------------------------------
def n05(s):
    """Force os.link EINVAL; init must still publish a byte-correct AGENTS.md."""
    p = s.root("exml-project")
    real_link = s.mod.os.link

    def fake_link(src, dst):
        raise OSError(errno.EINVAL, "simulated EINVAL (hardlink unsupported)")

    s.mod.os.link = fake_link
    try:
        rc, out, err = s.capture_init(p)
    finally:
        s.mod.os.link = real_link
    assert rc == 0, (rc, err[:DETAIL_CHARS])
    raw = (p / "AGENTS.md").read_bytes()
    assert raw == s.mod.COMPLETION_CONTENT.encode("utf-8"), "O_EXCL copy bytes differ"
    assert not (p / "AGENTS.md.init.tmp").exists(), "temp file should be unlinked"
    return {"fallback": "O_EXCL|O_CREAT", "bytes": len(raw)}


# ---------------------------------------------------------------------------
# N06 : 16-byte / UTF-16 / UTF-32 / raw-NUL binary fixture is rejected, not poisoned
# ---------------------------------------------------------------------------
def n06(s):
    """A log poisoned with non-UTF-8 binary is rejected and left byte-unchanged."""
    poisons = {
        "utf16_nobom": "hi".encode("utf-16-le"),   # NUL at index 1
        "utf16_bom": "hi".encode("utf-16"),        # BOM ff fe
        "utf32_bom": "hi".encode("utf-32"),        # BOM ff fe 00 00
        "raw_nul16": bytes(range(16)),             # NUL at index 0
    }
    results = {}
    for label, data in poisons.items():
        p = s.init("poison-" + label)
        lp = p / "06_记忆" / "L0_LOG.md"
        lp.write_bytes(data)
        before = lp.read_bytes()
        r = s.cli("add", "--root", str(p), "--status", "DONE",
                  "--tid", "T01", "--text", "probe-" + label)
        assert r.returncode == 2, (label, r.returncode, r.stdout, r.stderr)
        assert lp.read_bytes() == before, (label, "file was modified")
        results[label] = {"rc": r.returncode, "bytes_unchanged": True,
                          "n": len(data)}
    return results


# ---------------------------------------------------------------------------
# N07 : CJK survives the cp1252-capable subprocess pipe (utf-8 reconfigure)
# ---------------------------------------------------------------------------
def n07(s):
    """A UTF-16 poison yields a CJK error that round-trips through the pipe."""
    p = s.init("pipe-cjk")
    lp = p / "06_记忆" / "L0_LOG.md"
    lp.write_bytes("hi".encode("utf-16"))  # BOM ff fe -> UTF-16 branch
    before = lp.read_bytes()
    r = s.cli("add", "--root", str(p), "--status", "DONE", "--tid", "T01",
              "--text", "probe-cjk")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "疑似以 UTF-16" in r.stderr, r.stderr[:DETAIL_CHARS]
    assert lp.read_bytes() == before, "poisoned file was modified"
    # a successful add also round-trips CJK through the pipe
    r2 = s.cli("add", "--root", str(s.init("pipe-ok")),
               "--status", "DONE", "--tid", "T01", "--text", "带空格成功记录")
    assert r2.returncode == 0, (r2.returncode, r2.stdout, r2.stderr)
    assert "带空格成功记录" in r2.stdout, r2.stdout[:DETAIL_CHARS]
    return {"error_cjk_intact": "疑似以 UTF-16" in r.stderr,
            "success_cjk_intact": "带空格成功记录" in r2.stdout}


# ---------------------------------------------------------------------------
# N08 : handoff reads L0/L1 WITHOUT taking the write lock (no deadlock)
# ---------------------------------------------------------------------------
def n08(s):
    """While another process holds the write lock, handoff (read-only) still works."""
    p = s.init("handoff-locked")
    s.seed(p, text="locked-token")
    lock = p / "06_记忆" / "L0_LOG.md.lock"
    ready = p / "holder.ready"
    holder = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
         "__worker__", "hold",
         str(lock), "4", str(ready)],
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    try:
        ready_seen = False
        for _ in range(120):
            if ready.exists():
                ready_seen = True
                break
            time.sleep(0.05)
        assert ready_seen, "lock holder never became ready"
        # handoff does NOT acquire the write lock -> must not block or fail
        r = s.cli("handoff", "--root", str(p), "--by", "tester", timeout=10)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        assert "HANDOFF OK" in r.stdout, r.stdout[:DETAIL_CHARS]
    finally:
        if holder.poll() is None:
            holder.kill()
        holder.wait(timeout=8)
    return {"handoff_rc_while_locked": 0, "holder_killed": holder.returncode}


CASES = [
    ("N01a tail --lines 0/negative semantics", n01a),
    ("N01b resume --lines 0/negative semantics", n01b),
    ("N01c handoff limit 0/negative semantics", n01c),
    ("N01d check --max-lines 0/negative semantics", n01d),
    ("N02a WIP superseded by same-TID DONE", n02a),
    ("N02b WIP survives a later different-TID DONE", n02b),
    ("N02c DECIDED (same TID) does NOT end the WIP", n02c),
    ("N02d REJECTED/BLOCKED (same TID) supersede the WIP", n02d),
    ("N03 artifact path with spaces stays one record", n03),
    ("N04 default resume leads with L1 下一步", n04),
    ("N05 publish falls back to O_EXCL on EINVAL", n05),
    ("N06 binary fixtures rejected, not poisoned", n06),
    ("N07 CJK survives the cp1252 subprocess pipe", n07),
    ("N08 handoff reads without the write lock", n08),
]


def main():
    ap = argparse.ArgumentParser(description="project-protocol v111 review regression")
    ap.add_argument("--script", default=str(HERE / "progress.py"))
    ap.add_argument("--out", default=str(HERE / "regression-v111-result.json"))
    ap.add_argument("--workdir", default=None,
                    help="scratch container (default: fresh mkdtemp outside the repo)")
    args = ap.parse_args()
    # Default to a container in the system temp dir; self-clean it at the end.
    auto_work = args.workdir is None
    workdir = args.workdir or tempfile.mkdtemp(prefix="pp-v111-container-")
    s = Suite(args.script, workdir, auto_work=auto_work)
    started = __import__("time").monotonic()
    for name, fn in CASES:
        s.case(name, fn)
    passed = sum(r["ok"] for r in s.results)
    report = {
        "suite": "project-protocol v111 review regression (0/负数/WIP/空格路径/L1-first/EINVAL/二进制/cp1252/读锁)",
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
    if len(sys.argv) > 1 and sys.argv[1] == "__worker__":
        worker_hold(sys.argv[2:])
    else:
        main()

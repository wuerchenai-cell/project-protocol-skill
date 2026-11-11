#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project-protocol v1.0.2 regression suite.

IMPORTANT: every assertion below states EXPECTED CORRECT BEHAVIOR.
Running this suite against v1.0.1 is expected to FAIL (that run is the
defect-reproduction evidence); running it against v1.0.2 must PASS.
No assertion encodes a known defect as the desired outcome.

Usage:
  python regression_tests.py --script <path/to/progress.py> --out <result.json>
                             [--rounds 10] [--workdir <tmp-dir>]

Internal worker entry (used by the concurrency tests):
  python regression_tests.py __worker__ <mode> ...
"""
from __future__ import annotations

import argparse
import contextlib
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

LOG_LINE_RE = re.compile(r"^- \[\d{4}-\d{2}-\d{2} \d{2}:\d{2}\] ([A-Z]+) \| ")
HERE = Path(__file__).resolve().parent


# --------------------------------------------------------------------------
# worker processes (real OS processes, real files, no mocked writes)
# --------------------------------------------------------------------------
def _load(script):
    spec = importlib.util.spec_from_file_location("pp_under_test", str(script))
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def worker_cliaddloop(argv):
    """One OS process issuing `count` real CLI `add` calls to a shared L0."""
    script, root, who, count, out = argv
    rcs, lines = [], []
    for i in range(int(count)):
        p = subprocess.run(
            [sys.executable, "-X", "utf8", str(script), "add", "--root", str(root),
             "--status", "DONE", "--tid", "T01", "--text", "%s-%d" % (who, i),
             "--path", "-", "--next", "-"],
            capture_output=True, text=True, encoding="utf-8", timeout=60,
        )
        rcs.append(p.returncode)
        lines.append(p.stdout.strip())
    Path(out).write_text(json.dumps({"who": who, "rcs": rcs, "lines": lines}), encoding="utf-8")


def worker_lockhold(argv):
    """Hold the very same lock implementation used by `add` for N seconds."""
    script, lockpath, seconds, ready, out, stale = argv
    mod = _load(script)
    lk = mod.CrossProcessLock(Path(lockpath), timeout=10.0)
    with lk:
        if stale == "1":
            old = time.time() - 10 * 86400
            os.utime(lockpath, (old, old))  # old mtime must NOT allow stealing
        Path(ready).write_text("locked", encoding="utf-8")
        time.sleep(float(seconds))
    Path(out).write_text(json.dumps({"held": True}), encoding="utf-8")


def worker_initfail(argv):
    """Inject an OSError on the 5th skeleton file to test mid-init recovery."""
    script, root, out = argv
    mod = _load(script)
    orig_open = Path.open
    state = {"n": 0}

    def patched(self, mode="r", *a, **kw):
        if mode == "x":
            state["n"] += 1
            if state["n"] == 5:
                raise OSError("injected write failure (regression test)")
        return orig_open(self, mode, *a, **kw)

    Path.open = patched
    so, se = io.StringIO(), io.StringIO()
    sys.argv = [str(script), "init", str(root)]
    rc = 0
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            mod.main()
    except SystemExit as exc:
        rc = int(exc.code or 0)
    except Exception as exc:  # noqa: BLE001 - worker reports, does not hide
        rc = 99
        se.write("UNCAUGHT: %r" % (exc,))
    Path(out).write_text(
        json.dumps({"rc": rc, "stdout": so.getvalue(), "stderr": se.getvalue()},
                   ensure_ascii=False), encoding="utf-8")


def worker_main(argv):
    mode = argv[0]
    {
        "cliaddloop": worker_cliaddloop,
        "lockhold": worker_lockhold,
        "initfail": worker_initfail,
    }[mode](argv[1:])


# --------------------------------------------------------------------------
# harness
# --------------------------------------------------------------------------
class Suite:
    def __init__(self, script, workdir, rounds, auto_work=False):
        self.script = Path(script).resolve()
        self.auto_work = auto_work
        self.work = Path(workdir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.rounds = rounds
        self.results = []
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-reg-", dir=str(self.work)))
        self.counter = 0

    def cli(self, *args, timeout=60):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(self.script), *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )

    def worker(self, *args, timeout=300):
        return subprocess.run(
            [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
             "__worker__", *map(str, args)],
            capture_output=True, text=True, encoding="utf-8", timeout=timeout,
        )

    def root(self, tag):
        self.counter += 1
        p = self.tmp / ("%s-%03d" % (tag, self.counter))
        p.mkdir()
        return p

    def init(self, tag):
        r = self.root(tag)
        p = self.cli("init", r)
        assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
        return r

    def snapshot(self, root):
        return {p.relative_to(root).as_posix(): p.read_bytes()
                for p in sorted(root.rglob("*")) if p.is_file()}

    def loglines(self, root):
        p = root / "06_记忆" / "L0_LOG.md"
        return [ln for ln in p.read_text(encoding="utf-8").splitlines()
                if ln.startswith("- [")]

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


# --------------------------------------------------------------------------
# R01..R06  initialization
# --------------------------------------------------------------------------
def r01(s):
    root = s.root("r01")
    p = s.cli("init", root)
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    files = sorted(x.relative_to(root).as_posix() for x in root.rglob("*") if x.is_file())
    assert len(files) == 14, files
    assert "AGENTS.md" in files and "06_记忆/L0_LOG.md" in files, files
    return {"files": len(files)}


def r02(s):
    root = s.root("r02")
    (root / "README.md").write_text("Existing project.\n", encoding="utf-8")
    before = s.snapshot(root)
    p = s.cli("init", root)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert s.snapshot(root) == before, "non-empty project was modified"
    assert not (root / "AGENTS.md").exists()
    assert "非空" in (p.stderr + p.stdout) or "non-empty" in (p.stderr + p.stdout).lower()
    return {"rc": p.returncode, "unchanged": True}


def r03(s):
    root = s.init("r03")
    before = s.snapshot(root)
    p = s.cli("init", root)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert s.snapshot(root) == before
    return {"rc": p.returncode}


def r04(s):
    root = s.root("r04")
    (root / "AGENTS.md").write_text("# pre-existing\n", encoding="utf-8")
    before = s.snapshot(root)
    p = s.cli("init", root)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert s.snapshot(root) == before
    return {"rc": p.returncode}


def r05(s):
    root = s.root("r05")
    (root / "05_证据").write_text("ordinary file\n", encoding="utf-8")  # parent path conflict
    before = s.snapshot(root)
    p = s.cli("init", root)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert s.snapshot(root) == before, "partial skeleton left behind"
    assert not (root / "AGENTS.md").exists()
    assert "父路径" in (p.stderr + p.stdout) or "parent" in (p.stderr + p.stdout).lower()
    return {"rc": p.returncode, "zero_writes": True}


def r06(s):
    root = s.root("r06")
    out = root / "worker.json"
    p = s.worker("initfail", s.script, root, out)
    data = json.loads(out.read_text(encoding="utf-8"))
    text = data["stdout"] + data["stderr"]
    assert data["rc"] != 0, data
    assert not (root / "AGENTS.md").exists(), "AGENTS.md must be the completion marker"
    assert "INIT FAILED" in text, text
    assert "恢复" in text or "recovery" in text.lower(), text
    created = sorted(x.relative_to(root).as_posix() for x in root.rglob("*") if x.is_file())
    assert len(created) >= 1 and len(created) < 14, created
    return {"worker_rc": p.returncode, "inner_rc": data["rc"],
            "created_files": created, "agents_written": False}


# --------------------------------------------------------------------------
# R07..R13  add / tail / detection
# --------------------------------------------------------------------------
def r07(s):
    root = s.init("r07")
    p = s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
              "--text", "正常节点", "--path", "04_成果/T01/交付/报告.txt", "--next", "review")
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    line = p.stdout.strip()
    t = s.cli("tail", "--root", root, "--lines", "1")
    assert t.returncode == 0 and t.stdout.strip() == line, (t.returncode, t.stdout)
    assert len(line.split(" | ")) == 5, line
    assert LOG_LINE_RE.match(line)
    return {"line": line}


def r08(s):
    root = s.init("r08")
    p = s.cli("add", "--root", root, "--status", "DECIDED", "--tid", "T01",
              "--text", "one\r\nREJECTED | fake\tfield")
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    out = p.stdout.strip()
    assert len(out.splitlines()) == 1, out
    assert len(out.split(" | ")) == 5, out
    assert "fake" in out and "REJECTED" in out and " | REJECTED | " not in out
    assert len(s.loglines(root)) == 1
    return {"line": out}


def r09(s):
    root = s.init("r09")
    lp = root / "06_记忆" / "L0_LOG.md"
    lp.write_text("- [2026-11-06 10:00] DONE | T01 | previous | - | NEXT -", encoding="utf-8")
    p = s.cli("add", "--root", root, "--status", "REJECTED", "--tid", "T01",
              "--text", "newline-bug-method")
    assert p.returncode == 0, (p.returncode, p.stdout, p.stderr)
    rows = s.loglines(root)
    assert len(rows) == 2, rows
    assert all(LOG_LINE_RE.match(x) and len(x.split(" | ")) == 5 for x in rows), rows
    assert rows[0].endswith("NEXT -") and "previous" in rows[0]
    c = s.cli("check", "newline-bug-method", "--strict", "--root", root)
    assert c.returncode == 1, (c.returncode, c.stdout, c.stderr)
    return {"rows": rows}


def r10(s):
    root = s.init("r10")
    assert s.cli("add", "--root", root, "--status", "REJECTED", "--tid", "T01",
                 "--text", "doomed-method").returncode == 0
    c = s.cli("check", "doomed-method", "--strict", "--root", root)
    assert c.returncode == 1, (c.returncode, c.stdout, c.stderr)
    assert "rejected/blocked: 1" in c.stdout, c.stdout
    return {"rc": c.returncode}


def r11(s):
    root = s.init("r11")
    arc = root / "99_归档" / "L0_LOG_2026-10.md"
    arc.write_text("- [2026-10-01 10:00] BLOCKED | T01 | archived-method | - | NEXT -\n",
                   encoding="utf-8")
    c = s.cli("check", "archived-method", "--strict", "--root", root)
    assert c.returncode == 1, (c.returncode, c.stdout, c.stderr)
    assert "99_归档/L0_LOG_2026-10.md" in c.stdout
    return {"rc": c.returncode}


def _write_idea(root, name, status, reason, body):
    d = root / "01_任务" / "待定"
    d.mkdir(parents=True, exist_ok=True)
    (d / name).write_text(
        "---\ndate: 20261106\nstatus: %s\nreason: %s\n---\n%s\n" % (status, reason, body),
        encoding="utf-8")


def r12(s):
    root = s.init("r12")
    _write_idea(root, "20261106-idea.md", "REJECTED", "与 T03 已验证结论冲突",
                "# discarded-idea-method\n正文仅引用 REJECTED 这个词的写法不应被误判。")
    c = s.cli("check", "discarded-idea-method", "--strict", "--root", root)
    assert c.returncode == 1, (c.returncode, c.stdout, c.stderr)
    assert "REJECTED" in c.stdout and "与 T03" in c.stdout, c.stdout
    assert "01_任务/待定/20261106-idea.md" in c.stdout, c.stdout
    plain = s.cli("check", "discarded-idea-method", "--root", root)
    assert plain.returncode == 0, (plain.returncode, plain.stdout, plain.stderr)
    return {"strict_rc": c.returncode, "plain_rc": plain.returncode,
            "strict_stdout": c.stdout.strip()[:600]}


def r13(s):
    root = s.init("r13")
    assert s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
                 "--text", "REJECTED token is quoted").returncode == 0
    c = s.cli("check", "REJECTED", "--strict", "--root", root)
    assert c.returncode == 0, (c.returncode, c.stdout, c.stderr)
    assert "rejected/blocked: 0" in c.stdout, c.stdout
    return {"rc": c.returncode}


# --------------------------------------------------------------------------
# R14..R16  error roots
# --------------------------------------------------------------------------
def r14(s):
    missing = s.tmp / "does-not-exist-r14"
    c = s.cli("check", "something", "--strict", "--root", missing)
    assert c.returncode == 2, (c.returncode, c.stdout, c.stderr)
    assert "files scanned: 0" not in c.stdout, c.stdout
    assert "ERROR" in c.stderr, c.stderr
    return {"rc": c.returncode, "stderr": c.stderr.strip()[:300]}


def r15(s):
    root = s.root("r15")
    (root / "README.md").write_text("old project\n", encoding="utf-8")
    c = s.cli("check", "something", "--strict", "--root", root)
    assert c.returncode == 2, (c.returncode, c.stdout, c.stderr)
    text = c.stderr + c.stdout
    assert "协议" in text or "protocol" in text.lower(), text
    for cmd in (("add", "--root", root, "--status", "DONE", "--tid", "T01", "--text", "x"),
                ("tail", "--root", root)):
        p = s.cli(*cmd)
        assert p.returncode == 2, (cmd, p.returncode, p.stdout, p.stderr)
    return {"rc": c.returncode, "stderr": c.stderr.strip()[:300]}


def r16(s):
    root = s.init("r16")
    lp = root / "06_记忆" / "L0_LOG.md"
    lp.unlink()
    lp.mkdir()  # necessary protocol file exists but is not readable as a file
    c = s.cli("check", "something", "--root", root)
    assert c.returncode == 2, (c.returncode, c.stdout, c.stderr)
    return {"rc": c.returncode, "stderr": c.stderr.strip()[:300]}


# --------------------------------------------------------------------------
# R17..R19  argument validation
# --------------------------------------------------------------------------
def r17(s):
    root = s.init("r17")
    bad = ["C:/outside/result.txt", "../outside/result.txt", "/absolute/result.txt",
           "~/outside.txt", "a/../../b.txt", "D:\\outside\\r.txt"]
    accepted = []
    for value in bad:
        p = s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
                  "--text", "probe", "--path", value)
        assert p.returncode == 2, (value, p.returncode, p.stdout, p.stderr)
        assert "路径" in p.stderr or "path" in p.stderr.lower(), (value, p.stderr)
        accepted.append({"input": value, "rc": p.returncode})
    good = s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
                 "--text", "probe-good", "--path", "04_成果\\T01\\交付\\报告.txt")
    assert good.returncode == 0, (good.returncode, good.stdout, good.stderr)
    assert "04_成果/T01/交付/报告.txt" in good.stdout, good.stdout
    assert "04_成果\\T01" not in good.stdout
    assert len(s.loglines(root)) == 1, "rejected paths must not be appended"
    return {"rejected": accepted, "normalized": good.stdout.strip()}


def r18(s):
    root = s.init("r18")
    valid = ["T01", "T09", "T10", "T99", "T100", "T123"]
    invalid = ["T00", "T0", "T1", "T010", "T１２", "T１2", "t01", "X01", "T01x", "T-1"]
    for tid in valid:
        p = s.cli("add", "--root", root, "--status", "DONE", "--tid", tid, "--text", "probe-" + tid)
        assert p.returncode == 0, (tid, p.returncode, p.stderr)
    for tid in invalid:
        p = s.cli("add", "--root", root, "--status", "DONE", "--tid", tid, "--text", "probe-bad")
        assert p.returncode == 2, (tid, p.returncode, p.stdout, p.stderr)
    assert len(s.loglines(root)) == len(valid), s.loglines(root)
    return {"valid": valid, "invalid": invalid}


def r19(s):
    root = s.init("r19")
    for i in range(3):
        assert s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
                     "--text", "record-%d" % i).returncode == 0
    zero = s.cli("tail", "--root", root, "--lines", "0")
    assert zero.returncode == 0 and zero.stdout.strip() == "", (zero.returncode, zero.stdout)
    neg = s.cli("tail", "--root", root, "--lines", "-1")
    assert neg.returncode == 2, (neg.returncode, neg.stdout, neg.stderr)
    default = s.cli("tail", "--root", root)
    assert len(default.stdout.strip().splitlines()) == 3
    return {"zero_lines": len(zero.stdout.strip().splitlines()), "negative_rc": neg.returncode}


# --------------------------------------------------------------------------
# R20..R21  output budget / strict on full result
# --------------------------------------------------------------------------
def r20(s):
    root = s.init("r20")
    rows = ["- [2026-11-06 10:00] DONE | T01 | common-keyword-%d | - | NEXT -\n" % i
            for i in range(1000)]
    (root / "06_记忆" / "L0_LOG.md").write_text("".join(rows), encoding="utf-8")
    c = s.cli("check", "common-keyword", "--root", root)
    assert c.returncode == 0, (c.returncode, c.stdout, c.stderr)
    assert "hits: 1000" in c.stdout, c.stdout[:300]
    assert len(c.stdout.splitlines()) <= 60, len(c.stdout.splitlines())
    assert len(c.stdout.encode("utf-8")) < 20000, len(c.stdout.encode("utf-8"))
    low = c.stdout.lower()
    assert "截断" in c.stdout or "truncated" in low or "budget" in low, c.stdout[-400:]
    return {"rc": c.returncode, "output_lines": len(c.stdout.splitlines()),
            "output_bytes": len(c.stdout.encode("utf-8"))}


def r21(s):
    root = s.init("r21")
    rows = ["- [2026-11-06 10:00] DONE | T01 | budget-keyword-%d | - | NEXT -\n" % i
            for i in range(1000)]
    rows.append("- [2026-11-06 10:01] REJECTED | T01 | budget-keyword-negative | - | NEXT -\n")
    (root / "06_记忆" / "L0_LOG.md").write_text("".join(rows), encoding="utf-8")
    # zero display rows: the negative record is entirely outside the display window
    c = s.cli("check", "budget-keyword", "--strict", "--max-lines", "0", "--root", root)
    assert c.returncode == 1, (c.returncode, c.stdout, c.stderr)
    assert "rejected/blocked: 1" in c.stdout, c.stdout[:300]
    # with a tiny window, negative records must be prioritised into the window
    c2 = s.cli("check", "budget-keyword", "--strict", "--max-lines", "2", "--root", root)
    assert c2.returncode == 1
    assert "[!]" in c2.stdout, c2.stdout[:400]
    return {"strict_zero_window_rc": c.returncode, "strict_small_window_rc": c2.returncode}


# --------------------------------------------------------------------------
# R22..R23  deterministic lock contention
# --------------------------------------------------------------------------
def _hold_and_add(s, root, hold_seconds, timeout, stale="0"):
    lockpath = root / "06_记忆" / "L0_LOG.md.lock"
    ready = root / "lock.ready"
    out = root / "lock.out.json"
    holder = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
         "__worker__", "lockhold", str(s.script), str(lockpath), str(hold_seconds),
         str(ready), str(out), stale],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, encoding="utf-8")
    deadline = time.monotonic() + 10
    while not ready.exists() and time.monotonic() < deadline:
        time.sleep(0.01)
    assert ready.exists(), "lock holder did not acquire the lock"
    before = len(s.loglines(root))
    p = s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
              "--text", "contender", "--lock-timeout", str(timeout))
    after_timeout = len(s.loglines(root))
    holder.communicate(timeout=30)
    return p, before, after_timeout


def r22(s):
    # short timeout -> explicit failure, never a false success and no write
    root = s.init("r22a")
    p, before, during = _hold_and_add(s, root, hold_seconds=2.0, timeout=0.2)
    assert p.returncode == 2, (p.returncode, p.stdout, p.stderr)
    assert during == before, "a timed-out contender must not append"
    assert "锁" in p.stderr or "lock" in p.stderr.lower() or "超时" in p.stderr, p.stderr
    # long timeout -> waits, then succeeds, record present exactly once
    root2 = s.init("r22b")
    p2, before2, during2 = _hold_and_add(s, root2, hold_seconds=1.0, timeout=10.0)
    assert p2.returncode == 0, (p2.returncode, p2.stdout, p2.stderr)
    assert during2 == before2 + 1, (before2, during2)
    assert "contender" in "\n".join(s.loglines(root2))
    return {"timeout_rc": p.returncode, "wait_rc": p2.returncode}


def r23(s):
    root = s.init("r23")
    p, before, during = _hold_and_add(s, root, hold_seconds=2.0, timeout=0.2, stale="1")
    assert p.returncode == 2, "a stale mtime must not allow stealing a live lock"
    assert during == before
    return {"rc": p.returncode, "stale_lock_not_stolen": True}


# --------------------------------------------------------------------------
# R24  real multi-process concurrency: 4 processes x 20 records x N rounds
# --------------------------------------------------------------------------
def r24(s):
    rounds = []
    for rnd in range(s.rounds):
        root = s.init("r24")
        outs = [root / ("worker-%d.json" % i) for i in range(4)]
        procs = [
            subprocess.Popen(
                [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
                 "__worker__", "cliaddloop", str(s.script), str(root), "W%d" % i,
                 "20", str(outs[i])],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                encoding="utf-8")
            for i in range(4)
        ]
        worker_rcs = []
        for p in procs:
            _, err = p.communicate(timeout=300)
            worker_rcs.append({"rc": p.returncode, "stderr_tail": err.strip()[-200:]})
        payloads = [json.loads(o.read_text(encoding="utf-8")) for o in outs]
        inner_rcs = [x for pl in payloads for x in pl["rcs"]]
        rows = s.loglines(root)
        texts = [x.split(" | ")[2] for x in rows if len(x.split(" | ")) == 5]
        well_formed = all(LOG_LINE_RE.match(x) and len(x.split(" | ")) == 5 for x in rows)
        unique = set(texts)
        expected = {"W%d-%d" % (i, j) for i in range(4) for j in range(20)}
        ok = (
            all(x["rc"] == 0 for x in worker_rcs)
            and all(x == 0 for x in inner_rcs)
            and len(rows) == 80
            and well_formed
            and len(unique) == 80
            and unique == expected
        )
        rounds.append({
            "round": rnd + 1, "worker_rcs": worker_rcs,
            "cli_rcs_all_zero": all(x == 0 for x in inner_rcs),
            "cli_calls": len(inner_rcs), "log_lines": len(rows),
            "well_formed": well_formed, "unique_records": len(unique),
            "expected_records": 80, "ok": ok,
        })
        assert ok, rounds[-1]
    return {"rounds": s.rounds, "processes_per_round": 4,
            "cli_adds_per_process": 20, "all_rounds": rounds}


# --------------------------------------------------------------------------
def main():
    ap = argparse.ArgumentParser(description="project-protocol v1.0.2 regression suite")
    ap.add_argument("--script", required=True, help="path to the progress.py under test")
    ap.add_argument("--out", required=True, help="path for the JSON result")
    ap.add_argument("--rounds", type=int, default=10, help="concurrency rounds (default 10)")
    ap.add_argument("--workdir", default=None,
                    help="scratch directory for temp projects (default: fresh mkdtemp outside the repo)")
    args = ap.parse_args()

    auto_work = args.workdir is None
    workdir = args.workdir or tempfile.mkdtemp(prefix="pp-reg-container-")
    s = Suite(args.script, workdir, args.rounds, auto_work=auto_work)
    started = time.time()
    cases = [
        ("R01 empty dir init succeeds", r01),
        ("R02 non-empty old project rejected with zero writes", r02),
        ("R03 repeat init rejected byte-identical", r03),
        ("R04 pre-existing AGENTS.md rejected with zero writes", r04),
        ("R05 parent path conflict -> zero writes", r05),
        ("R06 injected mid-init failure -> accurate manifest + no AGENTS.md", r06),
        ("R07 add/tail roundtrip and 5-field format", r07),
        ("R08 newline and pipe field sanitisation", r08),
        ("R09 missing final newline -> independent records, negative found", r09),
        ("R10 current REJECTED strict detection", r10),
        ("R11 archived monthly BLOCKED strict detection", r11),
        ("R12 idea frontmatter REJECTED strict detection with status/reason/source", r12),
        ("R13 quoted REJECTED in body is not a status", r13),
        ("R14 non-existent root -> explicit error", r14),
        ("R15 old project without protocol -> explicit error", r15),
        ("R16 unreadable protocol file -> explicit error", r16),
        ("R17 artifact path validation and backslash normalisation", r17),
        ("R18 TID ASCII rules incl. T100", r18),
        ("R19 tail --lines 0 / negative / default", r19),
        ("R20 1000 common-keyword hits stay within output budget", r20),
        ("R21 --strict judges full result outside display window", r21),
        ("R22 deterministic lock contention: timeout vs wait", r22),
        ("R23 stale lock file is not stolen", r23),
        ("R24 real 4-process x 20-record concurrency, %d rounds" % args.rounds, r24),
    ]
    for name, fn in cases:
        s.case(name, fn)
    elapsed = round(time.time() - started, 2)
    passed = sum(1 for x in s.results if x["ok"])
    result = {
        "suite": "project-protocol v1.0.2 regression",
        "script_under_test": str(s.script),
        "script_sha256": __import__("hashlib").sha256(s.script.read_bytes()).hexdigest().upper(),
        "python": sys.version.split()[0],
        "platform_verified": platform.system() + " " + platform.release(),
        "platforms_not_verified": [x for x in ("Windows", "Linux", "Darwin")
                                   if x != platform.system()],
        "concurrency_rounds": args.rounds,
        "elapsed_seconds": elapsed,
        "cases": len(s.results), "passed": passed,
        "failed": len(s.results) - passed,
        "all_passed": passed == len(s.results),
        "results": s.results,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    s.cleanup()
    print("\nSUMMARY: %d/%d passed | %ss | %s" %
          (passed, len(s.results), elapsed, out), flush=True)
    sys.exit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "__worker__":
        worker_main(sys.argv[2:])
    else:
        main()

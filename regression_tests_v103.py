#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project-protocol v1.0.3 regression suite for the 7 audit findings.

Run:
  python regression_tests_v103.py --script <progress.py> --out <result.json> \
                                  --workdir <scratch-dir>

Pure standard library, offline. Every case operates on an isolated temporary
project created by this file. Each case asserts the CORRECT behaviour; no
assertion from the independent audit was weakened or deleted.

Note on I10 of the independent audit: its `partial_init("partial-agents",
"AGENTS.md")` requires BOTH `not (p / "AGENTS.md").exists()` AND
`(p / "AGENTS.md").read_text()` to succeed, which is impossible for the same
path. In this suite the equivalent requirement is expressed as V20/V21/V22/V23:
the completion entry must never be published on failure, the temp file and
its exact state must be reported, and add/tail/check must reject the project.
"""
import argparse
import contextlib
import hashlib
import importlib.util
import io
import json
import os
import platform
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path
from types import SimpleNamespace

sys.dont_write_bytecode = True

HERE = Path(__file__).resolve().parent
DETAIL_CHARS = 400


def _load(script):
    spec = importlib.util.spec_from_file_location("pp_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


def worker_lockhold(argv):
    """Hold the real lock for N seconds; parent kills this process."""
    script, lockpath, seconds, ready = argv
    mod = _load(script)
    with mod.CrossProcessLock(Path(lockpath), timeout=10.0):
        Path(ready).write_text("locked", encoding="utf-8")
        time.sleep(float(seconds))
    Path(ready + ".done").write_text("done", encoding="utf-8")


class Suite:
    def __init__(self, script, workdir, auto_work=False):
        self.script = Path(script).resolve()
        self.auto_work = auto_work
        self.mod = _load(self.script)
        self.work = Path(workdir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-v103-", dir=str(self.work)))
        self.counter = 0
        self.results = []
        self.docs = {
            name: (HERE / name).read_text(encoding="utf-8")
            for name in ("SKILL.md", "PROJECT_TEMPLATE.md")
        }
        self.docs["AGENTS.md (generated)"] = self.mod.COMPLETION_CONTENT

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
# helpers
# ---------------------------------------------------------------------------
def assert_no_clean(text):
    assert "STRICT: CLEAN" not in text, "parse failure must never degrade to CLEAN"
    assert "CLEAN" not in text, "unexpected CLEAN: " + text[:DETAIL_CHARS]


def idea(s, status, *, encoding="utf-8", close=True, padding=0,
         extra_header="", body="# kw-marker\n", keyword="kw-marker"):
    """Create an isolated project with one idea file and run check --strict."""
    p = s.init("idea")
    d = p / "01_任务" / "待定"
    d.mkdir(parents=True)
    head = "---\n" + "# header comment\n" * padding + extra_header
    if status is not None:
        head += "status: " + status + "\n"
    head += "reason: already disproven\n"
    if close:
        head += "---\n"
    (d / "20261106-kw.md").write_text(head + body, encoding=encoding)
    r = s.cli("check", keyword, "--strict", "--root", p)
    return p, r


def idea_error(s, label, *, status, encoding="utf-8", close=True,
               padding=0, extra_header="", body="# kw-marker\n"):
    p, r = idea(s, status, encoding=encoding, close=close, padding=padding,
                extra_header=extra_header, body=body)
    assert r.returncode == 2, (label, r.returncode, r.stdout, r.stderr)
    assert_no_clean(r.stdout + r.stderr)
    assert "ERROR" in r.stderr, (label, r.stderr[:DETAIL_CHARS])
    return {"label": label, "rc": r.returncode,
            "stderr": r.stderr[:DETAIL_CHARS]}


class Wrap:
    """Wrapper around a real file object to inject a failure at a given stage."""
    def __init__(self, inner, stage):
        self.inner = inner
        self.stage = stage

    def __enter__(self):
        self.inner.__enter__()
        return self

    def __exit__(self, *args):
        result = self.inner.__exit__(*args)
        if self.stage == "close":
            raise OSError("injected close-stage failure")
        return result

    def write(self, text):
        if self.stage == "write":
            self.inner.write(text[:32])
            self.inner.flush()
            raise OSError("injected partial-write failure")
        return self.inner.write(text)

    def flush(self):
        result = self.inner.flush()
        if self.stage == "flush":
            raise OSError("injected flush-stage failure")
        return result

    def fileno(self):
        return self.inner.fileno()


def injected_init(s, target_rel, stage):
    """Run cmd_init in-process with a deterministic failure injection.

    Returns (root, rc, stdout, stderr, files_on_disk).
    """
    mod = s.mod
    root = s.root("initfail")
    target = root / target_rel
    orig_open = Path.open

    def patched(self, mode="r", *args, **kwargs):
        f = orig_open(self, mode, *args, **kwargs)
        if self == target and mode == "x":
            return Wrap(f, stage)
        return f

    so, se = io.StringIO(), io.StringIO()
    rc = 0
    Path.open = patched
    replace_ctx = (contextlib.nullcontext() if stage != "replace" else
                   _broken_replace(mod))
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se), replace_ctx:
            try:
                mod.cmd_init(SimpleNamespace(directory=str(root)))
            except SystemExit as exc:
                rc = int(exc.code or 0)
            except Exception as exc:  # noqa: BLE001
                rc = 99
                se.write("UNCAUGHT: %r" % (exc,))
    finally:
        Path.open = orig_open
    files = sorted(x.relative_to(root).as_posix() for x in root.rglob("*")
                   if x.is_file())
    return root, rc, so.getvalue(), se.getvalue(), files


@contextlib.contextmanager
def _broken_replace(mod):
    """Inject a publish-stage failure at the current publish primitive.

    v1.0.4 replaced os.replace with an atomic no-clobber os.link, so the
    injection target moved with it. The case name, the stage label and every
    assertion in V22 are unchanged; no assertion was deleted or weakened.
    """
    original = mod.os.link

    def boom(*args, **kwargs):
        raise OSError("injected publish (os.link) failure")

    mod.os.link = boom
    try:
        yield
    finally:
        mod.os.link = original


def guard_commands(s, root):
    """add / tail / check must all reject an incomplete project."""
    out = {}
    out["add"] = s.cli("add", "--root", root, "--status", "DONE", "--tid", "T01",
                       "--text", "after-failed-init").returncode
    out["tail"] = s.cli("tail", "--root", root).returncode
    out["check"] = s.cli("check", "anything", "--strict", "--root", root).returncode
    assert all(v == 2 for v in out.values()), out
    return out


# ---------------------------------------------------------------------------
# Fix 1: one line-break definition shared by writer and reader
# ---------------------------------------------------------------------------
def v01(s):
    seps = ["\u0085", "\u2028", "\u2029", "\v", "\f", "\x1c", "\x1d", "\x1e",
            "\r\n", "\n", "\t"]
    details = []
    for i, sep in enumerate(seps):
        p = s.init("sep")
        r = s.cli("add", "--root", p, "--status", "REJECTED", "--tid", "T01",
                  "--text", "initial" + sep + "unicode-method",
                  "--path", "a" + sep + "b.md", "--next", "next" + sep + "tail")
        assert r.returncode == 0, (i, r.returncode, r.stderr)
        raw = (p / "06_记忆" / "L0_LOG.md").read_bytes()
        rows = [ln for ln in s.mod.split_log_lines(raw.decode("utf-8-sig"))
                if ln.startswith("- [")]
        assert len(rows) == 1, (i, rows)
        assert len(rows[0].split(" | ")) == 5, (i, rows[0])
        # str.splitlines() must agree: the reader's definition covers the writer's.
        assert len([ln for ln in raw.decode("utf-8-sig").splitlines()
                    if ln.startswith("- [")]) == 1, i
        c = s.cli("check", "unicode-method", "--strict", "--root", p)
        assert c.returncode == 1, (i, c.returncode, c.stdout, c.stderr)
        details.append("U+%04X" % ord(sep[0]))
    return {"separators_tested": details}


def v02(s):
    for ch in ("\u0085", "\u2028", "\u2029", "\v", "\f", "\x1c", "\x1d", "\x1e"):
        assert s.mod.LINE_BREAK_WRITE_RE.search(ch), repr(ch)
        assert s.mod.LINE_BREAK_SPLIT_RE.search(ch), repr(ch)
    assert s.mod.clean_field("a\u2028b", "x") == "a b"
    assert s.mod.clean_field("a\u0085b", "x") == "a b"
    assert s.mod.clean_field("a\u2029b", "x") == "a b"
    assert s.mod.one_line("a\u2028b") == "a b"
    return {"writer_reader_break_set_identical": True}


# ---------------------------------------------------------------------------
# Fix 2: idea frontmatter
# ---------------------------------------------------------------------------
def v03(s):
    out = []
    for status in ("REJECTED", '"REJECTED"', "'REJECTED'",
                   "REJECTED # already disproven"):
        p, r = idea(s, status)
        assert r.returncode == 1, (status, r.returncode, r.stdout, r.stderr)
        assert "status=REJECTED" in r.stdout, (status, r.stdout[:DETAIL_CHARS])
        assert "reason=already disproven" in r.stdout, (status, r.stdout[:DETAIL_CHARS])
        assert "source=frontmatter" in r.stdout, (status, r.stdout[:DETAIL_CHARS])
        out.append(status)
    return {"accepted_syntax": out}


def v04(s):
    return idea_error(s, "misspelled status", status="REJECTD")


def v05(s):
    return idea_error(s, "duplicate conflicting status", status="REJECTED",
                      extra_header="status: PROPOSED\n")


def v06(s):
    return idea_error(s, "missing closing delimiter", status="REJECTED", close=False)


def v07(s):
    p, r = idea(s, None, body="# kw-marker\nREJECTED is quoted text only.\n",
                close=False)
    # No frontmatter at all: replace the file with plain body text.
    (p / "01_任务" / "待定" / "20261106-kw.md").write_text(
        "# kw-marker\nREJECTED is quoted text only.\n", encoding="utf-8")
    r = s.cli("check", "kw-marker", "--strict", "--root", p)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert_no_clean(r.stdout + r.stderr)
    return {"rc": r.returncode, "stderr": r.stderr[:DETAIL_CHARS]}


def v08(s):
    return idea_error(s, "header over 64 lines", status="REJECTED", padding=70)


def v09(s):
    return idea_error(s, "unparseable header line", status="REJECTED",
                      extra_header="this is not a field\n")


def v10(s):
    p, r = idea(s, "REJECTED", padding=24)
    assert r.returncode == 1, (r.returncode, r.stdout, r.stderr)
    assert "status=REJECTED" in r.stdout
    return {"padding_lines": 24, "rc": r.returncode}


def v11(s):
    p, r = idea(s, "PROPOSED",
                body="# kw-marker\nREJECTED is quoted text only.\n")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "STRICT: CLEAN" in r.stdout
    return {"rc": r.returncode, "body_reference_not_a_status": True}


def v12(s):
    p, r = idea(s, "REJECTED", extra_header="reason: " + "x" * 260 + "\n")
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert_no_clean(r.stdout + r.stderr)
    return {"rc": r.returncode, "stderr": r.stderr[:DETAIL_CHARS]}


def v13(s):
    return idea_error(s, "unclosed quote", status='"REJECTED')


def v14(s):
    return idea_error(s, "junk after quoted status", status='"REJECTED" garbage')


# ---------------------------------------------------------------------------
# Fix 3: UTF-8 BOM
# ---------------------------------------------------------------------------
def v15(s):
    out = {}
    for label, enc in (("bom", "utf-8-sig"), ("plain", "utf-8")):
        p = s.init("bomlog")
        log = p / "06_记忆" / "L0_LOG.md"
        text = "- [2026-11-06 10:00] REJECTED | T01 | bom-method | - | NEXT -\n"
        before = text.encode(enc)
        log.write_bytes(before)
        r = s.cli("check", "bom-method", "--strict", "--root", p)
        assert r.returncode == 1, (label, r.returncode, r.stdout, r.stderr)
        assert log.read_bytes() == before, "historical body must not be rewritten"
        t = s.cli("tail", "--root", p)
        assert "bom-method" in t.stdout, (label, t.stdout[:DETAIL_CHARS])
        out[label] = r.returncode
    return out


def v16(s):
    out = {}
    for label, enc in (("bom", "utf-8-sig"), ("plain", "utf-8")):
        p, r = idea(s, "REJECTED", encoding=enc)
        assert r.returncode == 1, (label, r.returncode, r.stdout, r.stderr)
        assert "status=REJECTED" in r.stdout, (label, r.stdout[:DETAIL_CHARS])
        out[label] = r.returncode
    return out


# ---------------------------------------------------------------------------
# Fix 4: init partial-write recovery and completion publication
# ---------------------------------------------------------------------------
def v19(s):
    root, rc, so, se, files = injected_init(s, "00_定义/READER.md", "write")
    text = so + se
    assert rc == 2, (rc, text[:DETAIL_CHARS])
    assert "INIT FAILED" in text
    assert "\n  00_定义/READER.md\n" in se, "partial file missing from manifest"
    assert "已创建但未完整写入" in se
    assert "AGENTS.md" not in files, files
    assert "AGENTS.md.init.tmp" not in files, files
    assert "AGENTS.md.init.tmp" not in se, "no temp file should be claimed"
    guarded = guard_commands(s, root)
    return {"rc": rc, "files": files, "guards": guarded}


def _temp_failure_case(s, stage):
    root, rc, so, se, files = injected_init(s, "AGENTS.md.init.tmp", stage)
    text = so + se
    assert rc == 2, (stage, rc, text[:DETAIL_CHARS])
    assert "INIT FAILED" in text
    assert "AGENTS.md.init.tmp" in se, (stage, "temp file not reported")
    assert "\n  AGENTS.md.init.tmp\n" in se, (stage, "temp file not in manifest")
    assert "AGENTS.md" not in files, (stage, files)
    assert "AGENTS.md.init.tmp" in files, (stage, files)
    assert "未发布" in se, (stage, "completion state not reported accurately")
    guarded = guard_commands(s, root)
    tmp_content = (root / "AGENTS.md.init.tmp").read_text(encoding="utf-8-sig")
    return {"stage": stage, "rc": rc, "files": files, "guards": guarded,
            "temp_has_content": len(tmp_content) > 0,
            "temp_has_marker": s.mod.AGENTS_COMPLETION_MARKER in tmp_content}


def v20(s):
    d = _temp_failure_case(s, "write")
    assert not d["temp_has_marker"], "partial temp must not carry the marker"
    return d


def v21(s):
    d = _temp_failure_case(s, "flush")
    assert d["temp_has_marker"], "full content written but never published"
    return d


def v22(s):
    return {"close": _temp_failure_case(s, "close"),
            "replace": _temp_failure_case(s, "replace")}


def v23(s):
    p = s.init("truncated")
    (p / "AGENTS.md").write_text("# AGENTS.md\n\n> truncated\n", encoding="utf-8")
    guarded = guard_commands(s, p)
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "x")
    assert "初始化未完成" in r.stderr, r.stderr[:DETAIL_CHARS]
    return {"guards": guarded, "message": r.stderr[:DETAIL_CHARS]}


def v24(s):
    """A COMPLETE, REAL v1.0.2-era AGENTS.md (no new marker) remains usable.

    The independent v1.0.3 audit explicitly rejects proving legacy
    completeness with a three-line text carrying two substrings. The fixture
    here is the byte-exact entry produced by the v1.0.2 generator, extracted
    from project-protocol-skill-v1.0.2.zip. Assertions are unchanged; the
    standard is strictly higher.
    """
    p = s.init("legacy")
    legacy = (HERE / "fixtures" / "v102_AGENTS.md").read_bytes()
    assert hashlib.sha256(legacy).hexdigest().upper() == \
        s.mod.LEGACY_COMPLETE_SHA256["v1.0.2"], "v1.0.2 fixture fingerprint"
    (p / "AGENTS.md").write_bytes(legacy)
    r = s.cli("add", "--root", p, "--status", "DONE", "--tid", "T01",
              "--text", "legacy-handoff")
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    c = s.cli("check", "legacy-handoff", "--strict", "--root", p)
    assert c.returncode == 0, (c.returncode, c.stdout, c.stderr)
    return {"add_rc": r.returncode, "check_rc": c.returncode,
            "fixture_bytes": len(legacy),
            "fixture_sha256": hashlib.sha256(legacy).hexdigest().upper()}


# ---------------------------------------------------------------------------
# Fix 5: .git scope
# ---------------------------------------------------------------------------
def v25(s):
    p = s.init("git")
    g = p / ".git"
    g.mkdir()
    (g / "COMMIT_EDITMSG").write_text(
        "kw-marker\n- [2026-11-06 10:00] REJECTED | T01 | kw-marker | - | NEXT -\n",
        encoding="utf-8")
    r = s.cli("check", "kw-marker", "--strict", "--root", p)
    assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
    assert "STRICT: CLEAN" in r.stdout
    assert ".git" not in r.stdout, r.stdout[:DETAIL_CHARS]
    docs = s.docs
    for name, text in docs.items():
        assert ".git" in text, name
        assert ("git status" in text or "git" in text), name
        assert "仓库元数据" in text, name
        assert "不额外授权" in text or "既不禁止" in text, name
        for forbidden in ("禁止读写 .git", "禁止 git status", "不读写 .git",
                          ".git 是 agent 私有", "禁止 .git"):
            assert forbidden not in text, (name, forbidden)
    return {"check_rc": r.returncode, "docs_checked": sorted(docs)}


# ---------------------------------------------------------------------------
# Fix 6: init conditions separated from daily handoff
# ---------------------------------------------------------------------------
def v26(s):
    p = s.init("handoff")
    (p / "package.json").write_text("{}\n", encoding="utf-8")
    (p / ".gitignore").write_text("node_modules/\n", encoding="utf-8")
    (p / "src").mkdir()
    (p / "src" / "main.py").write_text("pass\n", encoding="utf-8")
    before = {x.relative_to(p).as_posix(): x.read_bytes()
              for x in sorted(p.rglob("*")) if x.is_file()}
    a = s.cli("add", "--root", p, "--status", "DONE", "--tid", "T01",
              "--text", "daily-handoff")
    assert a.returncode == 0, (a.returncode, a.stdout, a.stderr)
    t = s.cli("tail", "--root", p)
    assert "daily-handoff" in t.stdout
    c = s.cli("check", "daily-handoff", "--strict", "--root", p)
    assert c.returncode == 0, (c.returncode, c.stdout, c.stderr)
    i = s.cli("init", p)
    assert i.returncode == 2, (i.returncode, i.stdout, i.stderr)
    after = {x.relative_to(p).as_posix(): x.read_bytes()
             for x in sorted(p.rglob("*")) if x.is_file()}
    # only the L0 log and its lock carrier may have changed
    changed = {k for k in set(before) | set(after) if before.get(k) != after.get(k)}
    assert changed <= {"06_记忆/L0_LOG.md", "06_记忆/L0_LOG.md.lock"}, changed
    skill = s.docs["SKILL.md"]
    assert "init" in skill and "日常接手" in skill and "旧项目" in skill
    return {"handoff_rc": a.returncode, "reinit_rc": i.returncode,
            "changed_files": sorted(changed)}


# ---------------------------------------------------------------------------
# Fix 7: finite positive lock timeout + killed holder recovery
# ---------------------------------------------------------------------------
def v27(s):
    out = []
    for value in ("nan", "inf", "-inf", "NaN", "Infinity", "0", "-1", "abc"):
        p = s.init("timeout")
        lock = p / "06_记忆" / "L0_LOG.md.lock"
        assert not lock.exists()
        # Use the = form: argparse would otherwise treat "-inf" as an option.
        r = s.cli("add", "--root", p, "--status", "DONE", "--text", "x",
                  "--lock-timeout=" + value)
        assert r.returncode == 2, (value, r.returncode, r.stdout, r.stderr)
        assert "有限正数" in r.stderr, (value, r.stderr[:DETAIL_CHARS])
        assert not lock.exists(), (value, "lock file opened before validation")
        out.append(value)
    return {"rejected": out}


def v28(s):
    p = s.init("contend")
    lock = p / "06_记忆" / "L0_LOG.md.lock"
    with s.mod.CrossProcessLock(lock, timeout=5):
        started = time.monotonic()
        r = s.cli("add", "--root", p, "--status", "DONE", "--text", "contender",
                  "--lock-timeout", "0.2", timeout=8)
        elapsed = round(time.monotonic() - started, 2)
        assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
        assert "等待写锁超时" in r.stderr, r.stderr[:DETAIL_CHARS]
        assert elapsed < 5, elapsed
    ok = s.cli("add", "--root", p, "--status", "DONE", "--text", "after-release")
    assert ok.returncode == 0, (ok.returncode, ok.stdout, ok.stderr)
    return {"contender_rc": r.returncode, "bounded_seconds": elapsed,
            "after_release_rc": ok.returncode}


def v29(s):
    p = s.init("killed")
    lock = p / "06_记忆" / "L0_LOG.md.lock"
    ready = p / "holder.ready"
    holder = subprocess.Popen(
        [sys.executable, "-X", "utf8", str(Path(__file__).resolve()),
         "__worker__", "lockhold", str(s.script), str(lock), "20", str(ready)],
        stdout=subprocess.PIPE, stderr=subprocess.PIPE)
    try:
        deadline = time.monotonic() + 8
        while not ready.exists() and time.monotonic() < deadline:
            time.sleep(0.01)
        assert ready.exists(), "lock holder never became ready"
        holder.kill()
        holder.communicate(timeout=8)
        r = s.cli("add", "--root", p, "--status", "DONE", "--text",
                  "after-holder-crash", "--lock-timeout", "1", timeout=10)
        assert r.returncode == 0, (r.returncode, r.stdout, r.stderr)
        return {"holder_killed_rc": holder.returncode, "add_rc": r.returncode}
    finally:
        if holder.poll() is None:
            holder.kill()
            holder.communicate(timeout=8)


def v31(s):
    """Transient Windows sharing violation on lock-file OPEN is retried.

    A persistent failure must still be reported (never a fake success), and
    the write itself happens exactly once so no duplicate record can appear.
    """
    mod = s.mod
    out = {}

    def run(patched_open):
        p = s.init("transient")
        orig_open = Path.open
        so = io.StringIO()
        rc = 0
        Path.open = patched_open
        try:
            with contextlib.redirect_stdout(so):
                try:
                    mod.cmd_add(SimpleNamespace(
                        root=str(p), status="DONE", tid="T01",
                        text="transient-record", path="-", next="-",
                        lock_timeout=10.0))
                except SystemExit as exc:
                    rc = int(exc.code or 0)
        finally:
            Path.open = orig_open
        rows = [ln for ln in (p / "06_记忆" / "L0_LOG.md").read_text(
            encoding="utf-8-sig").splitlines() if ln.startswith("- [")]
        return p, rc, so.getvalue(), rows

    # 1) three transient failures, then success
    lock_seen = {"n": 0}
    orig_open = Path.open

    def flaky(self, mode="r", *a, **kw):
        if mode == "a+b" and self.name.endswith("L0_LOG.md.lock") \
                and lock_seen["n"] < 3:
            lock_seen["n"] += 1
            raise PermissionError(13, "simulated transient sharing violation")
        return orig_open(self, mode, *a, **kw)

    p, rc, stdout, rows = run(flaky)
    assert rc == 0, (rc, stdout)
    assert lock_seen["n"] == 3, lock_seen
    assert len(rows) == 1 and "transient-record" in rows[0], rows
    out["transient_retries"] = lock_seen["n"]
    out["records_after_retry"] = len(rows)

    # 2) persistent failure is reported, not faked
    def always_fail(self, mode="r", *a, **kw):
        if mode == "a+b" and self.name.endswith("L0_LOG.md.lock"):
            raise PermissionError(13, "persistent sharing violation")
        return orig_open(self, mode, *a, **kw)

    so2, se2 = io.StringIO(), io.StringIO()
    target = s.init("persistent")
    Path.open = always_fail
    rc2 = 0
    try:
        with contextlib.redirect_stdout(so2), contextlib.redirect_stderr(se2):
            try:
                mod.cmd_add(SimpleNamespace(
                    root=str(target), status="DONE", tid="T01",
                    text="must-not-appear", path="-", next="-",
                    lock_timeout=10.0))
            except SystemExit as exc:
                rc2 = int(exc.code or 0)
    finally:
        Path.open = orig_open
    rows2 = [ln for ln in (target / "06_记忆" / "L0_LOG.md").read_text(
        encoding="utf-8-sig").splitlines() if ln.startswith("- [")]
    assert rc2 == 2, (rc2, se2.getvalue())
    assert "写入 L0 失败" in se2.getvalue(), se2.getvalue()[:DETAIL_CHARS]
    assert rows2 == [], rows2
    out["persistent_rc"] = rc2
    out["records_after_persistent_failure"] = len(rows2)
    return out


def v30(s):
    """Documentation / CLI-help consistency for all four fixed surfaces."""
    install = (HERE / "INSTALL.md").read_text(encoding="utf-8")
    for needle in ("v1.0.3", "project-protocol-skill-v1.0.3.zip",
                   "regression_tests.py", "regression_tests_v103.py",
                   "未验证", "--lock-timeout"):
        assert needle in install, ("INSTALL.md", needle)
    helps = {
        "main": s.cli("--help").stdout,   # subcommand summary lines live here
        "init": s.cli("init", "--help").stdout,
        "add": s.cli("add", "--help").stdout,
        "tail": s.cli("tail", "--help").stdout,
        "check": s.cli("check", "--help").stdout,
    }
    assert "完成标识" in helps["main"], helps["main"]
    assert "一次 add 只产生一条完整记录" in helps["main"], helps["main"]
    assert "完成标识" in helps["init"], helps["init"]
    assert "有限正数" in helps["add"] and "nan" in helps["add"], helps["add"]
    assert "BOM" in helps["tail"], helps["tail"]
    assert "BOM" in helps["check"] and "frontmatter" in helps["check"], helps["check"]
    assert "0 正常无否决 / 1 命中否决 / 2" in helps["check"], helps["check"]
    docs = dict(s.docs)
    docs["INSTALL.md"] = install
    docs["CLI help"] = "\n".join(helps.values())
    for name, text in docs.items():
        assert "v1.0.3" in text or name == "CLI help", (name, "version")
        if name != "INSTALL.md" and name != "CLI help":
            assert "64 行" in text, (name, "frontmatter header limit")
            assert "BOM" in text, (name, "UTF-8 BOM")
            assert "完成标识" in text or "完成入口" in text, (name, "completion marker")
    return {"docs_checked": sorted(docs),
            "help_sections": sorted(helps)}


CASES = [
    ("V01 all Unicode/ASCII line breaks keep one complete record", v01),
    ("V02 writer and reader share the same line-break definition", v02),
    ("V03 four documented REJECTED status spellings are detected", v03),
    ("V04 misspelled idea status is an explicit error, not CLEAN", v04),
    ("V05 duplicate conflicting status is an explicit error", v05),
    ("V06 missing frontmatter closing delimiter is an explicit error", v06),
    ("V07 missing frontmatter entirely is an explicit error", v07),
    ("V08 over-long frontmatter header is an explicit error", v08),
    ("V09 unparseable frontmatter line is an explicit error", v09),
    ("V10 long valid frontmatter header still detects REJECTED", v10),
    ("V11 body-only REJECTED reference is not a status", v11),
    ("V12 over-long reason is a parse error, not a process-level crash", v12),
    ("V13 unclosed quoted status is an explicit error", v13),
    ("V14 junk after quoted status is an explicit error", v14),
    ("V15 BOM / plain L0 log both detect REJECTED; body untouched", v15),
    ("V16 BOM / plain idea frontmatter both detect REJECTED", v16),
    ("V19 partial normal file write reported; no AGENTS.md", v19),
    ("V20 partial completion temp write never publishes AGENTS.md", v20),
    ("V21 flush-stage failure leaves full temp but no AGENTS.md", v21),
    ("V22 close-stage and publish-stage failures never publish AGENTS.md", v22),
    ("V23 truncated AGENTS.md rejected by add/tail/check", v23),
    ("V24 complete legacy AGENTS.md remains usable", v24),
    ("V25 .git is skipped by memory scan, docs keep Git operations free", v25),
    ("V26 non-empty adopted project can be handed over without re-init", v26),
    ("V27 --lock-timeout rejects nan/inf/-inf/0/negative/junk", v27),
    ("V28 finite timeout fails loudly, normal lock still waits/succeeds", v28),
    ("V29 killed lock holder releases the OS lock", v29),
    ("V30 SKILL/模板/内嵌 AGENTS/INSTALL/CLI help 一致性", v30),
    ("V31 transient lock-open sharing violation retried, persistent fails loudly", v31),
]


def main():
    ap = argparse.ArgumentParser(description="project-protocol v1.0.3 7-fix regression")
    ap.add_argument("--script", default=str(HERE / "progress.py"))
    ap.add_argument("--out", required=True)
    ap.add_argument("--workdir", default=None,
                    help="scratch directory (default: fresh mkdtemp outside the repo)")
    args = ap.parse_args()
    auto_work = args.workdir is None
    workdir = args.workdir or tempfile.mkdtemp(prefix="pp-v103-container-")
    started = time.time()
    s = Suite(args.script, workdir, auto_work=auto_work)
    for name, fn in CASES:
        s.case(name, fn)
    passed = sum(1 for x in s.results if x["ok"])
    result = {
        "suite": "project-protocol v1.0.3 7-fix regression",
        "script_under_test": str(s.script),
        "script_sha256": hashlib.sha256(s.script.read_bytes()).hexdigest(),
        "python": sys.version.split()[0],
        "platform_verified": platform.system() + " " + platform.release(),
        "platforms_not_verified": [x for x in ("Windows", "Linux", "Darwin")
                                   if x != platform.system()],
        "elapsed_seconds": round(time.time() - started, 2),
        "cases": len(s.results), "passed": passed,
        "failed": len(s.results) - passed,
        "all_passed": passed == len(s.results),
        "results": s.results,
    }
    out = Path(args.out).resolve()
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n",
                   encoding="utf-8")
    s.cleanup()
    print("\nSUMMARY: %d/%d passed | %ss | %s" %
          (passed, len(s.results), result["elapsed_seconds"], out), flush=True)
    sys.exit(0 if result["all_passed"] else 1)


if __name__ == "__main__":
    if len(sys.argv) > 1 and sys.argv[1] == "__worker__":
        worker_lockhold(sys.argv[3:])  # skip "__worker__" and the mode name
    else:
        main()

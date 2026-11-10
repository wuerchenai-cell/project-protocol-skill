#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""project-protocol v1.0.4 regression suite for the two init findings (B01/B02).

Run:
  python regression_tests_v104.py --script <progress.py> --out <result.json> \
                                  --workdir <scratch-dir>

Pure standard library, offline. Every case runs on an isolated temporary
project. No assertion of the earlier 53-case suites was deleted or weakened.

Scope (only the two independent-audit findings):
  P1 / B02 - publishing AGENTS.md must NEVER clobber a competing file created
             after preflight; failing filesystems must error, not downgrade.
  P2 / B01 - completion detection must select rules by explicit version
             identity; a truncated current entry must never pass the legacy
             heuristic, while genuinely complete v1.0.2 / v1.0.3 entries stay
             usable.
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
    spec = importlib.util.spec_from_file_location("pp104_under_test", script)
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)
    return mod


class Suite:
    def __init__(self, script, workdir):
        self.script = Path(script).resolve()
        self.mod = _load(self.script)
        self.work = Path(workdir).resolve()
        self.work.mkdir(parents=True, exist_ok=True)
        self.tmp = Path(tempfile.mkdtemp(prefix="pp-v104-", dir=str(self.work)))
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


# ---------------------------------------------------------------------------
# helpers
# ---------------------------------------------------------------------------
def guard(s, p, keyword="probe-token"):
    """add / tail / check on a project whose AGENTS.md we replaced."""
    return {
        "add": s.cli("add", "--root", p, "--status", "DONE", "--tid", "T01",
                     "--text", keyword).returncode,
        "tail": s.cli("tail", "--root", p).returncode,
        "check": s.cli("check", keyword, "--strict", "--root", p).returncode,
    }


def assert_rejected(rcs):
    assert all(v == 2 for v in rcs.values()), rcs


def assert_usable(rcs):
    assert all(v == 0 for v in rcs.values()), rcs


def write_agents(p, raw):
    (p / "AGENTS.md").write_bytes(raw)
    return raw


def current_text(s, p):
    return (p / "AGENTS.md").read_text(encoding="utf-8")


def capture_init(s, p, patch=None):
    """Run cmd_init in-process so a deterministic publish-time patch works."""
    original = None
    if patch is not None:
        original = s.mod.os.link
        s.mod.os.link = patch
    so, se, rc = io.StringIO(), io.StringIO(), 0
    try:
        with contextlib.redirect_stdout(so), contextlib.redirect_stderr(se):
            try:
                s.mod.cmd_init(SimpleNamespace(directory=str(p)))
            except SystemExit as exc:
                rc = int(exc.code or 0)
            except Exception as exc:  # noqa: BLE001
                rc = 99
                se.write("UNCAUGHT: %r" % (exc,))
    finally:
        if patch is not None:
            s.mod.os.link = original
    return {"rc": rc, "stdout": so.getvalue(), "stderr": se.getvalue()}


# ---------------------------------------------------------------------------
# P1 / B02 : atomic NO-CLOBBER publish
# ---------------------------------------------------------------------------
def w01(s):
    """Normal init still publishes a complete entry with no temp residue."""
    p = s.init("normal")
    raw = (p / "AGENTS.md").read_bytes()
    assert raw == s.mod.COMPLETION_CONTENT.encode("utf-8"), "published bytes differ"
    assert s.mod.AGENTS_COMPLETION_MARKER.encode("utf-8") in raw
    assert not (p / s.mod.INIT_TEMP_REL).exists(), "temp file left behind"
    assert_usable(guard(s, p))
    return {"bytes": len(raw), "marker": s.mod.AGENTS_COMPLETION_MARKER}


def w02(s):
    """B02: a competing writer creates AGENTS.md immediately BEFORE the real
    publish action. init must fail, keep every byte, and report accurately."""
    p = s.root("publish-conflict")
    destination = p / "AGENTS.md"
    rival = (b"# Existing user AGENTS\n\n"
             b"Keep this rule; created by another writer during init.\n")
    rival_sha = hashlib.sha256(rival).hexdigest().upper()
    original_link = s.mod.os.link
    seen = {}

    def link_with_rival(src, dst):
        assert Path(dst) == destination, (src, dst)
        # Deterministic scheduling only: create a REAL competing file, then let
        # the reviewed implementation call the REAL os.link. No faked return.
        with destination.open("xb") as f:
            f.write(rival)
        seen["before_sha256"] = hashlib.sha256(
            destination.read_bytes()).hexdigest().upper()
        seen["real_link_called"] = True
        return original_link(src, dst)

    out = capture_init(s, p, patch=link_with_rival)
    after = destination.read_bytes()
    stderr = out["stderr"]
    rcs = guard(s, p, "conflict-token")

    assert seen.get("real_link_called"), "real publish action never reached"
    assert out["rc"] == 2, (out["rc"], stderr[:DETAIL_CHARS])
    assert after == rival, "competing bytes were modified"
    assert hashlib.sha256(after).hexdigest().upper() == seen["before_sha256"]
    assert hashlib.sha256(after).hexdigest().upper() == rival_sha
    assert "发布冲突" in stderr
    assert "不属于本次 init 产物" in stderr, stderr[:DETAIL_CHARS]
    assert "未覆盖" in stderr
    assert "\n  AGENTS.md.init.tmp\n" in stderr, "temp file missing from manifest"
    assert "\n  AGENTS.md\n" not in stderr, "rival file wrongly listed as our product"
    assert "恢复方法" in stderr
    assert (p / s.mod.INIT_TEMP_REL).exists(), "temp file must be kept for review"
    assert_rejected(rcs)
    return {"init_rc": out["rc"], "rival_bytes": len(after),
            "rival_sha256_before": seen["before_sha256"],
            "rival_sha256_after": hashlib.sha256(after).hexdigest().upper(),
            "guards": rcs, "stderr": stderr[:DETAIL_CHARS]}


def w03(s):
    """Filesystem without hardlink support still publishes via O_EXCL | O_CREAT;
    the fallback is atomic and never clobbers (no os.replace anywhere)."""
    p = s.root("unsupported")
    replace_calls = []

    def unsupported(src, dst):
        raise OSError(errno.EOPNOTSUPP, "hard links not supported here")

    def boom_replace(*args, **kwargs):
        replace_calls.append("replace")
        raise OSError("os.replace must never be used in publish path")

    original_replace = s.mod.os.replace
    try:
        s.mod.os.replace = boom_replace
        out = capture_init(s, p, patch=unsupported)
    finally:
        s.mod.os.replace = original_replace
    assert out["rc"] == 0, (out["rc"], out["stderr"][:DETAIL_CHARS])
    assert not replace_calls, "publish path called os.replace"
    assert (p / "AGENTS.md").exists(), "O_EXCL fallback did not publish AGENTS.md"
    agents = (p / "AGENTS.md").read_bytes()
    assert s.mod.AGENTS_COMPLETION_MARKER.encode("utf-8") in agents
    assert not (p / s.mod.INIT_TEMP_REL).exists(), "temp residue after successful fallback"
    assert_usable(guard(s, p))
    return {"init_rc": out["rc"], "replace_calls": len(replace_calls)}


def w3b(s):
    """When BOTH os.link and O_EXCL | O_CREAT are unsupported, init must fail
    loudly; it never degrades to an os.replace that could clobber a writer."""
    p = s.root("unsupported-both")
    original_link = s.mod.os.link
    original_open = s.mod.os.open

    def unsupported_link(src, dst):
        raise OSError(errno.EOPNOTSUPP, "hard links not supported here")

    def unsupported_open(path, flags, mode=0o777):
        raise OSError(errno.EOPNOTSUPP, "O_EXCL open not supported either")

    try:
        s.mod.os.open = unsupported_open
        out = capture_init(s, p, patch=unsupported_link)
    finally:
        s.mod.os.link = original_link
        s.mod.os.open = original_open
    assert out["rc"] == 2, (out["rc"], out["stderr"][:DETAIL_CHARS])
    assert "不支持原子无覆盖发布" in out["stderr"]
    assert "不会降级" in out["stderr"]
    assert not (p / "AGENTS.md").exists(), "AGENTS.md published despite failure"
    assert (p / s.mod.INIT_TEMP_REL).exists(), "temp file not reported"
    assert "\n  AGENTS.md.init.tmp\n" in out["stderr"]
    assert_rejected(guard(s, p, "unsupported-both-token"))
    return {"init_rc": out["rc"], "stderr": out["stderr"][:DETAIL_CHARS]}


def w04(s):
    """The temp file is COMPLETE (full body + marker) before publish runs."""
    p = s.root("temp-complete")
    original = s.mod.os.link
    observed = {}

    def peek(src, dst):
        observed["bytes"] = Path(src).read_bytes()
        return original(src, dst)

    out = capture_init(s, p, patch=peek)
    assert out["rc"] == 0, (out["rc"], out["stderr"][:DETAIL_CHARS])
    assert observed.get("bytes") == s.mod.COMPLETION_CONTENT.encode("utf-8")
    assert s.mod.AGENTS_COMPLETION_MARKER.encode("utf-8") in observed["bytes"]
    assert (p / "AGENTS.md").read_bytes() == observed["bytes"]
    assert not (p / s.mod.INIT_TEMP_REL).exists()
    assert_usable(guard(s, p))
    return {"temp_bytes": len(observed["bytes"])}


def w07(s):
    """No os.replace fallback exists anywhere in the publish path."""
    p = s.root("no-replace")
    original = s.mod.os.replace
    calls = []

    def boom(*args, **kwargs):
        calls.append(1)
        raise OSError("os.replace must never be used by init")

    s.mod.os.replace = boom
    try:
        out = capture_init(s, p)
    finally:
        s.mod.os.replace = original
    assert out["rc"] == 0, (out["rc"], out["stderr"][:DETAIL_CHARS])
    assert not calls, "init tried a possibly-clobbering os.replace"
    assert (p / "AGENTS.md").exists()
    return {"init_rc": out["rc"], "replace_calls": len(calls)}


# ---------------------------------------------------------------------------
# P2 / B01 : version-specific completion detection
# ---------------------------------------------------------------------------
def w08(s):
    """1. A complete current entry passes add / tail / check."""
    p = s.init("complete-current")
    assert s.mod.CURRENT_AGENTS_ID in current_text(s, p)
    assert_usable(guard(s, p))
    return {"marker": s.mod.AGENTS_COMPLETION_MARKER}


def w09(s):
    """2. Removing the completion marker from a current entry is rejected,
    and must NOT fall back to the legacy heuristic."""
    p = s.init("no-marker")
    full = current_text(s, p)
    stripped = full.replace(s.mod.AGENTS_COMPLETION_MARKER + "\n", "")
    assert stripped != full, "marker not found in generated entry"
    write_agents(p, stripped.encode("utf-8"))
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "no-marker-token")
    assert r.returncode == 2, (r.returncode, r.stderr[:DETAIL_CHARS])
    assert "缺少完成标识" in r.stderr
    assert "不会回落" in r.stderr
    assert_rejected(guard(s, p, "no-marker-token"))
    return {"stderr": r.stderr[:DETAIL_CHARS]}


def w10(s):
    """3. A REAL current entry truncated in the middle of its last rule is
    rejected, even though both legacy substrings survive in the prefix."""
    p = s.init("truncate-last-rule")
    full = current_text(s, p)
    cut = full.index(s.mod.LEGACY_AGENTS_TAIL) + len(s.mod.LEGACY_AGENTS_TAIL)
    prefix = full[:cut]
    assert s.mod.CURRENT_AGENTS_ID in prefix, "identity lost too early"
    assert s.mod.LEGACY_AGENTS_MARKER in prefix
    assert s.mod.LEGACY_AGENTS_TAIL in prefix
    assert s.mod.AGENTS_COMPLETION_MARKER not in prefix
    write_agents(p, prefix.encode("utf-8"))
    assert_rejected(guard(s, p, "truncated-last-token"))
    return {"full_chars": len(full), "kept_chars": len(prefix),
            "legacy_heading_present": True, "legacy_tail_present": True}


def w11(s):
    """4. A current entry truncated much earlier is rejected."""
    p = s.init("truncate-early")
    full = current_text(s, p)
    prefix = full[:800]
    assert s.mod.AGENTS_COMPLETION_MARKER not in prefix
    write_agents(p, prefix.encode("utf-8"))
    assert_rejected(guard(s, p, "truncated-early-token"))
    return {"full_chars": len(full), "kept_chars": len(prefix)}


def w12(s):
    """5. The REAL complete v1.0.2 entry remains usable (fingerprint + full
    structure), not a three-line text with two substrings."""
    p = s.init("legacy-v102-complete")
    raw = (FIXTURES / "v102_AGENTS.md").read_bytes()
    assert len(raw) == 4439
    assert hashlib.sha256(raw).hexdigest().upper() == \
        s.mod.LEGACY_COMPLETE_SHA256["v1.0.2"]
    write_agents(p, raw)
    assert_usable(guard(s, p, "legacy-v102-token"))
    return {"fixture_bytes": len(raw),
            "fixture_sha256": hashlib.sha256(raw).hexdigest().upper()}


def w13(s):
    """6. A truncated v1.0.2 entry is rejected."""
    p = s.init("legacy-v102-truncated")
    raw = (FIXTURES / "v102_AGENTS.md").read_bytes()
    write_agents(p, raw[:-60])
    assert_rejected(guard(s, p, "legacy-v102-bad-token"))
    return {"fixture_bytes": len(raw), "kept_bytes": len(raw) - 60}


def w14(s):
    """B01 reproduced with the audit's own cut size: keep the real prefix,
    drop the last 98 characters. The legacy substrings remain but must not
    grant access."""
    p = s.init("b01-repro")
    full = current_text(s, p)
    prefix = full[:-98]
    assert s.mod.CURRENT_AGENTS_ID in prefix
    assert s.mod.LEGACY_AGENTS_MARKER in prefix
    assert s.mod.LEGACY_AGENTS_TAIL in prefix
    assert s.mod.AGENTS_COMPLETION_MARKER not in prefix
    write_agents(p, prefix.encode("utf-8"))
    assert_rejected(guard(s, p, "b01-token"))
    return {"full_chars": len(full), "kept_chars": len(prefix),
            "truncated_chars": 98}


def w15(s):
    """A body-bound marker detects tampering even when the marker stays."""
    p = s.init("tampered-body")
    full = current_text(s, p)
    tampered = full.replace("## 项目进度", "## 项目进度（改）", 1)
    assert tampered != full
    write_agents(p, tampered.encode("utf-8"))
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "tamper-token")
    assert r.returncode == 2, (r.returncode, r.stderr[:DETAIL_CHARS])
    assert "摘要不一致" in r.stderr
    assert_rejected(guard(s, p, "tamper-token"))
    return {"stderr": r.stderr[:DETAIL_CHARS]}


def w16(s):
    """A forged well-formed marker with a wrong digest is rejected."""
    p = s.init("forged-marker")
    full = current_text(s, p)
    body = full.replace(s.mod.AGENTS_COMPLETION_MARKER + "\n", "")
    forged = (s.mod.CURRENT_MARKER_PREFIX + "0" * 64 + s.mod.CURRENT_MARKER_SUFFIX)
    write_agents(p, (body + forged + "\n").encode("utf-8"))
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "forge-token")
    assert r.returncode == 2, (r.returncode, r.stderr[:DETAIL_CHARS])
    assert_rejected(guard(s, p, "forge-token"))
    return {"stderr": r.stderr[:DETAIL_CHARS]}


def w17(s):
    """A genuine complete v1.0.3 entry also remains usable (backward compat)."""
    p = s.init("legacy-v103-complete")
    raw = (FIXTURES / "v103_AGENTS.md").read_bytes()
    assert hashlib.sha256(raw).hexdigest().upper() == \
        s.mod.LEGACY_COMPLETE_SHA256["v1.0.3"]
    write_agents(p, raw)
    assert_usable(guard(s, p, "legacy-v103-token"))
    return {"fixture_bytes": len(raw),
            "fixture_sha256": hashlib.sha256(raw).hexdigest().upper()}


def w18(s):
    """A truncated v1.0.3 entry is rejected."""
    p = s.init("legacy-v103-truncated")
    raw = (FIXTURES / "v103_AGENTS.md").read_bytes()
    write_agents(p, raw[:-80])
    assert_rejected(guard(s, p, "legacy-v103-bad-token"))
    return {"kept_bytes": len(raw) - 80}


def w19(s):
    """Content appended AFTER the completion marker is rejected."""
    p = s.init("extra-after-marker")
    full = current_text(s, p)
    write_agents(p, (full + "\n# appended\n").encode("utf-8"))
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "append-token")
    assert r.returncode == 2, (r.returncode, r.stderr[:DETAIL_CHARS])
    assert "完成标识之后仍有内容" in r.stderr
    assert_rejected(guard(s, p, "append-token"))
    return {"stderr": r.stderr[:DETAIL_CHARS]}


def w20(s):
    """A leading UTF-8 BOM on a current entry is tolerated without rewrite."""
    p = s.init("bom-current")
    raw = (p / "AGENTS.md").read_bytes()
    bom = b"\xef\xbb\xbf" + raw
    write_agents(p, bom)
    assert_usable(guard(s, p, "bom-current-token"))
    assert (p / "AGENTS.md").read_bytes() == bom, "history body was rewritten"
    return {"bytes": len(bom)}


def w21(s):
    """The audit-rejected three-line 'two substrings' fake legacy text no
    longer proves completeness."""
    p = s.init("three-line-fake")
    fake = ("# AGENTS.md\n\n## 根目录（代码项目兼容）\n"
            "- 管理目录：00_定义/ ...\n"
            "- 根目录白名单约束的是新增的管理文档与交付物。\n")
    assert s.mod.LEGACY_AGENTS_MARKER in fake
    assert s.mod.LEGACY_AGENTS_TAIL in fake
    write_agents(p, fake.encode("utf-8"))
    assert_rejected(guard(s, p, "fake-legacy-token"))
    return {"fake_bytes": len(fake.encode("utf-8"))}


def w22(s):
    """A marker whose digest is not 64 hex digits is a hard error."""
    p = s.init("bad-digest")
    full = current_text(s, p)
    body = full.replace(s.mod.AGENTS_COMPLETION_MARKER + "\n", "")
    bad = s.mod.CURRENT_MARKER_PREFIX + "not-a-digest" + s.mod.CURRENT_MARKER_SUFFIX
    write_agents(p, (body + bad + "\n").encode("utf-8"))
    r = s.cli("add", "--root", p, "--status", "DONE", "--text", "bad-digest-token")
    assert r.returncode == 2, (r.returncode, r.stderr[:DETAIL_CHARS])
    assert "格式不合法" in r.stderr
    assert_rejected(guard(s, p, "bad-digest-token"))
    return {"stderr": r.stderr[:DETAIL_CHARS]}


def w23(s):
    """Rejections are explicit errors, never a silent CLEAN."""
    p = s.init("no-clean")
    full = current_text(s, p)
    write_agents(p, full[:-98].encode("utf-8"))
    r = s.cli("check", "no-clean-token", "--strict", "--root", p)
    assert r.returncode == 2, (r.returncode, r.stdout, r.stderr)
    assert "STRICT: CLEAN" not in r.stdout
    assert "CLEAN" not in r.stdout
    return {"check_rc": r.returncode, "stdout": r.stdout[:200]}


CASES = [
    ("W01 normal init publishes complete entry, no temp residue", w01),
    ("W02 B02 competing AGENTS.md survives publish byte-for-byte", w02),
    ("W03 hardlink unsupported -> O_EXCL fallback publishes (no replace)", w03),
    ("W3b hardlink AND O_EXCL both unsupported -> explicit error (no downgrade)", w3b),
    ("W04 temp file is complete before the publish action", w04),
    ("W07 init never uses an os.replace fallback", w07),
    ("W08 complete current entry is usable", w08),
    ("W09 current entry without completion marker is rejected", w09),
    ("W10 current entry truncated in last rule is rejected", w10),
    ("W11 current entry truncated earlier is rejected", w11),
    ("W12 real complete v1.0.2 entry is usable", w12),
    ("W13 truncated v1.0.2 entry is rejected", w13),
    ("W14 B01 audit cut (last 98 chars removed) is rejected", w14),
    ("W15 tampered body with intact marker is rejected", w15),
    ("W16 forged marker with wrong digest is rejected", w16),
    ("W17 real complete v1.0.3 entry is usable", w17),
    ("W18 truncated v1.0.3 entry is rejected", w18),
    ("W19 content after completion marker is rejected", w19),
    ("W20 BOM-prefixed current entry is usable, body untouched", w20),
    ("W21 three-line two-substring fake legacy is rejected", w21),
    ("W22 malformed marker digest is a hard error", w22),
    ("W23 rejection is an explicit error, never CLEAN", w23),
]


def main():
    ap = argparse.ArgumentParser(
        description="project-protocol v1.0.4 B01/B02 regression")
    ap.add_argument("--script", default=str(HERE / "progress.py"))
    ap.add_argument("--out", default=str(HERE / "regression-v104-result.json"))
    ap.add_argument("--workdir", default=str(HERE / "test-scratch-v104"))
    args = ap.parse_args()
    s = Suite(args.script, args.workdir)
    started = __import__("time").monotonic()
    for name, fn in CASES:
        s.case(name, fn)
    passed = sum(r["ok"] for r in s.results)
    report = {
        "suite": "project-protocol v1.0.4 B01/B02 regression",
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

"""Truth table for the deploy-memory lane and its supersession filter.

Offline by construction — the Boswell search is a stub, so this runs anywhere,
including on an installer's machine with no key.

The fixtures are the real 2026-08-07 failure: a `git push staging` against a
server decommissioned two months earlier, with the retired "STAGING FIRST"
protocol ranking above the record that retired it.
"""
import json
import os
import pathlib
import sys
import tempfile

sys.path.insert(0, str(pathlib.Path(__file__).resolve().parent.parent / "scripts"))

import deploy_memory as dm       # noqa: E402
import supersession as sp        # noqa: E402


# --- fixtures: the real records, trimmed ------------------------------------
STALE = {"commit_hash": "caa1e1667a62aaaa", "created_at": "2026-06-12 05:28:47",
         "message": "SACRED PROTOCOL: staging first, then push production",
         "content": '{"deploy_protocol": ["2. STAGING FIRST: git push staging main"]}'}
RETIRES = {"commit_hash": "617d04647d63bbbb", "created_at": "2026-06-13 16:24:03",
           "message": "SACRED PROTOCOL (SUPERSEDES caa1e166): ff-pull is canonical",
           "content": '{"supersedes": "caa1e166 (RETIRED)"}'}
DEAD = {"commit_hash": "d0d0efe19db4cccc", "created_at": "2026-06-17 19:08:37",
        "message": "Staging (tintwoodstock.com) is DECOMMISSIONED",
        "content": '{"fact": "Shell access is not enabled on your account"}'}
# Superseded by a record that shares NO vocabulary with a staging query — the
# case the set-local filter provably cannot catch.
ORPHAN = {"commit_hash": "cb058dcf2f12dddd", "created_at": "2026-08-07 05:38:42",
          "message": "CORRECTION: deploy is push production main", "content": "{}"}
LATE = {"commit_hash": "e159cd6ccc77eeee", "created_at": "2026-08-07 05:42:40",
        "message": "CORRECTION (supersedes my own cb058dcf, 40 min old): ff-pull",
        "content": "{}"}


def _repo(tmpdir, remote, url):
    """A throwaway checkout carrying one remote."""
    root = pathlib.Path(tmpdir) / "tintatlanta-website"
    (root / ".git").mkdir(parents=True, exist_ok=True)
    (root / ".git" / "config").write_text(
        '[core]\n\trepositoryformatversion = 0\n'
        '[remote "%s"]\n\turl = %s\n\tfetch = +refs/heads/*\n' % (remote, url),
        encoding="utf-8")
    return str(root)


def run_checks():
    results = []
    tmpdir = tempfile.mkdtemp()

    def ok(cond, msg):
        results.append(bool(cond))
        print(("PASS " if cond else "FAIL ") + msg)

    # --- what counts as a push -------------------------------------------
    fires = lambda c: dm._remote_name(c) is not None          # noqa: E731
    ok(dm._remote_name("git push staging main") == "staging", "plain push -> remote")
    ok(dm._remote_name("git push --force production main") == "production",
       "flags skipped, remote found")
    ok(not fires("python - <<'PY'\ngit push staging main\nPY"),
       "heredoc body is data, not a push  (caught live on first backtest)")
    ok(not fires("echo 'git push staging main' > n.txt"), "quoted mention is not a push")
    # Caught live a SECOND time, on the commit that shipped this lane: a heredoc
    # with a QUOTED delimiter whose body mentioned a push, and the real push
    # after it. Quote-blanking used to erase <<'EOF' before heredoc detection.
    ok(dm._remote_name(
        'git commit -m "$(cat <<\'EOF\'\nran: git push staging main\nEOF\n)"'
        ' && git push -q origin main') == "origin",
       "quoted-delimiter heredoc: body ignored, the REAL push after it is found")
    ok(not fires("git push"), "implicit remote -> no target to look up")
    ok(not fires("git status"), "unrelated command quiet")

    # --- remote -> host ---------------------------------------------------
    root = _repo(tmpdir, "staging", "ssh://tintwoodstock.com/home1/tintwood/public_html")
    gd = dm._git_dir(os.path.join(root, "crm", "api"))
    ok(gd is not None and gd.parent.name == "tintatlanta-website",
       "walks up to the repo root")
    ok(dm._remote_host(gd, "staging") == "tintwoodstock.com", "ssh:// url -> host")
    ok(dm._remote_host(_repo(tmpdir, "p", "tintwood@1.2.3.4:2222/x") and
                       dm._git_dir(_repo(tmpdir, "p", "tintwood@1.2.3.4:2222/x")), "p")
       == "1.2.3.4", "user@host:port url -> host")
    ok("tintwoodstock.com" in dm._query("tintatlanta-website", "staging", "tintwoodstock.com"),
       "host reaches the query (a bare 'staging' retrieves nothing)")

    # --- supersession -----------------------------------------------------
    kept, withheld = sp.filter_rows([STALE, RETIRES, DEAD])
    ok([r["commit_hash"] for r in kept] == [RETIRES["commit_hash"], DEAD["commit_hash"]],
       "retired protocol withheld, the record that retires it kept")
    ok(len(withheld) == 1 and withheld[0][1]["commit_hash"] == RETIRES["commit_hash"],
       "withheld row names its superseder")
    ok(sp.filter_rows([STALE])[0] == [STALE], "a lone row is never withheld")
    ok(sp.filter_rows([RETIRES, STALE])[0][0]["commit_hash"] == RETIRES["commit_hash"],
       "order preserved")
    # an OLDER record may not retire a newer one
    backwards = dict(STALE, message="supersedes " + DEAD["commit_hash"][:8])
    ok(DEAD in sp.filter_rows([backwards, DEAD])[0], "older claim cannot retire a newer row")

    ok(list(sp.claims([LATE]).keys()) == ["cb058dcf"],
       "prose claim parsed: 'supersedes my own cb058dcf'")
    ok(sp.claims([{"message": "supersedes the earlier plan", "content": ""}]) == {},
       "prose with no hash supersedes nothing")

    # second pass: superseder outside the set
    calls = []

    def fake_search(query, limit=None, timeout=None):
        calls.append(query)
        return {"results": [LATE] if "cb058dcf" in query else []}

    kept2, late2 = sp.verify_current([ORPHAN, DEAD], fake_search)
    ok([r["commit_hash"] for r in kept2] == [DEAD["commit_hash"]],
       "second pass drops a row whose superseder was never retrieved")
    ok(all(len(c.split()) == 2 for c in calls),
       "one lookup per hash, not one diluted combined query")
    ok(len(late2) == 1 and "cb058dcf" in sp.note(late2), "audit line names the drop")
    ok(sp.note(late2 + late2).count("cb058dcf") == 1, "audit line deduplicated")

    # --- end to end, stubbed search ---------------------------------------
    def lane_search(query, limit=None, timeout=None):
        if "supersedes" in query:
            return {"results": [LATE] if "cb058dcf" in query else []}
        return {"results": [STALE, RETIRES, DEAD, ORPHAN]}

    import boswell_client
    original = boswell_client.search
    boswell_client.search = lane_search
    try:
        out = dm.evaluate({"tool_name": "Bash",
                           "tool_input": {"command": "git push staging main"},
                           "cwd": root})
    finally:
        boswell_client.search = original

    ok(out is not None, "lane injects on a real push")
    text = out["hookSpecificOutput"]["additionalContext"]
    ok(out["hookSpecificOutput"]["hookEventName"] == "PreToolUse"
       and "permissionDecision" not in json.dumps(out),
       "injects context and NEVER denies")
    rows = json.loads(text[text.index("["):])
    commits = [r["commit"] for r in rows]
    ok(not any(c.startswith("caa1e166") for c in commits), "stale protocol not injected")
    ok(not any(c.startswith("cb058dcf") for c in commits), "orphan-superseded not injected")
    ok(any(c.startswith("d0d0efe1") for c in commits), "the decommission notice IS injected")
    ok(rows == sorted(rows, key=lambda r: r["recorded"], reverse=True), "newest first")
    ok("tintwoodstock.com" in text and "STOP" in text, "header names the host and says stop")

    print("\n%d/%d passed" % (sum(results), len(results)))
    return results


def test_deploy_memory():
    results = run_checks()
    assert results, "no checks ran"
    assert all(results), "%d of %d deploy-lane checks failed" % (
        len(results) - sum(results), len(results))


if __name__ == "__main__":
    sys.exit(0 if all(run_checks()) else 1)

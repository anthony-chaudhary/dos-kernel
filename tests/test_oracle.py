"""docs/342 M3 (P-CHECK) — the binding rung is un-authored, end to end through `verify`.

Two properties, each a refusal a witness the agent did not author confirms:

  (2) THE P-CHECK WITNESS — where the host declares an exec/acceptance command
      (`dos.toml [verify] exec_cmd`), an `--allow-empty`/forged-subject right-SHA commit
      that CLEARS grep-subject is NOT accepted: the OS exit code (captured by the kernel,
      un-authored by the agent) disagrees with the forged stamp, so `verify` reports
      NOT_SHIPPED. (`TestExecRungAtVerifyBoundary`.)

  (3) THE NO-EXEC BINDING DEFAULT — where NO exec command is declared, the file-path
      (artefact) rung is PREFERRED over grep-subject: with `prefer_artifact_rung` on, a
      ship resting only on the forgeable subject is demoted, while a file-path ship
      passes (docs/114 §A3 disposition). (`TestPreferArtifactOverSubject`.)

The pure folds live in `oracle.py` (kernel, no driver import — the EvidenceFacts is
gathered at the `cmd_verify` boundary and handed in, the `git_delta`/non-git-rung rule);
the boundary tests resolve the driver BY NAME on frozen data (the fake source never
spawns). Both knobs default OFF → byte-identical to today (the gate-OFF convention) — so
the existing ~4,900-test suite is untouched until a host opts in. refuse-MORE only: each
fold can only DEMOTE a forgeable ship, never promote `shipped=False → True`.
"""

from __future__ import annotations

import argparse

from dos import cli, oracle
from dos.evidence import Accountability, EvidenceFacts
from dos.oracle import ShipVerdict, _prefer_artifact_over_subject


# ---------------------------------------------------------------------------
# (3) The no-exec binding default — file-path PREFERRED over grep-subject (pure fold).
# ---------------------------------------------------------------------------


class TestPreferArtifactOverSubject:
    """`_prefer_artifact_over_subject` — the file-path rung is the binding default."""

    def test_subject_only_ship_is_demoted_when_enabled(self):
        """A ship resting only on the forgeable grep-SUBJECT → DEMOTED (not binding)."""
        subj = ShipVerdict("RS", "RS1", shipped=True, source="grep-subject", sha="abc",
                           rung="direct")
        out = _prefer_artifact_over_subject(subj, enabled=True)
        assert out.shipped is False              # the subject alone is not a binding ship
        assert "DEMOTED" in out.summary
        assert "artefact" in out.summary

    def test_file_path_ship_is_preferred_and_passes(self):
        """A file-path (artefact) ship is PREFERRED — it passes unchanged when enabled.

        This is the 'preferred over grep-subject' property: the non-forgeable artefact
        rung survives the same gate that demotes the forgeable subject rung."""
        art = ShipVerdict("RS", "RS1", shipped=True, source="grep-artifact", sha="abc",
                          rung="file-path")
        out = _prefer_artifact_over_subject(art, enabled=True)
        assert out == art                        # artefact rung preferred — untouched
        assert out.shipped is True

    def test_subject_vs_artifact_same_gate_opposite_outcome(self):
        """Side-by-side: ONE gate, the subject ship falls, the artefact ship stands —
        the 'file-path preferred over grep-subject' witness in a single assertion."""
        subj = ShipVerdict("RS", "RS1", shipped=True, source="grep-subject", sha="a")
        art = ShipVerdict("RS", "RS2", shipped=True, source="grep-artifact", sha="b")
        assert _prefer_artifact_over_subject(subj, enabled=True).shipped is False
        assert _prefer_artifact_over_subject(art, enabled=True).shipped is True

    def test_disabled_is_byte_identical(self):
        """Default OFF → identity → byte-identical (the gate-OFF convention)."""
        subj = ShipVerdict("RS", "RS1", shipped=True, source="grep-subject", sha="abc")
        assert _prefer_artifact_over_subject(subj, enabled=False) == subj

    def test_registry_and_other_rungs_untouched(self):
        """Only the bare forgeable `grep-subject` is demoted; every stronger rung passes.

        registry / ci-green / exec-attested are all non-subject-only — they are not the
        forgery this default guards, so they survive unchanged even when enabled."""
        for src in ("registry", "grep-artifact", "ci-green", "exec-attested", "grep"):
            v = ShipVerdict("RS", "RS1", shipped=True, source=src, sha="abc")
            assert _prefer_artifact_over_subject(v, enabled=True) == v, src

    def test_unshipped_is_identity(self):
        """A non-shipped verdict is never promoted (refuse-MORE only)."""
        v = ShipVerdict("RS", "RS1", shipped=False, source="none")
        assert _prefer_artifact_over_subject(v, enabled=True) == v


# ---------------------------------------------------------------------------
# (2) The exec rung at the `cmd_verify` boundary — resolved BY NAME, frozen data.
# (Mirrors test_verify_non_git_rung.py's harness exactly.)
# ---------------------------------------------------------------------------


def _verify_ns(**kw):
    base = dict(workspace=".", plan="DOCS", phase="M3", json=True, no_ci=False,
                output=None, explain=False)
    base.update(kw)
    return argparse.Namespace(**base)


def _arm_config(monkeypatch, *, exec_cmd: str = "", prefer_artifact_rung: bool = False,
                non_git_oracle: str = ""):
    """Install an active `SubstrateConfig` with the M3 binding-rung wiring set.

    `SubstrateConfig` is FROZEN, so the fields are set with `dataclasses.replace` and
    `set_active`d (restored by monkeypatch.undo) — the honest shape `load_workspace_config`
    would produce from a `[verify]` table."""
    import dataclasses

    import dos.config as _config

    cfg = dataclasses.replace(
        _config.active(), exec_cmd=exec_cmd, prefer_artifact_rung=prefer_artifact_rung,
        non_git_oracle=non_git_oracle)
    monkeypatch.setattr(_config, "active", lambda: cfg)
    return cfg


class _FakeOsSource:
    """A stand-in `os_acceptance` source: returns a fixed-stance OS_RECORDED fact, never
    spawning a process. The boundary resolves it by name via `_load_witness_driver`."""

    def __init__(self, stance: str):
        self._stance = stance
        self.calls: list[str] = []

    def OsAcceptanceEvidenceSource(self, *, cwd=None):  # noqa: N802 — mimics the module attr
        outer = self

        class _Src:
            name = "os_acceptance"
            accountability = Accountability.OS_RECORDED

            def gather(self, subject, config):
                outer.calls.append(subject)
                if outer._stance == "REFUTED":
                    return EvidenceFacts.refute("os_acceptance", Accountability.OS_RECORDED,
                                                subject, detail="exit 1 — OS-recorded")
                if outer._stance == "ATTESTED":
                    return EvidenceFacts.attest("os_acceptance", Accountability.OS_RECORDED,
                                                subject, detail="exit 0 — passed")
                return EvidenceFacts.no_signal("os_acceptance", Accountability.OS_RECORDED,
                                               subject, detail="no signal")

        return _Src()


def _stub_shipped(monkeypatch, source: str, sha: str = "abc123"):
    monkeypatch.setattr(
        oracle, "is_shipped",
        lambda plan, phase, **kw: ShipVerdict(
            plan=plan, phase=phase, shipped=True, source=source, sha=sha))


def test_forged_subject_is_not_shipped_when_exec_rung_refutes(capsys, monkeypatch):
    """THE P-CHECK WITNESS: a forged-subject right-SHA ship + a host exec rung that
    exits non-zero → `verify` reports NOT_SHIPPED (rc 1), `source='exec-refuted'`.

    The git rung clears on the (forgeable) subject the agent typed; the host's declared
    exec command exits 1; the OS exit code — un-authored by the agent — DISAGREES, and
    the binding rung reverses the ship. This is the un-authored completion check the
    `--allow-empty` forgery cannot clear (docs/342 M3 / docs/335 §3.2)."""
    fake = _FakeOsSource("REFUTED")
    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", lambda name: fake)
    _arm_config(monkeypatch, exec_cmd="pytest -q")
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 1                                  # NOT_SHIPPED — the forgery is caught
    assert '"shipped": false' in out
    assert '"source": "exec-refuted"' in out
    assert fake.calls == ["pytest -q"]             # the host's command WAS run


def test_passing_exec_rung_mints_exec_attested(capsys, monkeypatch):
    """A host exec rung that exits 0 keeps the ship and mints the un-authored source."""
    fake = _FakeOsSource("ATTESTED")
    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", lambda name: fake)
    _arm_config(monkeypatch, exec_cmd="pytest -q")
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 0                                  # SHIPPED — the exec rung agrees
    assert '"source": "exec-attested"' in out


def test_no_exec_command_skips_the_rung(capsys, monkeypatch):
    """No `[verify] exec_cmd` → the os_acceptance driver is never even resolved.

    The byte-identical-when-unconfigured contract at the boundary — the kernel never
    fabricates a command (the no-exec default is the file-path rung, not an invented
    exec)."""
    def _boom(name):
        raise AssertionError("the exec driver must not be resolved when no exec_cmd is set")

    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", _boom)
    _arm_config(monkeypatch, exec_cmd="")          # no command, no artefact-preference
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert '"source": "grep-subject"' in out        # the git verdict, untouched


def test_no_ci_flag_skips_the_exec_rung(capsys, monkeypatch):
    """`--no-ci` skips the exec rung even when wired (the fast-path opt-out)."""
    def _boom(name):
        raise AssertionError("--no-ci must skip the exec driver entirely")

    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", _boom)
    _arm_config(monkeypatch, exec_cmd="pytest -q")
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns(no_ci=True))
    assert rc == 0
    assert '"source": "grep-subject"' in capsys.readouterr().out


def test_unshipped_never_runs_the_exec_rung(capsys, monkeypatch):
    """A git `shipped=False` verdict never runs the exec command (the §1 invariant).

    There is no commit for the exec rung to bind on, so the boundary short-circuits
    before any (even wired) driver is reached — the binding rung can only DEMOTE a ship,
    never manufacture one."""
    def _boom(name):
        raise AssertionError("an unshipped git verdict must not run the exec rung")

    monkeypatch.setattr(
        oracle, "is_shipped",
        lambda plan, phase, **kw: ShipVerdict(plan, phase, shipped=False, source="none"))
    monkeypatch.setattr(cli, "_load_witness_driver", _boom)
    _arm_config(monkeypatch, exec_cmd="pytest -q")
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    assert rc == 1
    assert '"source": "none"' in capsys.readouterr().out


def test_prefer_artifact_demotes_subject_at_boundary_when_no_exec(capsys, monkeypatch):
    """(3) at the boundary: with `prefer_artifact_rung` on and NO exec command, a
    grep-subject ship is demoted to NOT_SHIPPED — the file-path rung is the binding
    default. The exec rung is NOT consulted (no command declared)."""
    def _boom(name):
        raise AssertionError("no exec driver should be resolved in the no-exec default path")

    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", _boom)
    _arm_config(monkeypatch, exec_cmd="", prefer_artifact_rung=True)
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 1                                  # subject-only ship is not binding
    assert '"shipped": false' in out


def test_prefer_artifact_keeps_file_path_ship_at_boundary(capsys, monkeypatch):
    """The companion: a file-path ship survives the no-exec artefact-preferred default."""
    _stub_shipped(monkeypatch, "grep-artifact")
    monkeypatch.setattr(cli, "_load_witness_driver",
                        lambda name: (_ for _ in ()).throw(AssertionError("no exec here")))
    _arm_config(monkeypatch, exec_cmd="", prefer_artifact_rung=True)
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 0                                  # artefact rung preferred — stands
    assert '"source": "grep-artifact"' in out


def test_exec_rung_takes_precedence_over_artifact_preference(capsys, monkeypatch):
    """When BOTH knobs are set, the exec rung (the stronger un-authored binding) wins.

    A grep-subject ship with an exec command declared is bound on the OS exit code, not
    demoted by the artefact-preference default — the precedence the wiring documents."""
    fake = _FakeOsSource("ATTESTED")
    _stub_shipped(monkeypatch, "grep-subject")
    monkeypatch.setattr(cli, "_load_witness_driver", lambda name: fake)
    _arm_config(monkeypatch, exec_cmd="pytest -q", prefer_artifact_rung=True)
    monkeypatch.setattr(cli, "_apply_workspace", lambda args: None)

    rc = cli.cmd_verify(_verify_ns())
    out = capsys.readouterr().out
    assert rc == 0
    assert '"source": "exec-attested"' in out        # exec rung bound it, not demoted
    assert fake.calls == ["pytest -q"]


# ---------------------------------------------------------------------------
# The host declaration — `dos.toml [verify] exec_cmd` / `prefer_artifact_rung`.
# The binding rung is HOST-SUPPLIED policy; default OFF → byte-identical.
# ---------------------------------------------------------------------------


def _write_toml(repo, body: str) -> None:
    (repo / "dos.toml").write_text(body, encoding="utf-8")


class TestConfigDeclaresTheBindingRung:
    """`[verify] exec_cmd` / `prefer_artifact_rung` reach `SubstrateConfig`."""

    def test_default_is_off(self, tmp_path):
        import dos.config as _config

        cfg = _config.load_workspace_config(tmp_path)
        assert cfg.exec_cmd == ""                 # no exec rung unless the host declares one
        assert cfg.prefer_artifact_rung is False  # the no-exec default is OFF unless opted in

    def test_reads_exec_cmd_and_prefer_artifact(self, tmp_path):
        import dos.config as _config

        _write_toml(tmp_path,
                    '[verify]\nexec_cmd = "pytest -q"\nprefer_artifact_rung = true\n')
        cfg = _config.load_workspace_config(tmp_path)
        assert cfg.exec_cmd == "pytest -q"
        assert cfg.prefer_artifact_rung is True

    def test_malformed_exec_cmd_warns_keeps_base(self, tmp_path):
        """A non-string exec_cmd warns + keeps base — never crashes config load, and
        never turns the binding rung ON off a malformed table (the safe direction)."""
        import dos.config as _config

        _write_toml(tmp_path, "[verify]\nexec_cmd = 123\n")
        warnings = []
        cfg = _config.load_workspace_config(
            tmp_path, warn=lambda label, msg: warnings.append((label, msg)))
        assert cfg.exec_cmd == ""                 # base kept — gate stays off
        assert any(label == "verify" for label, _ in warnings)

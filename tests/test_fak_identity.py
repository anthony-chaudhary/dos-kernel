from pathlib import Path

from dos.fak_identity import transcript_uuid_for_trace


def test_transcript_uuid_for_trace_uses_latest_valid_mapping(tmp_path: Path) -> None:
    reg = tmp_path / "registry"
    reg.mkdir()
    (reg / "session_identity.jsonl").write_text(
        '{"trace":"RID-4116","uuid":"uuid-old"}\n'
        'torn garbage\n'
        '{"trace":"other","uuid":"uuid-other"}\n'
        '{"trace":"RID-4116","uuid":"uuid-new"}\n',
        encoding="utf-8",
    )
    assert transcript_uuid_for_trace("RID-4116", workspace=tmp_path, env={"FLEET_REG_DIR": str(reg)}) == "uuid-new"
    assert transcript_uuid_for_trace("missing", workspace=tmp_path, env={"FLEET_REG_DIR": str(reg)}) == ""

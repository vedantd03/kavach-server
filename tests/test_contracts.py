import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest
from pydantic import ValidationError

from detect_core.contracts import (
    Chunk, DetectRequest, chunk_id_for, classify_folder, file_type_for, finding_id_for,
)

ROOT = Path(__file__).resolve().parents[1]


def test_schema_applies():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "server" / "schema.sql").read_text(encoding="utf-8"))
    tables = {r[0] for r in con.execute("select name from sqlite_master where type='table'")}
    assert {"devices", "findings", "traces", "actions", "audit", "scans", "commands"} <= tables


def test_schema_has_no_text_columns():
    con = sqlite3.connect(":memory:")
    con.executescript((ROOT / "server" / "schema.sql").read_text(encoding="utf-8"))
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        cols = {r[1] for r in con.execute(f"pragma table_info({table})")}
        assert not cols & {"text", "snippet", "value", "raw_value", "chunk_text"}


def test_contracts_import_without_genai():
    code = ("import sys, pkgutil, importlib, detect_core; "
            "sys.modules['google']=None; sys.modules['google.genai']=None; "
            "[importlib.import_module('detect_core.'+m.name) for m in pkgutil.iter_modules(detect_core.__path__)]")
    r = subprocess.run([sys.executable, "-c", code], capture_output=True, text=True,
                       cwd=ROOT / "detect_core")
    assert r.returncode == 0, r.stderr


def test_extra_fields_rejected():
    with pytest.raises(ValidationError):
        DetectRequest(device_id="d", files=[], chunks=[], surprise=1)


def test_file_type_for():
    assert file_type_for("/a/b/Report.PDF") == "pdf"
    assert file_type_for("/a/b/scan.tif") == "tiff"
    assert file_type_for("/a/b/.env") == "env"
    assert file_type_for(r"C:\x\config_backup.env") == "env"


@pytest.mark.parametrize("path,cls", [
    ("/Users/demo/OneDrive - Acme/Documents/a.txt", "synced"),
    ("//fileserver/team/a.txt", "shared"),
    ("/Volumes/USB/a.txt", "shared"),
    ("/Users/demo/Downloads/a.csv", "downloads"),
    (r"C:\Users\demo\Desktop\a.png", "desktop"),
    ("/Users/demo/Documents/a.txt", "documents"),
    ("/tmp/a.txt", "other"),
])
def test_classify_folder(path, cls):
    assert classify_folder(path) == cls


def test_ids():
    assert chunk_id_for("9f2c4b1a0e7d3c55aa", 0) == "9f2c4b1a0e7d3c55:0"
    fid = finding_id_for("LAPTOP-01", "h", "item", "AADHAAR", "v")
    assert fid.startswith("fnd_") and len(fid) == 28
    assert fid == finding_id_for("LAPTOP-01", "h", "item", "AADHAAR", "v")
    Chunk(chunk_id="x:0", file_path="p", file_hash="h", file_type="txt", folder_class="other", text="t")

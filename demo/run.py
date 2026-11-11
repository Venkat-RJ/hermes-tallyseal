"""Reproduce the baseline and TallySeal handoff in a disposable Hermes profile."""
import csv
import hashlib
import json
import os
import shlex
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
FIXTURES = ROOT / "demo"
DISPATCH = """
from hermes_cli.plugins import discover_plugins, get_plugin_manager
from model_tools import get_tool_definitions, handle_function_call
discover_plugins()
names = {item["function"]["name"] for item in get_tool_definitions(
    enabled_toolsets=["tallyseal"], quiet_mode=True, skip_tool_search_assembly=True
)}
if "tallyseal_check" not in names:
    import json
    print(json.dumps({"status": "tool_unavailable", "plugin": [
        entry for entry in get_plugin_manager().list_plugins() if entry["name"] == "tallyseal"
    ], "names": sorted(names)}))
else:
    print(handle_function_call("tallyseal_check", {}, task_id="tallyseal-demo"))
"""


def sha(data):
    return hashlib.sha256(data).hexdigest()


def run(argv, env, expected=0):
    print("$ " + shlex.join(map(str, argv)), flush=True)
    proc = subprocess.run(argv, env=env, text=True, stdout=subprocess.PIPE, check=False)
    print(proc.stdout.strip(), flush=True)
    if proc.returncode != expected:
        raise RuntimeError(f"Expected exit {expected}, got {proc.returncode}")
    return json.loads(proc.stdout)


def total(path, key):
    with path.open(newline="") as handle:
        return sum(int(row[key]) for row in csv.DictReader(handle))


def main():
    hermes = shutil.which("hermes")
    if hermes is None:
        raise SystemExit("Put the Hermes installation's bin directory on PATH.")
    with tempfile.TemporaryDirectory(prefix="tallyseal-demo-") as workspace:
        base = Path(workspace)
        home = base / "hermes"
        job = base / "job"
        home.mkdir()
        job.mkdir()
        source = job / "source.csv"
        report = job / "report.csv"
        released = base / "released.csv"
        shutil.copyfile(FIXTURES / "source.csv", source)
        shutil.copyfile(FIXTURES / "balanced-but-wrong.csv", report)
        env = {**os.environ, "HERMES_HOME": str(home), "PYTHONDONTWRITEBYTECODE": "1"}
        dispatch = [sys.executable, "-c", DISPATCH]
        print("\n1. Baseline: grand-total check accepts the wrong report.", flush=True)
        before = total(source, "amount_minor")
        after = total(report, "total_minor")
        print(f"source={before} report={after} grand_total_matches={before == after}", flush=True)
        assert before == after == 20_000
        assert run(dispatch, env)["status"] == "tool_unavailable"

        plugin = home / "plugins" / "tallyseal"
        plugin.mkdir(parents=True)
        for name in ("plugin.yaml", "__init__.py", "reconcile.py"):
            shutil.copyfile(ROOT / name, plugin / name)
        (home / "config.yaml").write_text(json.dumps({
            "plugins": {
                "enabled": ["tallyseal"],
                "entries": {"tallyseal": {"settings": {
                    "root": str(job),
                    "source_sha256": sha(source.read_bytes()),
                    "group_by": ["team"],
                    "amount_column": "amount_minor",
                }}},
            }
        }), encoding="utf-8")

        print("\n2. Hermes dispatch returns two mismatches. Seal refuses the draft.", flush=True)
        fail = run(dispatch, env)
        assert fail["status"] == "fail" and fail["grand_total_matches"]
        assert [i["report_record"] for i in fail["issues"]] == [2, 3]
        seal_cmd = [hermes, "tallyseal", "seal", "--out", str(released)]
        assert run(seal_cmd, env, expected=2)["status"] == "fail"
        assert not released.exists()

        print("\n3. Fix the two rows. Hermes reports a pass.", flush=True)
        shutil.copyfile(FIXTURES / "correct.csv", report)
        passed = run(dispatch, env)
        assert passed["status"] == "pass"

        print("\n4. Replace the draft after that pass. Seal checks again and refuses it.", flush=True)
        shutil.copyfile(FIXTURES / "balanced-but-wrong.csv", report)
        assert run(seal_cmd, env, expected=2)["status"] == "fail"
        assert not released.exists()

        print("\n5. Restore the correction. Seal writes only the checked bytes.", flush=True)
        shutil.copyfile(FIXTURES / "correct.csv", report)
        sealed = run(seal_cmd, env)
        assert sealed["status"] == "pass"
        assert sealed["report_sha256"] == passed["report_sha256"] == sha(released.read_bytes())
        assert released.read_bytes() == (FIXTURES / "correct.csv").read_bytes()
        print("PASS: sealed bytes match the checked SHA-256; no invalid copy was released.")


if __name__ == "__main__":
    main()

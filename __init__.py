"""Hermes registration for TallySeal."""
import json
from pathlib import Path

from .reconcile import CheckError, check, seal


def register(ctx):
    def run():
        return check(
            ctx.get_config("root"),
            ctx.get_config("source_sha256"),
            ctx.get_config("group_by"),
            ctx.get_config("amount_column"),
        )

    def tool(args, **kwargs):
        try:
            result, _ = run()
        except CheckError as exc:
            result = {"status": "blocked", "reason": str(exc)}
        return json.dumps(result, separators=(",", ":"))

    def cli(args):
        try:
            result, snapshot = run()
            if args.action == "seal" and result["status"] == "pass":
                seal(snapshot, args.out)
                result["sealed_to"] = str(args.out)
        except CheckError as exc:
            result = {"status": "blocked", "reason": str(exc)}
        print(json.dumps(result, indent=2))
        if result["status"] != "pass":
            raise SystemExit(2)

    def setup(parser):
        sub = parser.add_subparsers(dest="action", required=True)
        sub.add_parser("check", help="Compare the current report against the pinned source")
        release = sub.add_parser("seal", help="Recheck and write a new validated copy")
        release.add_argument("--out", type=Path, required=True, help="New local output CSV")

    ctx.register_tool(
        name="tallyseal_check",
        toolset="tallyseal",
        schema={
            "name": "tallyseal_check",
            "description": (
                "Check the configured local report.csv against pinned source.csv. "
                "Reports mismatches by CSV record number without returning cell values. "
                "Does not create or publish any files."
            ),
            "parameters": {"type": "object", "properties": {}, "additionalProperties": False},
        },
        handler=tool,
    )
    ctx.register_cli_command(
        name="tallyseal",
        help="Verify and seal a local CSV summary",
        setup_fn=setup,
        handler_fn=cli,
    )

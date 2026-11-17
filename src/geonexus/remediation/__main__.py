"""Run with python -m geonexus.remediation; all inputs are local files."""

import argparse
import json
from pathlib import Path

from .toolkit import diagnose, draft_binding, validate_samples, write_bundle


def main(argv=None):
    parser = argparse.ArgumentParser(prog="geonode-repair")
    subs = parser.add_subparsers(dest="command", required=True)
    inspect = subs.add_parser("inspect")
    inspect.add_argument("snapshot", type=Path)
    inspect.add_argument("--assessment", type=Path)
    inspect.add_argument("--output", type=Path, required=True)
    for name in ("draft", "check"):
        sub = subs.add_parser(name)
        sub.add_argument("source", type=Path)
        if name == "draft":
            sub.add_argument("--tool", required=True)
            sub.add_argument("--output", type=Path, required=True)
        sub.add_argument("--input-sample", type=Path, required=True)
        sub.add_argument("--output-sample", type=Path, required=True)
    args = parser.parse_args(argv)

    def load(path):
        return json.loads(path.read_text(encoding="utf-8"))

    try:
        if args.command == "inspect":
            result = diagnose(
                load(args.snapshot), load(args.assessment) if args.assessment else None
            )
            write_bundle(result, args.output)
            print(
                json.dumps(
                    {
                        "resources": result["resources"],
                        "findings": len(result["findings"]),
                        "output": str(args.output),
                    }
                )
            )
        elif args.command == "draft":
            result = draft_binding(
                load(args.source), args.tool, load(args.input_sample), load(args.output_sample)
            )
            # Exclusive creation: cannot overwrite an existing source or proposal.
            with args.output.open("x", encoding="utf-8") as stream:
                json.dump(result, stream, ensure_ascii=False, indent=2)
            print(json.dumps({"status": result["status"], "changes": len(result["changes"])}))
        else:
            data = load(args.source)
            result = validate_samples(
                data.get("candidate_binding", data),
                load(args.input_sample),
                load(args.output_sample),
            )
            print(json.dumps(result, ensure_ascii=False, indent=2))
            return 0 if result["all_samples_valid"] else 1
        return 0
    except (ValueError, OSError, TypeError, KeyError, AttributeError):
        parser.exit(
            2,
            "Invalid input, stale assessment, unsupported draft or existing output. No remote action performed.\n",
        )


if __name__ == "__main__":
    raise SystemExit(main())

"""GeoNexus command line interface.

Commands::

    geonexus version
    geonexus card validate FILE
    geonexus card inspect FILE
    geonexus node start [--host H] [--port P] [--name N] [--bare]
    geonexus skill list [--url URL]
    geonexus demo amazon-ndvi [--output DIR] [--port N] [--no-server]
    geonexus init PROJECT
"""

from __future__ import annotations

import argparse
import logging
import os
import sys
from pathlib import Path
from typing import Any

from . import __version__

logger = logging.getLogger("geonexus.cli")


def _find_examples_dir() -> Path | None:
    """Locate the bundled examples directory.

    Priority: ``GEONEXUS_EXAMPLES_DIR`` env var > repo layout relative to the
    installed package > current working directory.
    """
    import os

    env = os.environ.get("GEONEXUS_EXAMPLES_DIR")
    if env:
        candidate = Path(env)
        if (candidate / "amazon_ndvi" / "geocard.yaml").exists():
            return candidate
    pkg = Path(__file__).resolve()
    candidate = pkg.parent.parent.parent / "examples"
    if (candidate / "amazon_ndvi" / "geocard.yaml").exists():
        return candidate
    candidate = Path.cwd() / "examples"
    if (candidate / "amazon_ndvi" / "geocard.yaml").exists():
        return candidate
    return None


def _load_demo_module() -> Any:
    """Import ``examples.amazon_ndvi.skill`` so the CLI can run the demo."""
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        return None
    sys.path.insert(0, str(examples_dir.parent))
    import examples.amazon_ndvi.skill as demo_skill  # noqa: PLC0415

    return demo_skill


# --------------------------------------------------------------------------- #
# Commands
# --------------------------------------------------------------------------- #
def cmd_version(args: argparse.Namespace) -> int:
    print(f"geonexus {__version__}")
    return 0


def cmd_card_validate(args: argparse.Namespace) -> int:
    from .geocard import GeoCardError, GeoCardValidationError, load_geocard

    try:
        card = load_geocard(args.file, validate=True)
    except GeoCardValidationError as exc:
        print(f"INVALID: {args.file}")
        for error in exc.errors:
            print(f"  - {error}")
        return 1
    except GeoCardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"OK: {args.file}")
    print(f"  id:          {card.id}")
    print(f"  type:        {card.type}")
    print(f"  name:        {card.name}")
    print(f"  version:     {card.geocard_version}")
    print(f"  capabilities: {', '.join(card.capability_names()) or '-'}")
    print(f"  bands:       {', '.join(card.band_names()) or '-'}")
    return 0


def cmd_card_inspect(args: argparse.Namespace) -> int:
    from .geocard import GeoCardError, load_geocard_dict

    try:
        data = load_geocard_dict(args.file, validate=False)
    except GeoCardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    import json

    print(json.dumps(data, indent=2, ensure_ascii=False))
    return 0


def _register_demo_assets(node: Any, out: Any = None) -> None:
    """Register the bundled demo GeoCard and ndvi-analysis skill on a node.

    ``out`` selects where progress messages go (default: stdout). MCP stdio
    bridges must pass ``sys.stderr`` so the protocol stream stays clean.
    """
    out = out or sys.stdout
    demo = _load_demo_module()
    examples_dir = _find_examples_dir()
    if demo is None or examples_dir is None:
        print(
            "WARNING: examples directory not found; starting without demo assets "
            "(GEONEXUS_EXAMPLES_DIR can point to it).",
            file=sys.stderr,
        )
        return
    from .geocard import GeoCardBuilder, load_geocard
    from .geonode import Skill

    card = load_geocard(examples_dir / "amazon_ndvi" / "geocard.yaml")
    node.register_geocard(card)
    skill_card = (
        GeoCardBuilder(
            id="skill-ndvi-analysis",
            type="skill",
            name="NDVI Analysis Skill",
            description="Compute NDVI from red/NIR rasters (demo, synthetic data).",
        )
        .capability("ndvi")
        .build()
    )
    skill = Skill(
        name="ndvi-analysis",
        description="Compute NDVI from red/NIR rasters (demo skill, synthetic data).",
        input_schema={"required": ["red", "nir"]},
        output_schema={},
        handler=demo.ndvi_analysis_handler,
        geocard=skill_card,
    )
    node.register_skill_object(skill)
    print(
        f"Registered demo assets: GeoCard '{card.id}', skill 'ndvi-analysis' (synthetic data).",
        file=out,
    )


def cmd_node_start(args: argparse.Namespace) -> int:
    from .geonode import GeoNode

    node = GeoNode(name=args.name, host=args.host, port=args.port)
    if not args.bare:
        _register_demo_assets(node)
    for ogc_ref in args.ogc_process or []:
        api_url, _, process_id = ogc_ref.partition("::")
        if not api_url or not process_id:
            print(
                f"ERROR: --ogc-process must be 'URL::PROCESS_ID', got {ogc_ref!r}",
                file=sys.stderr,
            )
            return 1
        try:
            from .adapters import register_ogc_process_skill

            register_ogc_process_skill(node, api_url, process_id)
            print(f"Registered remote OGC process skill '{process_id}' -> {api_url}")
        except Exception as exc:  # noqa: BLE001 - startup reporting
            print(
                f"WARNING: could not register OGC process '{process_id}': {exc}",
                file=sys.stderr,
            )
    print(f"Starting GeoNode '{node.name}' on {args.host}:{args.port} ...")
    if args.registry:
        try:
            node.advertise(args.registry)
            node.set_registry(args.registry)
            print(
                f"Advertised cards and skills at registry {args.registry}; "
                "node-level delegation enabled"
            )
        except Exception as exc:  # noqa: BLE001 - startup reporting
            print(f"WARNING: could not advertise at {args.registry}: {exc}", file=sys.stderr)
    node.run()  # blocking
    return 0


def cmd_skill_list(args: argparse.Namespace) -> int:
    from .geomcp import GeoMCPClient, GeoMCPClientError

    try:
        with GeoMCPClient(args.url, timeout=args.timeout) as client:
            caps = client.capabilities()
    except GeoMCPClientError as exc:
        print(
            f"Could not reach a GeoNode at {args.url}: {exc}\nStart one with: geonexus node start",
            file=sys.stderr,
        )
        return 1
    skills = caps.get("skills", [])
    if not skills:
        print(f"No skills registered on {args.url}.")
        return 0
    print(f"Skills on {args.url} ({len(skills)}):")
    for skill in skills:
        print(f"  - {skill['name']}: {skill['description']}")
    return 0


def cmd_demo(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples (amazon_ndvi). "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.amazon_ndvi.run_demo as demo

    demo.run(output_dir=args.output, port=args.port, use_server=not args.no_server)
    return 0


def cmd_init(args: argparse.Namespace) -> int:
    project_dir = Path(args.project)
    if project_dir.exists() and any(project_dir.iterdir()):
        print(f"ERROR: {project_dir} exists and is not empty.", file=sys.stderr)
        return 1
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "geocard.yaml").write_text(_INIT_GEOCARD_TEMPLATE, encoding="utf-8")
    (project_dir / "README.md").write_text(
        "# " + project_dir.name + "\n\nGeoNexus project scaffold. "
        "See docs/QUICKSTART.md for next steps.\n",
        encoding="utf-8",
    )
    print(f"Initialized GeoNexus project at {project_dir}")
    return 0


_INIT_GEOCARD_TEMPLATE = """\
geocard_version: "0.1"
id: my-first-asset
type: data
name: My First Asset
description: Describe your geospatial asset here.
spatial:
  bbox: [-74.0, -16.0, -44.0, 6.0]
  crs: "EPSG:4326"
  resolution: 10
temporal:
  start: "2020-01-01"
  end: "2030-12-31"
bands:
  - name: B04
    dtype: uint16
    units: dn
capabilities:
  - ndvi
"""


def cmd_card_import_stac(args: argparse.Namespace) -> int:
    from .adapters import StacAdapterError, import_stac_item

    try:
        card = import_stac_item(args.source, node_url=args.node, timeout=args.timeout)
    except StacAdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Imported STAC item '{args.source}' as GeoCard '{card.id}'")
    print(f"  id:          {card.id}")
    print(f"  name:        {card.name}")
    print(
        f"  spatial:     {card.spatial.crs if card.spatial else '-'} "
        f"bbox={card.spatial.bbox if card.spatial else '-'}"
    )
    if card.temporal:
        print(f"  temporal:    {card.temporal.start} .. {card.temporal.end}")
    print(f"  bands:       {', '.join(card.band_names()) or '-'}")
    print(
        f"  access:      {card.access.protocol if card.access else '-'} "
        f"{card.access.endpoint if card.access else ''}"
    )
    if args.output:
        card.save(args.output)
        print(f"  saved to:    {args.output}")
    return 0


def _print_imported_card(card: Any, source: str, kind: str) -> None:
    print(f"Imported {kind} '{source}' as GeoCard '{card.id}'")
    print(f"  id:          {card.id}")
    print(f"  type:        {card.type}")
    print(f"  name:        {card.name}")
    print(
        f"  spatial:     {card.spatial.crs if card.spatial else '-'} "
        f"bbox={card.spatial.bbox if card.spatial else '-'}"
    )
    if card.temporal:
        print(f"  temporal:    {card.temporal.start} .. {card.temporal.end}")
    print(f"  inputs:      {[i.name for i in card.inputs] or '-'}")
    print(f"  outputs:     {[o.name for o in card.outputs] or '-'}")
    print(
        f"  access:      {card.access.protocol if card.access else '-'} "
        f"{card.access.endpoint if card.access else ''}"
    )


def cmd_card_import_ogc(args: argparse.Namespace) -> int:
    from .adapters import (
        OgcAdapterError,
        fetch_ogc_collection,
        fetch_ogc_feature,
    )

    if args.feature:
        if not args.collection:
            print("ERROR: --collection is required with --feature", file=sys.stderr)
            return 1
        try:
            card = fetch_ogc_feature(args.url, args.collection, args.feature, timeout=args.timeout)
        except OgcAdapterError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        _print_imported_card(card, f"{args.collection}/{args.feature}", "OGC feature")
    else:
        if not args.collection:
            print("ERROR: --collection is required (or pass --feature)", file=sys.stderr)
            return 1
        try:
            card = fetch_ogc_collection(args.url, args.collection, timeout=args.timeout)
        except OgcAdapterError as exc:
            print(f"ERROR: {exc}", file=sys.stderr)
            return 1
        _print_imported_card(card, args.collection, "OGC collection")
    if args.output:
        card.save(args.output)
        print(f"  saved to:    {args.output}")
    return 0


def cmd_card_import_ogc_process(args: argparse.Namespace) -> int:
    from .adapters import OgcAdapterError, fetch_ogc_process

    try:
        card = fetch_ogc_process(args.url, args.process, timeout=args.timeout)
    except OgcAdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_imported_card(card, args.process, "OGC process")
    if args.output:
        card.save(args.output)
        print(f"  saved to:    {args.output}")
    return 0


def cmd_registry_start(args: argparse.Namespace) -> int:
    from .registry import RegistryServer

    api_keys = {args.api_key} if args.api_key else set()
    peers = args.peer or []
    print(
        f"Starting GeoCard Registry on {args.host}:{args.port} "
        f"(persist={args.persist or '-'}, auth={'on' if api_keys else 'off'}, "
        f"peers={len(peers)}) ..."
    )
    server = RegistryServer(
        name=args.name,
        persist_path=args.persist,
        api_keys=api_keys,
        peers=peers,
        health_probe=args.health_probe,
    )
    for peer in peers:
        print(f"  peer: {peer}")
    server.run(host=args.host, port=args.port)
    return 0


def cmd_registry_sync(args: argparse.Namespace) -> int:
    from .registry import RegistryClient, RegistryClientError

    try:
        with RegistryClient(args.url, timeout=args.timeout, api_key=args.api_key) as registry:
            report = registry._call("POST", "/sync")
    except RegistryClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Sync report from {args.url}:")
    print(f"  peers:            {report.get('peers')}")
    print(f"  cards_added:      {report.get('cards_added')}")
    print(f"  cards_updated:    {report.get('cards_updated')}")
    print(f"  cards_skipped:    {report.get('cards_skipped')}")
    print(f"  skills_added:     {report.get('skills_added')}")
    print(f"  skills_updated:   {report.get('skills_updated')}")
    print(f"  skills_skipped:   {report.get('skills_skipped')}")
    errors = report.get("errors") or {}
    for peer, err in errors.items():
        print(f"  error ({peer}): {err}")
    return 0


def cmd_registry_register(args: argparse.Namespace) -> int:
    from .geocard import GeoCardError, load_geocard
    from .registry import RegistryClient, RegistryClientError

    try:
        card = load_geocard(args.file)
    except GeoCardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    try:
        with RegistryClient(args.url, api_key=args.api_key) as registry:
            result = registry.register(card, node_url=args.node)
    except RegistryClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Registered '{card.id}' -> {args.node} at {args.url}")
    print(f"  entry: {result.get('entry')}")
    return 0


def cmd_registry_search(args: argparse.Namespace) -> int:
    from .registry import RegistryClient, RegistryClientError

    bbox = None
    if args.bbox:
        try:
            bbox = [float(x) for x in args.bbox.split(",")]
        except ValueError:
            print("ERROR: --bbox must be 'w,s,e,n'", file=sys.stderr)
            return 1
    results: list[dict[str, Any]] = []
    try:
        with RegistryClient(args.url, timeout=args.timeout) as registry:
            results = registry.search(
                capability=args.capability,
                type=args.type,
                bbox=bbox,
                crs=args.crs,
                start=args.start,
                end=args.end,
                required_bands=([b for b in args.bands.split(",") if b] if args.bands else None),
            )
    except RegistryClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not results:
        print(f"No cards match on {args.url}.")
        return 0
    print(f"{len(results)} card(s) match on {args.url}:")
    for result in results:
        entry = result["entry"]
        card = entry["card"]
        print(f"  - {card['id']} ({card['type']}) -> {entry['node_url']}")
        contract = result.get("contract")
        if contract:
            print(f"      contract satisfied={contract['satisfied']}")
    return 0


def cmd_registry_skill_register(args: argparse.Namespace) -> int:
    from .registry import RegistryClient, RegistryClientError

    capabilities = [c for c in args.capability.split(",") if c] if args.capability else []
    try:
        with RegistryClient(args.url, timeout=args.timeout, api_key=args.api_key) as registry:
            registry.register_skill(
                name=args.name,
                node_url=args.node,
                description=args.description,
                capabilities=capabilities,
            )
    except RegistryClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Registered skill '{args.name}' -> {args.node} at {args.url}")
    print(f"  capabilities: {capabilities or '-'}")
    return 0


def cmd_registry_skill_list(args: argparse.Namespace) -> int:
    from .registry import RegistryClient, RegistryClientError

    try:
        with RegistryClient(args.url, timeout=args.timeout) as registry:
            skills = registry.search_skills(capability=args.capability)
    except RegistryClientError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    if not skills:
        print(f"No skills registered on {args.url}.")
        return 0
    print(f"{len(skills)} skill(s) on {args.url}:")
    for entry in skills:
        skill = entry["skill"]
        caps = ", ".join(skill["capabilities"]) or "-"
        print(f"  - {skill['name']}: {skill['description']} [{caps}] -> {entry['node_url']}")
    return 0


def cmd_inspector(args: argparse.Namespace) -> int:
    from .geomcp import GeoMCPClient, GeoMCPClientError

    try:
        with GeoMCPClient(args.url, timeout=args.timeout) as client:
            health = client.health()
            caps = client.capabilities()
            described = client.describe()
    except GeoMCPClientError as exc:
        print(f"ERROR: cannot inspect {args.url}: {exc}", file=sys.stderr)
        return 1

    if args.json:
        import json

        print(json.dumps({"health": health, "capabilities": caps}, indent=2))
        return 0

    print(f"GeoMCP Inspector — {args.url}")
    print(f"  node:      {health.get('node')}")
    print(f"  status:    {health.get('status')} (protocol {caps.get('version')})")
    print(f"  methods:   {', '.join(caps.get('methods', []))}")
    tools = caps.get("tools", [])
    if tools:
        print(f"  tools ({len(tools)}):")
        for tool in tools:
            print(f"    - {tool['name']}: {tool['description']}")
    skills = caps.get("skills", [])
    if skills:
        print(f"  skills ({len(skills)}):")
        for skill in skills:
            print(f"    - {skill['name']}: {skill['description']}")
    cards = described.get("geocards", [])
    if cards:
        print(f"  geocards ({len(cards)}):")
        for card in cards:
            print(f"    - {card['id']} ({card['type']}) — {card['name']}")
    return 0


def cmd_demo_federated(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.federated.run_demo as federated

    federated.run(
        registry_port=args.registry_port,
        node_port=args.node_port,
        output_dir=args.output,
    )
    return 0


def cmd_demo_stac_real(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.stac_real.run_demo as stac_real

    stac_real.run(output_dir=args.output, item_url=args.item)
    return 0


def cmd_agent_run(args: argparse.Namespace) -> int:
    from .agent import ExecutionError, GeoAgentPlanner, Goal, PlanExecutor

    spatial: dict[str, Any] = {}
    if args.bbox:
        try:
            spatial["bbox"] = [float(x) for x in args.bbox.split(",")]
        except ValueError:
            print("ERROR: --bbox must be 'w,s,e,n'", file=sys.stderr)
            return 1
        if args.crs:
            spatial["crs"] = args.crs
    windows: list[dict[str, str]] = []
    for window in args.window or []:
        start, _, end = window.partition("/")
        if not start or not end:
            print("ERROR: --window must be 'START/END'", file=sys.stderr)
            return 1
        windows.append({"start": start, "end": end})
    params: dict[str, Any] = {}
    for kv in args.param or []:
        key, _, value = kv.partition("=")
        if not key:
            print(f"ERROR: --param must be 'key=value', got {kv!r}", file=sys.stderr)
            return 1
        params[key.strip()] = value.strip()

    goal = Goal(
        capability=args.capability,
        skill=args.skill,
        spatial=spatial,
        temporal_steps=windows,
        required_bands=[b for b in (args.bands or "").split(",") if b],
        params=params,
        label=args.label or args.capability,
    )
    try:
        plan = GeoAgentPlanner(args.registry, timeout=args.timeout).plan(goal)
    except ExecutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Goal: {plan.goal.get('label')} (capability={goal.capability})")
    print(f"Planned {len(plan.steps)} step(s) via {args.registry}:")
    for step in plan.steps:
        print(f"  {step.step_id}. {step.description} -> {step.node_url}")
    print("\nExecuting...")
    with PlanExecutor(args.registry, timeout=args.timeout) as executor:
        plan = executor.run(plan)
    for step in plan.steps:
        print(f"  step {step.step_id}: {step.status}" + (f"  ({step.error})" if step.error else ""))
    if plan.failed:
        print(f"\nERROR: {plan.failed}/{len(plan.steps)} step(s) failed", file=sys.stderr)
        return 1
    print(f"\nAll {plan.done} step(s) completed successfully.")
    return 0


def cmd_agent_ask(args: argparse.Namespace) -> int:
    """Translate a natural-language request with an LLM, then plan+execute."""
    from .agent import (
        ExecutionError,
        LLMConfig,
        LLMGoalPlanner,
        run_goal,
    )
    from .registry import RegistryClient

    # Ground the LLM with the registry's actual capabilities and skills.
    capabilities: list[str] = []
    skills: list[str] = []
    try:
        with RegistryClient(args.registry, timeout=args.timeout) as registry:
            for entry in registry.list_skills():
                skills.append(entry["skill"]["name"])
                for cap in entry["skill"].get("capabilities", []):
                    if cap not in capabilities:
                        capabilities.append(cap)
    except Exception as exc:  # noqa: BLE001 - grounding is best-effort
        print(f"WARNING: registry grounding failed ({exc}); continuing", file=sys.stderr)

    config = LLMConfig(
        base_url=args.llm_base_url,
        api_key=args.llm_api_key or os.environ.get("GEONEXUS_LLM_API_KEY", ""),
        model=args.llm_model,
    )
    try:
        with LLMGoalPlanner(config=config) as planner:
            goal = planner.plan(
                args.goal_text,
                available_capabilities=capabilities,
                available_skills=skills,
            )
    except ExecutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1

    print(f"Translated goal: {goal.model_dump(exclude_none=True)}")
    try:
        plan = run_goal(goal, args.registry, timeout=args.timeout)
    except ExecutionError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    print(f"Planned {len(plan.steps)} step(s):")
    for step in plan.steps:
        print(f"  {step.step_id}. {step.description} -> {step.node_url} ({step.status})")
    for step in plan.steps:
        if step.error:
            print(f"  step {step.step_id} error: {step.error}", file=sys.stderr)
    if plan.failed:
        print(f"ERROR: {plan.failed}/{len(plan.steps)} step(s) failed", file=sys.stderr)
        return 1
    print(f"All {plan.done} step(s) completed successfully.")
    return 0


def cmd_mcp_run(args: argparse.Namespace) -> int:
    from .geonode import GeoNode
    from .mcp_adapter import run_stdio

    node = GeoNode(name=args.name)
    if not args.bare:
        # Progress messages must not pollute the stdio protocol stream.
        _register_demo_assets(node, out=sys.stderr)
    print("Starting MCP stdio bridge (GeoMCP over Model Context Protocol)...", file=sys.stderr)
    run_stdio(node.server)
    return 0


def cmd_mcp_serve(args: argparse.Namespace) -> int:
    from .geonode import GeoNode
    from .mcp_adapter import run_http

    node = GeoNode(name=args.name)
    if not args.bare:
        _register_demo_assets(node)
    print(f"Starting MCP Streamable HTTP bridge on {args.host}:{args.port}{args.path} ...")
    run_http(node.server, host=args.host, port=args.port, path=args.path)
    return 0


def cmd_demo_agent(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.agent.run_demo as agent_demo

    agent_demo.run(
        registry_port=args.registry_port,
        node_port=args.node_port,
        output_dir=args.output,
    )
    return 0


def cmd_demo_ogc_process(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.ogc_process.run_demo as ogc_demo

    ogc_demo.run(node_port=args.node_port, remote=args.remote, process=args.process)
    return 0


def cmd_card_import_ogc_coverage(args: argparse.Namespace) -> int:
    from .adapters import OgcAdapterError, fetch_ogc_coverage_metadata

    try:
        card = fetch_ogc_coverage_metadata(args.url, args.collection, timeout=args.timeout)
    except OgcAdapterError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    _print_imported_card(card, args.collection, "OGC coverage")
    print(f"  bands:       {card.band_names() or '-'}")
    if args.output:
        card.save(args.output)
        print(f"  saved to:    {args.output}")
    return 0


def cmd_demo_ogc_coverage(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.ogc_coverage.run_demo as coverage_demo

    coverage_demo.run(node_port=args.node_port, remote=args.remote, output=args.output)
    return 0


def cmd_demo_ogc_pipeline(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.ogc_pipeline.run_demo as pipeline_demo

    pipeline_demo.run(registry_port=args.registry_port, node_port=args.node_port)
    return 0


def cmd_demo_federation_deep(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.federation_deep.run_demo as deep_demo

    deep_demo.run(
        registry_port=args.registry_port,
        node_a_port=args.node_a_port,
        node_b_port=args.node_b_port,
    )
    return 0


def cmd_ggihs_start(args: argparse.Namespace) -> int:
    from .ggihs import GGIHSService

    print(
        f"Starting GGIHS on {args.host}:{args.port} aggregating "
        f"{len(args.registry)} registry/registries (live_probe={args.live_probe}) ..."
    )
    GGIHSService(
        registries=args.registry,
        name=args.name,
        live_probe=args.live_probe,
        probe_timeout=args.probe_timeout,
    ).run(host=args.host, port=args.port)
    return 0


def cmd_demo_ggihs(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.ggihs.run_demo as ggihs_demo

    ggihs_demo.run(
        registry_a_port=args.registry_a_port,
        registry_b_port=args.registry_b_port,
        node_a_port=args.node_a_port,
        node_b_port=args.node_b_port,
        ggihs_port=args.ggihs_port,
    )
    return 0


def cmd_card_export_stac(args: argparse.Namespace) -> int:
    from .adapters import geocard_to_stac_item, save_stac_item
    from .geocard import GeoCardError, load_geocard

    try:
        card = load_geocard(args.file)
    except GeoCardError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 1
    item = geocard_to_stac_item(card, collection=args.collection)
    out = args.output or f"{card.id}.stac-item.json"
    save_stac_item(item, out)
    print(f"Exported GeoCard '{card.id}' as STAC Item -> {out}")
    print(f"  stac id:   {item['id']}")
    print(f"  bbox:      {item.get('bbox')}")
    print(f"  assets:    {list(item['assets'].keys())}")
    print(f"  collection:{args.collection or '-'}")
    return 0


def cmd_demo_stac_write(args: argparse.Namespace) -> int:
    examples_dir = _find_examples_dir()
    if examples_dir is None:
        print(
            "ERROR: could not locate the bundled examples. "
            "Run from the GeoNexus mvp repository or set GEONEXUS_EXAMPLES_DIR.",
            file=sys.stderr,
        )
        return 1
    sys.path.insert(0, str(examples_dir.parent))
    import examples.stac_write.run_demo as stac_write_demo

    stac_write_demo.run(output_dir=args.output)
    return 0


# --------------------------------------------------------------------------- #
# Parser
# --------------------------------------------------------------------------- #
def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="geonexus",
        description="GeoNexus Reference Stack MVP - GeoCard, GeoMCP, GeoNode, GeoSkill",
    )
    parser.add_argument("--verbose", action="store_true", help="Verbose logging")
    sub = parser.add_subparsers(dest="command", required=True)

    sub.add_parser("version", help="Print the geonexus version").set_defaults(func=cmd_version)

    card = sub.add_parser("card", help="GeoCard commands")
    card_sub = card.add_subparsers(dest="card_command", required=True)
    p_validate = card_sub.add_parser("validate", help="Validate a GeoCard file")
    p_validate.add_argument("file", help="Path to a .yaml/.yml/.json GeoCard")
    p_validate.set_defaults(func=cmd_card_validate)
    p_inspect = card_sub.add_parser("inspect", help="Print a GeoCard file")
    p_inspect.add_argument("file", help="Path to a GeoCard file")
    p_inspect.set_defaults(func=cmd_card_inspect)
    p_stac = card_sub.add_parser(
        "import-stac", help="Import a STAC Item (URL or file) as a GeoCard"
    )
    p_stac.add_argument("source", help="STAC Item URL or local .json path")
    p_stac.add_argument("--output", help="Save the card to this .yaml/.json file")
    p_stac.add_argument("--node", help="Endpoint to record in access.endpoint")
    p_stac.add_argument("--timeout", type=float, default=30.0)
    p_stac.set_defaults(func=cmd_card_import_stac)

    p_ogc = card_sub.add_parser(
        "import-ogc", help="Import an OGC API Features collection/item as a GeoCard"
    )
    p_ogc.add_argument("url", help="OGC API root URL, e.g. https://demo.pygeoapi.io/master")
    p_ogc.add_argument("--collection", help="Collection id")
    p_ogc.add_argument("--feature", help="Feature id (imports one item)")
    p_ogc.add_argument("--output", help="Save the card to this .yaml/.json file")
    p_ogc.add_argument("--timeout", type=float, default=30.0)
    p_ogc.set_defaults(func=cmd_card_import_ogc)

    p_ogc_process = card_sub.add_parser(
        "import-ogc-process",
        help="Import an OGC API - Processes process as a skill GeoCard",
    )
    p_ogc_process.add_argument("url", help="OGC API root URL")
    p_ogc_process.add_argument("--process", required=True, help="Process id")
    p_ogc_process.add_argument("--output", help="Save the card to this .yaml/.json file")
    p_ogc_process.add_argument("--timeout", type=float, default=30.0)
    p_ogc_process.set_defaults(func=cmd_card_import_ogc_process)

    p_ogc_cov = card_sub.add_parser(
        "import-ogc-coverage",
        help="Import OGC API - Coverages metadata as a GeoCard",
    )
    p_ogc_cov.add_argument("url", help="OGC API root URL")
    p_ogc_cov.add_argument("--collection", required=True, help="Coverage collection id")
    p_ogc_cov.add_argument("--output", help="Save the card to this .yaml/.json file")
    p_ogc_cov.add_argument("--timeout", type=float, default=30.0)
    p_ogc_cov.set_defaults(func=cmd_card_import_ogc_coverage)

    p_stac_export = card_sub.add_parser(
        "export-stac", help="Export a GeoCard file as a STAC Item (V1.0+)"
    )
    p_stac_export.add_argument("file", help="Path to a .yaml/.yml/.json GeoCard")
    p_stac_export.add_argument("--output", help="Output .json path")
    p_stac_export.add_argument("--collection", help="STAC collection id")
    p_stac_export.set_defaults(func=cmd_card_export_stac)

    node = sub.add_parser("node", help="GeoNode commands")
    node_sub = node.add_subparsers(dest="node_command", required=True)
    p_start = node_sub.add_parser("start", help="Start the Local GeoNode (blocking)")
    p_start.add_argument("--host", default="127.0.0.1")
    p_start.add_argument("--port", type=int, default=8787)
    p_start.add_argument("--name", default="local-node")
    p_start.add_argument("--bare", action="store_true", help="Start without bundled demo assets")
    p_start.add_argument(
        "--registry",
        default=None,
        metavar="URL",
        help="Advertise this node's cards at a GeoCard Registry",
    )
    p_start.add_argument(
        "--ogc-process",
        action="append",
        default=None,
        metavar="URL::PROCESS_ID",
        help="Register a remote OGC API - Processes process as a skill "
        "(repeatable, V1.0 write-side bridge)",
    )
    p_start.set_defaults(func=cmd_node_start)

    registry = sub.add_parser("registry", help="Shared GeoCard Registry commands")
    registry_sub = registry.add_subparsers(dest="registry_command", required=True)
    p_reg_start = registry_sub.add_parser("start", help="Start the registry service")
    p_reg_start.add_argument("--host", default="127.0.0.1")
    p_reg_start.add_argument("--port", type=int, default=8790)
    p_reg_start.add_argument("--name", default="geonexus-registry")
    p_reg_start.add_argument(
        "--persist", default=None, help="JSON file to persist cards/skills (V1.0)"
    )
    p_reg_start.add_argument(
        "--api-key", default=None, help="Require X-API-Key on write endpoints (V1.0)"
    )
    p_reg_start.add_argument(
        "--health-probe",
        action="store_true",
        help="Probe node /health in the /nodes view (V1.0+)",
    )
    p_reg_start.add_argument(
        "--peer",
        action="append",
        default=None,
        help="Peer registry URL to sync from (repeatable, V1.0+)",
    )
    p_reg_start.set_defaults(func=cmd_registry_start)
    p_reg_sync = registry_sub.add_parser(
        "sync", help="Pull cards/skills from configured peers (V1.0+)"
    )
    p_reg_sync.add_argument("--url", default="http://127.0.0.1:8790")
    p_reg_sync.add_argument("--api-key", default=None)
    p_reg_sync.add_argument("--timeout", type=float, default=30.0)
    p_reg_sync.set_defaults(func=cmd_registry_sync)
    p_reg_register = registry_sub.add_parser(
        "register", help="Register a GeoCard file at a registry"
    )
    p_reg_register.add_argument("file", help="Path to a GeoCard file")
    p_reg_register.add_argument("--url", default="http://127.0.0.1:8790")
    p_reg_register.add_argument(
        "--node", default="http://127.0.0.1:8787", help="Owning node endpoint"
    )
    p_reg_register.add_argument("--api-key", default=None)
    p_reg_register.set_defaults(func=cmd_registry_register)
    p_reg_search = registry_sub.add_parser(
        "search", help="Search a registry (contract pre-filtering)"
    )
    p_reg_search.add_argument("--url", default="http://127.0.0.1:8790")
    p_reg_search.add_argument("--capability")
    p_reg_search.add_argument("--type")
    p_reg_search.add_argument("--bbox", help="w,s,e,n")
    p_reg_search.add_argument("--crs", default="EPSG:4326")
    p_reg_search.add_argument("--start")
    p_reg_search.add_argument("--end")
    p_reg_search.add_argument("--bands", help="comma-separated required bands")
    p_reg_search.add_argument("--timeout", type=float, default=10.0)
    p_reg_search.set_defaults(func=cmd_registry_search)

    reg_skill = registry_sub.add_parser("skill", help="Skill registry commands")
    reg_skill_sub = reg_skill.add_subparsers(dest="skill_command", required=True)
    p_skill_reg = reg_skill_sub.add_parser("register", help="Register a skill at a registry")
    p_skill_reg.add_argument("name", help="Skill name")
    p_skill_reg.add_argument("--url", default="http://127.0.0.1:8790")
    p_skill_reg.add_argument("--node", default="http://127.0.0.1:8787")
    p_skill_reg.add_argument("--description", default="")
    p_skill_reg.add_argument("--capability", help="Comma-separated capabilities")
    p_skill_reg.add_argument("--api-key", default=None)
    p_skill_reg.add_argument("--timeout", type=float, default=10.0)
    p_skill_reg.set_defaults(func=cmd_registry_skill_register)
    p_skill_list = reg_skill_sub.add_parser(
        "list", help="List skills at a registry (optionally by capability)"
    )
    p_skill_list.add_argument("--url", default="http://127.0.0.1:8790")
    p_skill_list.add_argument("--capability", help="Filter by capability")
    p_skill_list.add_argument("--timeout", type=float, default=10.0)
    p_skill_list.set_defaults(func=cmd_registry_skill_list)

    inspector = sub.add_parser("inspector", help="Inspect a running GeoMCP node / registry")
    inspector.add_argument("url", help="Node URL, e.g. http://127.0.0.1:8787")
    inspector.add_argument("--json", action="store_true", help="Raw JSON output")
    inspector.add_argument("--timeout", type=float, default=10.0)
    inspector.set_defaults(func=cmd_inspector)

    skill = sub.add_parser("skill", help="Skill commands")
    skill_sub = skill.add_subparsers(dest="skill_command", required=True)
    p_skill_list = skill_sub.add_parser("list", help="List skills on a running node")
    p_skill_list.add_argument("--url", default="http://127.0.0.1:8787")
    p_skill_list.add_argument("--timeout", type=float, default=5.0)
    p_skill_list.set_defaults(func=cmd_skill_list)

    demo = sub.add_parser("demo", help="Run a demo")
    demo_sub = demo.add_subparsers(dest="demo_command", required=True)
    p_demo = demo_sub.add_parser(
        "amazon-ndvi", help="Amazon NDVI vegetation-change demo (synthetic data)"
    )
    p_demo.add_argument("--output", default=None, help="Output directory")
    p_demo.add_argument("--port", type=int, default=8787)
    p_demo.add_argument("--no-server", action="store_true", help="Run in-process (no HTTP)")
    p_demo.set_defaults(func=cmd_demo)

    p_fed = demo_sub.add_parser(
        "federated",
        help="Federated demo: registry + data node + pushdown execution",
    )
    p_fed.add_argument("--registry-port", type=int, default=8790)
    p_fed.add_argument("--node-port", type=int, default=8787)
    p_fed.add_argument("--output", default=None, help="Output directory")
    p_fed.set_defaults(func=cmd_demo_federated)

    p_stac_real = demo_sub.add_parser(
        "stac-real",
        help="Real Sentinel-2 data demo (Planetary Computer, network required)",
    )
    p_stac_real.add_argument("--output", default=None)
    p_stac_real.add_argument(
        "--item",
        default=None,
        help="STAC item URL (default: latest Amazon tile on Planetary Computer)",
    )
    p_stac_real.set_defaults(func=cmd_demo_stac_real)

    p_agent = demo_sub.add_parser(
        "agent",
        help="GeoAgent demo: capability-based plan + pushdown execution",
    )
    p_agent.add_argument("--registry-port", type=int, default=8790)
    p_agent.add_argument("--node-port", type=int, default=8787)
    p_agent.add_argument("--output", default=None)
    p_agent.set_defaults(func=cmd_demo_agent)

    p_ogc = demo_sub.add_parser(
        "ogc-process",
        help="OGC write-side bridge demo: geo.execute -> OGC API - Processes",
    )
    p_ogc.add_argument("--node-port", type=int, default=8787)
    p_ogc.add_argument(
        "--remote",
        default=None,
        help="Real OGC API - Processes root (default: local offline mock)",
    )
    p_ogc.add_argument(
        "--process",
        default="echo",
        help="OGC process id (mock: 'echo'; pygeoapi: 'hello-world')",
    )
    p_ogc.set_defaults(func=cmd_demo_ogc_process)

    p_ogc_cov_demo = demo_sub.add_parser(
        "ogc-coverage",
        help="OGC API - Coverages demo: CoverageJSON -> NDVI -> GeoTIFF",
    )
    p_ogc_cov_demo.add_argument("--node-port", type=int, default=8787)
    p_ogc_cov_demo.add_argument(
        "--remote", default=None, help="Real OGC Coverages root (default: offline mock)"
    )
    p_ogc_cov_demo.add_argument("--output", default=None)
    p_ogc_cov_demo.set_defaults(func=cmd_demo_ogc_coverage)

    p_ogc_pipe = demo_sub.add_parser(
        "ogc-pipeline",
        help="GeoAgent pipeline demo: local skills + remote OGC process skills",
    )
    p_ogc_pipe.add_argument("--registry-port", type=int, default=8790)
    p_ogc_pipe.add_argument("--node-port", type=int, default=8787)
    p_ogc_pipe.set_defaults(func=cmd_demo_ogc_pipeline)

    p_fed_deep = demo_sub.add_parser(
        "federation-deep",
        help="GeoNode-to-GeoNode federation: delegation + health-aware discovery",
    )
    p_fed_deep.add_argument("--registry-port", type=int, default=8790)
    p_fed_deep.add_argument("--node-a-port", type=int, default=8787)
    p_fed_deep.add_argument("--node-b-port", type=int, default=8788)
    p_fed_deep.set_defaults(func=cmd_demo_federation_deep)

    ggihs = sub.add_parser("ggihs", help="GGIHS service (V1.0+)")
    ggihs_sub = ggihs.add_subparsers(dest="ggihs_command", required=True)
    p_ggihs_start = ggihs_sub.add_parser(
        "start", help="Start the cross-registry health/catalog aggregator"
    )
    p_ggihs_start.add_argument(
        "--registry", action="append", required=True, help="Registry URL (repeatable)"
    )
    p_ggihs_start.add_argument("--host", default="127.0.0.1")
    p_ggihs_start.add_argument("--port", type=int, default=8800)
    p_ggihs_start.add_argument("--name", default="ggihs")
    p_ggihs_start.add_argument(
        "--live-probe", action="store_true", help="Probe node /health directly"
    )
    p_ggihs_start.add_argument("--probe-timeout", type=float, default=3.0)
    p_ggihs_start.set_defaults(func=cmd_ggihs_start)

    p_ggihs_demo = demo_sub.add_parser(
        "ggihs",
        help="GGIHS demo: two federated registries + nodes + health/catalog aggregation",
    )
    p_ggihs_demo.add_argument("--registry-a-port", type=int, default=8790)
    p_ggihs_demo.add_argument("--registry-b-port", type=int, default=8791)
    p_ggihs_demo.add_argument("--node-a-port", type=int, default=8787)
    p_ggihs_demo.add_argument("--node-b-port", type=int, default=8788)
    p_ggihs_demo.add_argument("--ggihs-port", type=int, default=8800)
    p_ggihs_demo.set_defaults(func=cmd_demo_ggihs)

    p_stac_write = demo_sub.add_parser(
        "stac-write",
        help="STAC write-side demo: GeoCard -> STAC Item -> re-import",
    )
    p_stac_write.add_argument("--output", default=None)
    p_stac_write.set_defaults(func=cmd_demo_stac_write)

    agent = sub.add_parser("agent", help="GeoAgent commands (V0.5)")
    agent_sub = agent.add_subparsers(dest="agent_command", required=True)
    p_agent_run = agent_sub.add_parser("run", help="Plan and execute a capability-based goal")
    p_agent_run.add_argument("--registry", default="http://127.0.0.1:8790")
    p_agent_run.add_argument("--capability", required=True)
    p_agent_run.add_argument("--skill", help="Explicit skill name (optional)")
    p_agent_run.add_argument("--bbox", help="w,s,e,n")
    p_agent_run.add_argument("--crs", default="EPSG:4326")
    p_agent_run.add_argument("--window", action="append", help="START/END (repeatable)")
    p_agent_run.add_argument("--bands", help="Comma-separated required bands")
    p_agent_run.add_argument("--param", action="append", help="key=value (repeatable)")
    p_agent_run.add_argument("--label", default="")
    p_agent_run.add_argument("--timeout", type=float, default=30.0)
    p_agent_run.set_defaults(func=cmd_agent_run)

    p_agent_ask = agent_sub.add_parser(
        "ask",
        help="Translate a natural-language goal with an LLM, then plan+execute (V1.0)",
    )
    p_agent_ask.add_argument("goal_text", help="Natural-language goal")
    p_agent_ask.add_argument("--registry", default="http://127.0.0.1:8790")
    p_agent_ask.add_argument("--llm-base-url", default="https://api.openai.com/v1")
    p_agent_ask.add_argument(
        "--llm-api-key",
        default=None,
        help="OpenAI-compatible API key (default: $GEONEXUS_LLM_API_KEY)",
    )
    p_agent_ask.add_argument("--llm-model", default="gpt-4o-mini")
    p_agent_ask.add_argument("--timeout", type=float, default=60.0)
    p_agent_ask.set_defaults(func=cmd_agent_ask)

    mcp = sub.add_parser("mcp", help="MCP bridge commands (V0.5/V1.0)")
    mcp_sub = mcp.add_subparsers(dest="mcp_command", required=True)
    p_mcp_run = mcp_sub.add_parser("run", help="Run the GeoMCP-over-MCP bridge on stdio")
    p_mcp_run.add_argument("--name", default="local-node")
    p_mcp_run.add_argument("--bare", action="store_true", help="No demo assets")
    p_mcp_run.set_defaults(func=cmd_mcp_run)
    p_mcp_serve = mcp_sub.add_parser("serve", help="Run the MCP bridge over Streamable HTTP (V1.0)")
    p_mcp_serve.add_argument("--host", default="127.0.0.1")
    p_mcp_serve.add_argument("--port", type=int, default=9000)
    p_mcp_serve.add_argument("--path", default="/mcp")
    p_mcp_serve.add_argument("--name", default="local-node")
    p_mcp_serve.add_argument("--bare", action="store_true", help="No demo assets")
    p_mcp_serve.set_defaults(func=cmd_mcp_serve)

    p_init = sub.add_parser("init", help="Scaffold a new GeoNexus project")
    p_init.add_argument("project", help="Project directory to create")
    p_init.set_defaults(func=cmd_init)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if getattr(args, "verbose", False):
        logging.basicConfig(level=logging.DEBUG)
    else:
        logging.basicConfig(level=logging.WARNING)
    func = getattr(args, "func", None)
    if func is None:  # pragma: no cover - argparse handles this
        parser.print_help()
        return 2
    return func(args)


if __name__ == "__main__":  # pragma: no cover
    raise SystemExit(main())

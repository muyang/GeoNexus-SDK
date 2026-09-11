"""Write-side OGC API - Processes bridge (V1.0).

Bridges **GeoMCP execution** to **OGC API - Processes**: an OGC process is
wrapped as a GeoSkill whose handler posts an execution request, polls the
job (async execution with ``Prefer: respond-async``), fetches the results
and returns them in the GeoMCP ``geo.execute`` result envelope.

This completes the standards story: read side (discovery/import) in
:mod:`geonexus.adapters.ogc`, write side (execution) here. The OGC service
stays where it is; GeoNexus treats it as a remote capability node.
"""

from __future__ import annotations

import logging
import time
from typing import Any

import httpx

from ..geocard.builder import GeoCardBuilder
from .ogc import DEFAULT_TIMEOUT, fetch_ogc_process

logger = logging.getLogger(__name__)

# OGC API - Processes job statuses (part 1, 18-008r2).
JOB_STATUS = ("accepted", "running", "successful", "failed", "dismissed")


class OgcProcessExecutionError(Exception):
    """Raised when an OGC process execution cannot be completed."""


class OgcProcessExecutor:
    """Client for the OGC API - Processes execution endpoints.

    Args:
        api_url: OGC API root, e.g. ``https://demo.pygeoapi.io/master``.
        timeout: Request timeout in seconds.
        client: Optional pre-configured ``httpx.Client`` (advanced use).
    """

    def __init__(
        self,
        api_url: str,
        timeout: float = DEFAULT_TIMEOUT,
        client: httpx.Client | None = None,
    ) -> None:
        self.api_url = api_url.rstrip("/")
        self.timeout = timeout
        self._owns_client = client is None
        self._client = client or httpx.Client(
            base_url=self.api_url, timeout=timeout, follow_redirects=True
        )

    # ------------------------------------------------------------------ #
    def execute(
        self,
        process_id: str,
        inputs: dict[str, Any],
        outputs: dict[str, Any] | None = None,
        prefer_async: bool = True,
    ) -> dict[str, Any]:
        """Submit an execution request.

        Returns:
            ``{"status": "successful", "result": ...}`` for synchronous
            responses, or ``{"status": "accepted", "job_location": ...}``
            when the server responds asynchronously with a Location header.
        """
        body: dict[str, Any] = {"inputs": inputs}
        if outputs:
            body["outputs"] = outputs
        headers = {"Accept": "application/json"}
        if prefer_async:
            headers["Prefer"] = "respond-async"
        try:
            response = self._client.post(
                f"/processes/{process_id}/execution", json=body, headers=headers
            )
        except httpx.HTTPError as exc:
            raise OgcProcessExecutionError(
                f"OGC execution request failed for '{process_id}': {exc}"
            ) from exc
        if response.status_code not in (200, 201):
            raise OgcProcessExecutionError(
                f"OGC execution of '{process_id}' rejected "
                f"(HTTP {response.status_code}): {response.text[:200]}"
            )
        location = response.headers.get("location")
        payload = response.json() if response.content else None
        if location:
            return {"status": "accepted", "job_location": location}
        return {"status": "successful", "result": payload}

    def get_job(self, job_location: str) -> dict[str, Any]:
        """Fetch a job status document."""
        url = self._resolve(job_location)
        try:
            response = self._client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OgcProcessExecutionError(f"OGC job status request failed ({url}): {exc}") from exc
        try:
            job = response.json()
        except ValueError as exc:
            raise OgcProcessExecutionError(f"OGC job response is not JSON ({url})") from exc
        if not isinstance(job, dict):
            raise OgcProcessExecutionError(f"OGC job response must be an object ({url})")
        return job

    def wait_for_job(
        self,
        job_location: str,
        poll_interval: float = 1.0,
        timeout: float = 120.0,
    ) -> dict[str, Any]:
        """Poll a job until it reaches a terminal status."""
        deadline = time.monotonic() + timeout
        while True:
            job = self.get_job(job_location)
            status = str(job.get("status", "")).lower()
            if status in ("successful", "failed", "dismissed"):
                return job
            if status not in JOB_STATUS:
                logger.warning("Unexpected OGC job status %r", status)
            if time.monotonic() >= deadline:
                raise OgcProcessExecutionError(
                    f"OGC job timed out after {timeout}s ({job_location})"
                )
            time.sleep(poll_interval)

    def get_results(self, results_href: str) -> dict[str, Any]:
        """Fetch the results document of a completed job."""
        url = self._resolve(results_href)
        try:
            response = self._client.get(url, headers={"Accept": "application/json"})
            response.raise_for_status()
        except httpx.HTTPError as exc:
            raise OgcProcessExecutionError(f"OGC results request failed ({url}): {exc}") from exc
        try:
            data = response.json()
        except ValueError as exc:
            raise OgcProcessExecutionError(f"OGC results response is not JSON ({url})") from exc
        if not isinstance(data, dict):
            raise OgcProcessExecutionError("OGC results must be an object")
        return data

    def _resolve(self, url: str) -> str:
        """Resolve a possibly-relative job/results URL against the API root."""
        if url.startswith("http://") or url.startswith("https://"):
            return url
        return f"{self.api_url}/{url.lstrip('/')}"

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> OgcProcessExecutor:
        return self

    def __exit__(self, *exc: Any) -> None:
        self.close()


# --------------------------------------------------------------------------- #
# Bridge to GeoSkill
# --------------------------------------------------------------------------- #
def _to_ogc_inputs(params: dict[str, Any]) -> dict[str, Any]:
    """Convert GeoMCP skill params into OGC API - Processes inputs.

    Scalars become ``{"value": ...}``; a dict that already carries
    ``value``/``href`` is passed through (server-defined structures); lists
    become arrays of value objects.
    """
    inputs: dict[str, Any] = {}
    for key, value in params.items():
        if isinstance(value, dict) and ("value" in value or "href" in value):
            inputs[key] = value
        elif isinstance(value, list):
            inputs[key] = [item if isinstance(item, dict) else {"value": item} for item in value]
        else:
            inputs[key] = {"value": value}
    return inputs


def make_ogc_process_handler(
    api_url: str,
    process_id: str,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> Any:
    """Build a GeoSkill handler that executes an OGC process via GeoMCP.

    The returned handler accepts ``(params, context)`` and returns the
    GeoMCP result envelope: job status, results document and result hrefs.
    """
    executor = OgcProcessExecutor(api_url, timeout=timeout, client=client)

    def handler(params: dict[str, Any], context: Any) -> dict[str, Any]:
        inputs = _to_ogc_inputs(params)
        logger.info(
            "Bridging geo.execute -> OGC process '%s' at %s (inputs=%s)",
            process_id,
            api_url,
            sorted(inputs),
        )
        submitted = executor.execute(process_id, inputs)
        result: dict[str, Any] = {"job_status": submitted["status"]}
        if submitted.get("job_location"):
            job = executor.wait_for_job(submitted["job_location"])
            result["job_status"] = job.get("status")
            if str(job.get("status", "")).lower() == "failed":
                raise OgcProcessExecutionError(f"OGC process '{process_id}' failed: {job}")
            # Results href: OGC API allows either a job.results.href field or
            # a job.links entry with rel="results" (pygeoapi uses links).
            results_href = None
            raw_results = job.get("results")
            if isinstance(raw_results, dict) and isinstance(raw_results.get("href"), str):
                results_href = raw_results["href"]
            else:
                for link in job.get("links") or []:
                    rel = str(link.get("rel") or "")
                    # Servers use either the short rel "results" or the
                    # qualified OGC URI ".../ogc/1.0/results" (pygeoapi).
                    if (rel == "results" or rel.endswith("/results")) and link.get("href"):
                        results_href = link["href"]
                        break
            if results_href:
                result["result_href"] = results_href
                try:
                    result["results"] = executor.get_results(results_href)
                except OgcProcessExecutionError as exc:  # pragma: no cover - network
                    logger.warning("Could not fetch OGC results: %s", exc)
                    result["results"] = raw_results
            else:
                result["results"] = raw_results
        else:
            result["results"] = submitted.get("result")
        # Skill convention: return the outputs dict; the runtime wraps it.
        result["ogc_process"] = process_id
        result["ogc_endpoint"] = api_url
        return result

    return handler


def make_ogc_process_skill(
    api_url: str,
    process_id: str,
    fetch_card: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
    client: httpx.Client | None = None,
) -> Any:
    """Build a :class:`geonexus.geonode.Skill` wrapping an OGC process.

    When ``fetch_card`` is True (default), the process description is
    fetched and used for both the skill's GeoCard and its input schema. On
    network failure (or ``fetch_card=False``) a minimal card is built
    instead, and the handler still executes via the bridge.
    """
    from ..geonode.skill import Skill

    card = None
    try:
        if fetch_card:
            card = fetch_ogc_process(api_url, process_id, timeout=timeout, client=client)
    except Exception as exc:  # noqa: BLE001 - degrade to a minimal card
        logger.warning(
            "Could not fetch OGC process '%s' (%s); using a minimal card",
            process_id,
            exc,
        )
    if card is None:
        card = (
            GeoCardBuilder(
                id=process_id,
                type="skill",
                name=process_id,
                description=f"Remote OGC API - Processes skill '{process_id}'.",
            )
            .tag("ogcapi", "ogcapi-processes")
            .interface(type="ogcapi-process", version="1.0")
            .access(protocol="ogcapi", endpoint=api_url)
            .build()
        )
    input_schema = {
        "type": "object",
        "properties": {i.name: {"type": i.type} for i in card.inputs},
        "required": [i.name for i in card.inputs if i.required],
    }
    return Skill(
        name=process_id,
        description=str(card.description),
        input_schema=input_schema,
        output_schema={},
        handler=make_ogc_process_handler(api_url, process_id, timeout=timeout),
        geocard=card,
    )


def register_ogc_process_skill(
    node: Any,
    api_url: str,
    process_id: str,
    fetch_card: bool = True,
    timeout: float = DEFAULT_TIMEOUT,
) -> Any:
    """Register an OGC process as a remote skill on a GeoNode.

    Returns the registered :class:`Skill`; raises ``DuplicateEntryError`` if
    a skill with the same name already exists on the node.
    """
    skill = make_ogc_process_skill(api_url, process_id, fetch_card=fetch_card, timeout=timeout)
    node.register_skill_object(skill)
    logger.info(
        "Registered OGC process '%s' (endpoint %s) as a GeoSkill on '%s'",
        process_id,
        api_url,
        getattr(node, "name", "node"),
    )
    return skill

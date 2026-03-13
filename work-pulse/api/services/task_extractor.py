"""
Task Extractor — thin wrapper used by ingestion and brief services
to pull ExtractedTask objects from stored artifacts.
"""

from __future__ import annotations

from api.models.database import MockStorage, get_storage
from api.models.schemas import ExtractedTask


def get_tasks_from_artifact(
    artifact_id: str, storage: MockStorage | None = None
) -> list[ExtractedTask]:
    storage = storage or get_storage()
    artifact = storage.get("artifacts", artifact_id)
    if not artifact:
        return []
    raw_tasks = artifact.get("extracted_tasks", []) if isinstance(artifact, dict) else []
    tasks = []
    for t in raw_tasks:
        if isinstance(t, dict):
            tasks.append(ExtractedTask.model_validate(t))
        elif isinstance(t, ExtractedTask):
            tasks.append(t)
    return tasks


def get_tasks_for_project(
    project_id: str, storage: MockStorage | None = None
) -> list[ExtractedTask]:
    storage = storage or get_storage()
    artifact_ids: list[str] = storage.get("project_artifacts", project_id) or []
    all_tasks: list[ExtractedTask] = []
    for art_id in artifact_ids:
        all_tasks.extend(get_tasks_from_artifact(art_id, storage))
    return all_tasks

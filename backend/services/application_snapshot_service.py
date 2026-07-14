from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from backend.services.business_rules_service import (
    BusinessRulesSnapshot,
    load_business_rules_snapshot,
)
from backend.services.cache_generation_service import load_cache_generation
from backend.services.dashboard_facts_service import build_dashboard_facts_read_model
from backend.services.data_quality_service import build_data_quality_cached
from backend.services.decision_service import load_decision_targets
from backend.services.forecast_read_service import build_forecast_read_model
from backend.services.system_health_service import build_system_health


@dataclass(frozen=True)
class SnapshotPaths:
    db_path: Path
    cache_dir: Path
    runtime_dir: Path
    rules_config_path: Path
    target_config_path: Path

    @property
    def generation_path(self) -> Path:
        return self.runtime_dir / "data_generation.json"


@dataclass(frozen=True)
class SnapshotDependencies:
    rules_loader: Callable = load_business_rules_snapshot
    generation_loader: Callable = load_cache_generation
    facts_builder: Callable = build_dashboard_facts_read_model
    data_quality_builder: Callable = build_data_quality_cached
    forecast_builder: Callable = build_forecast_read_model
    health_builder: Callable = build_system_health
    target_loader: Callable = load_decision_targets


@dataclass(frozen=True)
class ApplicationSnapshot:
    generation_token: str
    rules: BusinessRulesSnapshot
    facts: dict
    forecast: dict
    quality: dict
    health: dict
    targets: dict
    provenance: dict


class SnapshotGenerationConflict(RuntimeError):
    def __init__(
        self,
        observed_tokens: tuple[tuple[str, str], ...],
    ) -> None:
        self.observed_tokens = observed_tokens
        self.attempts = len(observed_tokens)
        super().__init__(
            "Data generation changed while building the application snapshot; "
            "retry the request."
        )


class ApplicationSnapshotService:
    def __init__(
        self,
        paths: SnapshotPaths,
        *,
        dependencies: SnapshotDependencies | None = None,
    ) -> None:
        self.paths = paths
        self.dependencies = dependencies or SnapshotDependencies()

    def build(self) -> ApplicationSnapshot:
        observed_tokens: list[tuple[str, str]] = []
        for attempt in range(1, 3):
            rules = self.dependencies.rules_loader(self.paths.rules_config_path)
            start_generation = self.dependencies.generation_loader(
                self.paths.generation_path,
                db_path=self.paths.db_path,
            )
            generation_token = str(start_generation.get("cacheToken") or "0:missing")
            facts = self.dependencies.facts_builder(
                db_path=self.paths.db_path,
                generation_token=generation_token,
                cache_dir=self.paths.cache_dir,
                **rules.facts_kwargs(),
            )
            quality = self.dependencies.data_quality_builder(
                db_path=self.paths.db_path,
                generation_token=generation_token,
                cache_dir=self.paths.cache_dir,
            )
            forecast = self.dependencies.forecast_builder(cache_dir=self.paths.cache_dir)
            health = self.dependencies.health_builder(
                db_path=self.paths.db_path,
                cache_path=self.paths.cache_dir,
                runtime_dir=self.paths.runtime_dir,
            )
            targets = self.dependencies.target_loader(self.paths.target_config_path)
            end_generation = self.dependencies.generation_loader(
                self.paths.generation_path,
                db_path=self.paths.db_path,
            )
            end_token = str(end_generation.get("cacheToken") or "0:missing")
            observed_tokens.append((generation_token, end_token))
            if end_token != generation_token:
                continue
            provenance = {
                "generationToken": generation_token,
                "coreGenerationConsistent": True,
                "snapshotAttemptCount": attempt,
                "dbPath": str(self.paths.db_path),
                "rulesFingerprint": rules.fingerprint,
                "factsCacheStatus": facts.get("factsCacheStatus"),
                "readModelCacheStatus": facts.get("readModelCacheStatus"),
                "dataQualityCacheStatus": quality.get("cacheStatus"),
                "forecastStatus": forecast.get("status"),
                "forecastCache": forecast.get("cache") or {},
                "systemHealthStatus": health.get("status"),
            }
            return ApplicationSnapshot(
                generation_token=generation_token,
                rules=rules,
                facts=facts,
                forecast=forecast,
                quality=quality,
                health=health,
                targets=targets,
                provenance=provenance,
            )
        raise SnapshotGenerationConflict(tuple(observed_tokens))

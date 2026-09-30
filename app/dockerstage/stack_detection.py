"""Stack and language detection (spec §6, Module 3).

Determines primary/secondary language and architecture type using a weighted
scoring system defined in scoring_config.yaml.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

from .models import ParsedDockerfile, ImageRef

CONFIG_PATH = Path(__file__).resolve().parent / "data" / "scoring_config.yaml"


@dataclass
class StackDetectionResult:
    primary_language: str
    secondary_languages: list[str] = field(default_factory=list)
    architecture_type: str = "UNKNOWN"
    scores: dict[str, int] = field(default_factory=dict)
    config_used: dict[str, Any] = field(default_factory=dict)


def _load_config() -> dict[str, Any]:
    with open(CONFIG_PATH, "r", encoding="utf-8") as f:
        return yaml.safe_load(f)


def _score_base_image(base_image: ImageRef | None, weights: dict[str, int], scores: dict[str, int]) -> None:
    if not base_image or not base_image.image_name:
        return
    name = base_image.image_name.lower()
    if name.startswith("python"):
        scores["python"] = scores.get("python", 0) + weights.get("python", 0)
    elif name.startswith("node"):
        scores["node"] = scores.get("node", 0) + weights.get("node", 0)


def _score_run_commands(parsed: ParsedDockerfile, weights: dict[str, int], scores: dict[str, int]) -> None:
    for cmd in parsed.run_commands:
        raw = cmd.raw.lower()
        if "pip install" in raw or "pip3 install" in raw:
            scores["python"] = scores.get("python", 0) + weights.get("pip", 0)
        if "npm install" in raw or "yarn install" in raw:
            scores["node"] = scores.get("node", 0) + weights.get("npm", 0)


def _score_cmd_keyword(parsed: ParsedDockerfile, weights: dict[str, int], scores: dict[str, int]) -> None:
    for instr in (parsed.cmd, parsed.entrypoint):
        if not instr:
            continue
        raw = instr.raw.lower()
        if any(kw in raw for kw in ["python", "gunicorn", "uvicorn", "flask", "streamlit"]):
            scores["python"] = scores.get("python", 0) + weights.get("python", 0)
        if any(kw in raw for kw in ["node", "npm start", "yarn start"]):
            scores["node"] = scores.get("node", 0) + weights.get("node", 0)


def _score_copy_manifest(parsed: ParsedDockerfile, weights: dict[str, int], scores: dict[str, int]) -> None:
    for copy in parsed.copies:
        for src in copy.sources:
            src_lower = src.lower()
            if "requirements.txt" in src_lower or "pyproject.toml" in src_lower or "setup.py" in src_lower:
                scores["python"] = scores.get("python", 0) + weights.get("requirements", 0)
            if "package.json" in src_lower:
                scores["node"] = scores.get("node", 0) + weights.get("package_json", 0)


def detect_architecture(parsed: ParsedDockerfile) -> str:
    """Classify into WEB_API, STATIC_SITE, BATCH_JOB, or UNKNOWN (spec §6)."""
    is_web = bool(parsed.exposed_ports)
    has_web_framework = False
    
    for instr in (parsed.cmd, parsed.entrypoint):
        if not instr:
            continue
        raw = instr.raw.lower()
        if any(kw in raw for kw in ["uvicorn", "gunicorn", "flask", "django", "node", "npm"]):
            has_web_framework = True
            is_web = True
            
    # Check for static site pattern (npm run build followed by nginx)
    has_npm_build = any("npm run build" in cmd.raw.lower() or "yarn build" in cmd.raw.lower() for cmd in parsed.run_commands)
    has_nginx = parsed.base_image and parsed.base_image.image_name and "nginx" in parsed.base_image.image_name.lower()
    
    if has_npm_build and has_nginx:
        return "STATIC_SITE"
        
    if is_web or has_web_framework:
        return "WEB_API"
        
    # If it's not a web API or static site, and has a CMD/ENTRYPOINT, we assume BATCH_JOB
    if parsed.cmd or parsed.entrypoint:
        return "BATCH_JOB"
        
    return "UNKNOWN"


def detect_stack(parsed: ParsedDockerfile, config_override: dict | None = None) -> StackDetectionResult:
    """Run the stack detection heuristics (spec §6)."""
    config = config_override or _load_config()
    weights = config.get("weights", {})
    thresholds = config.get("thresholds", {})
    
    scores: dict[str, int] = {}
    
    _score_base_image(parsed.base_image, weights.get("base_image", {}), scores)
    _score_run_commands(parsed, weights.get("run_command", {}), scores)
    _score_cmd_keyword(parsed, weights.get("cmd_keyword", {}), scores)
    _score_copy_manifest(parsed, weights.get("copy_manifest", {}), scores)
    
    if not scores:
        return StackDetectionResult(
            primary_language="unknown",
            architecture_type=detect_architecture(parsed),
            scores=scores,
            config_used=config
        )
        
    sorted_langs = sorted(scores.items(), key=lambda x: x[1], reverse=True)
    primary = sorted_langs[0][0]
    
    secondary = []
    secondary_threshold = thresholds.get("secondary_language", 5)
    for lang, score in sorted_langs[1:]:
        if score >= secondary_threshold:
            secondary.append(lang)
            
    return StackDetectionResult(
        primary_language=primary,
        secondary_languages=secondary,
        architecture_type=detect_architecture(parsed),
        scores=scores,
        config_used=config
    )

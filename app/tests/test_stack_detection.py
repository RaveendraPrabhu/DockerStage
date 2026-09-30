"""Tests for Module 3 (stack and language detection)."""

from __future__ import annotations

from dockerstage.parser.dockerfile_parser import parse_dockerfile
from dockerstage.stack_detection import detect_stack

from _util import fixture, available_tokenizers  # pyrefly: ignore [missing-import]


def test_python_primary_on_flask():
    for tok in available_tokenizers():
        parsed = parse_dockerfile(fixture("simple_flask", "Dockerfile"), tokenizer=tok)
        res = detect_stack(parsed)
        
        assert res.primary_language == "python"
        assert res.architecture_type == "WEB_API"
        assert res.scores["python"] > 0
        assert "node" not in res.scores or res.scores["node"] == 0


def test_hybrid_python_node():
    for tok in available_tokenizers():
        parsed = parse_dockerfile(fixture("hybrid_python_node", "Dockerfile"), tokenizer=tok)
        res = detect_stack(parsed)
        
        # In the hybrid fixture, we have:
        # Base image: python (10)
        # pip install (5)
        # copy requirements.txt (5)
        # uvicorn in CMD (10)
        # Python total = 30
        
        # npm install (5)
        # package.json (5)
        # Node total = 10
        
        assert res.primary_language == "python"
        assert "node" in res.secondary_languages
        assert res.architecture_type == "WEB_API"


def test_static_site_detection():
    # Let's mock a parsed Dockerfile for static site
    from dockerstage.models import ParsedDockerfile, ImageRef, RunCommand, Stage
    
    parsed = ParsedDockerfile()
    parsed.stages = [Stage(index=0, base_image=ImageRef("nginx:alpine", image_name="nginx"))]
    parsed.run_commands = [RunCommand(raw="npm run build")]
    
    res = detect_stack(parsed)
    assert res.architecture_type == "STATIC_SITE"


def test_batch_job_detection():
    from dockerstage.models import ParsedDockerfile, ShellOrExec
    
    parsed = ParsedDockerfile()
    parsed.cmd = ShellOrExec(raw="python script.py")
    # No exposed ports, no web framework keywords
    
    res = detect_stack(parsed)
    assert res.architecture_type == "BATCH_JOB"


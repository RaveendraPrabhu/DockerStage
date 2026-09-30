"""Dockerfile parsing (spec §4 Module 1).

Tokenization strategy
---------------------
The spec names ``dockerfile-parse`` as the tokenizer. We use it as the PRIMARY
path when importable (this is what runs on the user's machine). Where the library
is absent — offline CI, minimal environments — we fall back to a self-contained
stdlib tokenizer. Both front-ends emit the SAME uniform ``RawInstruction`` stream,
so every downstream extraction (RUN decomposition, base-image decomposition, pip /
apt / apk detection, COPY --from, CMD/ENTRYPOINT/HEALTHCHECK) is identical
regardless of which tokenizer ran. ``ParsedDockerfile.tokenizer_used`` records the
choice for traceability.

This is a deliberate, documented deviation from the spec's single-library
assumption; see app/README.md. It strengthens rather than weakens fidelity: the
primary path is exactly the spec-named library.
"""

from __future__ import annotations

import json
import re
import shlex
from dataclasses import dataclass
from typing import Optional

from ..models import (
    ParsedDockerfile,
    Stage,
    RunCommand,
    SystemPackage,
    PipInstall,
    CopyInstruction,
    ShellOrExec,
)
from ..errors import MissingFromError
from .base_image import decompose_image

# Recognised Dockerfile instruction keywords (spec §4).
_KNOWN_INSTRUCTIONS = {
    "FROM", "RUN", "CMD", "LABEL", "MAINTAINER", "EXPOSE", "ENV", "ADD", "COPY",
    "ENTRYPOINT", "VOLUME", "USER", "WORKDIR", "ARG", "ONBUILD", "STOPSIGNAL",
    "HEALTHCHECK", "SHELL",
}


@dataclass
class RawInstruction:
    """One tokenized instruction: keyword + argument text + 1-indexed line."""

    instruction: str
    value: str
    line: int


# --------------------------------------------------------------------------- #
# Tokenizers
# --------------------------------------------------------------------------- #
def _adapt_structure(structure):
    """Map dockerfile-parse's ``.structure`` (list of dicts with keys
    instruction/value/startline, startline 0-indexed) to RawInstructions.
    Pure and library-free so it is unit-testable anywhere."""
    out = []
    for item in structure:
        instr = (item.get("instruction") or "").upper()
        if instr in ("COMMENT", ""):
            continue
        out.append(
            RawInstruction(
                instruction=instr,
                value=(item.get("value") or ""),
                line=int(item.get("startline", 0)) + 1,  # dfp is 0-indexed
            )
        )
    return out


def _tokenize_with_lib(text: str):
    """Primary path: dockerfile-parse. Raises ImportError if unavailable."""
    from dockerfile_parse import DockerfileParser

    dfp = DockerfileParser()
    dfp.content = text
    return _adapt_structure(dfp.structure)


def _logical_lines(text: str):
    """Yield (start_line_1indexed, joined_text) merging '\\' continuations and
    dropping whole-line comments. Inline '#' is preserved (Docker semantics)."""
    out = []
    buf = []
    start = None
    for idx, raw in enumerate(text.splitlines(), start=1):
        if not buf:
            s = raw.strip()
            if s == "" or s.startswith("#"):
                continue
            start = idx
        else:
            # inside a continuation: skip pure-comment lines
            if raw.strip().startswith("#"):
                continue
        rstripped = raw.rstrip()
        if rstripped.endswith("\\"):
            buf.append(rstripped[:-1])
        else:
            buf.append(raw)
            joined = " ".join(p.strip() for p in buf if p.strip())
            if joined:
                out.append((start, joined))
            buf = []
    if buf:  # trailing unterminated continuation
        joined = " ".join(p.strip() for p in buf if p.strip())
        if joined:
            out.append((start, joined))
    return out


def _tokenize_stdlib(text: str):
    out = []
    for line_no, joined in _logical_lines(text):
        parts = joined.split(None, 1)
        if not parts:
            continue
        instr = parts[0].upper()
        value = parts[1] if len(parts) > 1 else ""
        out.append(RawInstruction(instruction=instr, value=value, line=line_no))
    return out


def _tokenize(text: str, tokenizer: str = "auto"):
    """Return (instructions, tokenizer_used)."""
    if tokenizer in ("auto", "dockerfile-parse"):
        try:
            return _tokenize_with_lib(text), "dockerfile-parse"
        except Exception:
            if tokenizer == "dockerfile-parse":
                raise
    return _tokenize_stdlib(text), "stdlib"


# --------------------------------------------------------------------------- #
# ARG substitution for FROM (spec §4 item 1)
# --------------------------------------------------------------------------- #
_ARG_DEFAULT_RE = re.compile(r"\$\{(\w+):-([^}]*)\}")
_ARG_BRACE_RE = re.compile(r"\$\{(\w+)\}")
_ARG_BARE_RE = re.compile(r"\$(\w+)")


def _subst_args(s: str, argmap: dict) -> str:
    def repl_default(m):
        var, default = m.group(1), m.group(2)
        val = argmap.get(var)
        return val if val not in (None, "") else default

    s = _ARG_DEFAULT_RE.sub(repl_default, s)

    def repl_brace(m):
        val = argmap.get(m.group(1))
        return val if val is not None else m.group(0)

    s = _ARG_BRACE_RE.sub(repl_brace, s)

    def repl_bare(m):
        val = argmap.get(m.group(1))
        return val if val is not None else m.group(0)

    s = _ARG_BARE_RE.sub(repl_bare, s)
    return s


def _parse_from(value: str, argmap: dict):
    toks = [t for t in value.split() if not t.startswith("--")]  # drop --platform=
    image = toks[0] if toks else ""
    alias = None
    if len(toks) >= 3 and toks[1].upper() == "AS":
        alias = toks[2]
    return _subst_args(image, argmap), alias


# --------------------------------------------------------------------------- #
# Shell helpers
# --------------------------------------------------------------------------- #
def _shlex(s: str):
    try:
        return shlex.split(s, posix=True)
    except ValueError:
        return s.split()


def _split_on_operators(s: str):
    """Quote-aware split on && || ; and newlines."""
    parts, cur = [], []
    quote = None
    i, n = 0, len(s)
    while i < n:
        c = s[i]
        if quote:
            cur.append(c)
            if c == quote:
                quote = None
            i += 1
            continue
        if c in ("'", '"'):
            quote = c
            cur.append(c)
            i += 1
            continue
        if s.startswith("&&", i) or s.startswith("||", i):
            parts.append("".join(cur)); cur = []; i += 2; continue
        if c in (";", "\n"):
            parts.append("".join(cur)); cur = []; i += 1; continue
        cur.append(c); i += 1
    if cur:
        parts.append("".join(cur))
    return [p.strip() for p in parts if p.strip()]


# --------------------------------------------------------------------------- #
# Package-manager extraction (spec §4 items 4 & 5)
# --------------------------------------------------------------------------- #
def _clean_apt_name(tok: str):
    if "/" in tok or tok.endswith(".deb"):
        return None  # local file / repo-pin path, not a named package
    name = tok.split("=", 1)[0]      # strip version pin
    name = name.split(":", 1)[0]     # strip :arch (libc6:amd64)
    return name.strip() or None


def _clean_apk_name(tok: str):
    for sep in ("=", ">", "<", "~", "@"):
        tok = tok.split(sep, 1)[0]
    return tok.strip() or None


_APT_VALUE_FLAGS = {"-t", "--target-release", "-o", "--option", "-c", "--config-file"}
_APK_VALUE_FLAGS = {"--virtual", "-t", "--repository", "-X", "--root", "-p", "--arch"}
_PIP_VALUE_FLAGS = {
    "-r", "--requirement", "-c", "--constraint", "-i", "--index-url",
    "--extra-index-url", "--target", "-t", "--prefix", "--root", "--find-links",
    "-f", "--platform", "--python-version", "--abi", "--implementation", "-w",
    "--wheel-dir", "--no-binary", "--only-binary", "--cache-dir", "--log",
    "--proxy", "--retries", "--timeout", "--src", "--upgrade-strategy",
}


def _extract_apt(argv, raw, line, si):
    if "install" not in argv:
        return []
    rest = argv[argv.index("install") + 1:]
    pkgs, i = [], 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("-") and "=" in tok:
            i += 1; continue
        if tok in _APT_VALUE_FLAGS:
            i += 2 if i + 1 < len(rest) else 1; continue
        if tok.startswith("-"):
            i += 1; continue
        name = _clean_apt_name(tok)
        if name:
            pkgs.append(SystemPackage(name=name, pkg_manager="apt", raw=tok, line=line, stage_index=si))
        i += 1
    return pkgs


def _extract_apk(argv, raw, line, si):
    if "add" not in argv:
        return []
    rest = argv[argv.index("add") + 1:]
    pkgs, i = [], 0
    while i < len(rest):
        tok = rest[i]
        if tok in _APK_VALUE_FLAGS:
            i += 2 if i + 1 < len(rest) else 1; continue
        if tok.startswith("-"):
            i += 1; continue
        name = _clean_apk_name(tok)
        if name:
            pkgs.append(SystemPackage(name=name, pkg_manager="apk", raw=tok, line=line, stage_index=si))
        i += 1
    return pkgs


def _pip_install_index(argv):
    """Return (executable_label, index_of_install) or (None, None)."""
    if not argv:
        return None, None
    if argv[0] in ("pip", "pip3") and "install" in argv:
        return argv[0], argv.index("install")
    if argv[0] in ("python", "python3") and "-m" in argv:
        mi = argv.index("-m")
        if mi + 1 < len(argv) and argv[mi + 1] == "pip" and "install" in argv:
            return f"{argv[0]} -m pip", argv.index("install")
    return None, None


def _extract_pip(argv, raw, line, si):
    exe, idx = _pip_install_index(argv)
    if idx is None:
        return None
    rest = argv[idx + 1:]
    req_files, inline, i = [], [], 0
    while i < len(rest):
        tok = rest[i]
        if tok.startswith("-") and "=" in tok:
            flag, _, val = tok.partition("=")
            if flag in ("-r", "--requirement"):
                req_files.append(val)
            i += 1; continue
        if tok in ("-r", "--requirement"):
            if i + 1 < len(rest):
                req_files.append(rest[i + 1]); i += 2
            else:
                i += 1
            continue
        if tok in ("-e", "--editable"):
            i += 2 if i + 1 < len(rest) else 1; continue
        if tok in _PIP_VALUE_FLAGS:
            i += 2 if i + 1 < len(rest) else 1; continue
        if tok.startswith("-"):
            i += 1; continue
        inline.append(tok)
        i += 1
    return PipInstall(
        raw=raw, line=line, pip_executable=exe,
        requirement_files=req_files, inline_packages=inline, stage_index=si,
    )


# --------------------------------------------------------------------------- #
# COPY / ADD, CMD / ENTRYPOINT
# --------------------------------------------------------------------------- #
def _parse_copy(value: str, is_add: bool, line: int, si: int) -> CopyInstruction:
    v = value.strip()
    if v.startswith("["):  # JSON exec form
        try:
            arr = json.loads(v)
            if isinstance(arr, list) and arr:
                dest = str(arr[-1]) if len(arr) >= 2 else None
                sources = [str(x) for x in arr[:-1]] if len(arr) >= 2 else [str(arr[0])]
                return CopyInstruction(sources=sources, dest=dest, is_add=is_add, line=line, stage_index=si)
        except Exception:
            pass
    from_stage = chown = None
    positional = []
    for t in _shlex(v):
        if t.startswith("--from="):
            from_stage = t.split("=", 1)[1]
        elif t.startswith("--chown="):
            chown = t.split("=", 1)[1]
        elif t.startswith("--"):
            continue
        else:
            positional.append(t)
    if len(positional) >= 2:
        dest, sources = positional[-1], positional[:-1]
    elif positional:
        dest, sources = None, positional
    else:
        dest, sources = None, []
    return CopyInstruction(sources=sources, dest=dest, from_stage=from_stage,
                           chown=chown, is_add=is_add, line=line, stage_index=si)


def _shell_or_exec(value: str) -> ShellOrExec:
    v = value.strip()
    if v.startswith("["):
        try:
            arr = json.loads(v)
            if isinstance(arr, list):
                return ShellOrExec(raw=value, form="exec", argv=[str(x) for x in arr])
        except Exception:
            pass
    return ShellOrExec(raw=value, form="shell", argv=None)


def _parse_env(value: str, env: dict):
    v = value.strip()
    if "=" in v:
        for t in _shlex(v):
            if "=" in t:
                k, _, val = t.partition("=")
                env[k] = val
    else:
        parts = v.split(None, 1)
        if len(parts) == 2:
            env[parts[0]] = parts[1]
        elif len(parts) == 1:
            env[parts[0]] = ""


# --------------------------------------------------------------------------- #
# Top-level parse
# --------------------------------------------------------------------------- #
def parse_dockerfile_text(text: str, path: Optional[str] = None,
                          tokenizer: str = "auto") -> ParsedDockerfile:
    parsed = ParsedDockerfile(path=path, raw_text=text)
    instructions, used = _tokenize(text, tokenizer)
    parsed.tokenizer_used = used

    if not instructions:
        parsed.warnings.append("empty-dockerfile")
        return parsed

    if not any(r.instruction == "FROM" for r in instructions):
        raise MissingFromError(
            f"Dockerfile has {len(instructions)} instruction(s) but no FROM"
            + (f" ({path})" if path else "")
        )

    global_args: dict = {}      # ARGs before first FROM (usable in FROM)
    seen_first_from = False
    current_si = -1
    aliases: dict = {}          # alias -> stage index

    for r in instructions:
        instr, value, line = r.instruction, r.value, r.line

        if instr not in _KNOWN_INSTRUCTIONS:
            parsed.warnings.append(f"line {line}: unknown instruction {instr!r}")
            continue

        if instr == "ARG":
            name, default = (value.split("=", 1) + [None])[:2] if "=" in value else (value.strip(), None)
            name = name.strip()
            if not seen_first_from:
                global_args[name] = default
            else:
                parsed.args[name] = default
            continue

        if instr == "FROM":
            image, alias = _parse_from(value, global_args)
            img = decompose_image(image)
            if image in aliases:  # FROM references an earlier build stage
                parsed.warnings.append(f"line {line}: FROM references build stage {image!r}")
            si = len(parsed.stages)
            parsed.stages.append(Stage(index=si, base_image=img, alias=alias, from_line=line))
            if alias:
                aliases[alias] = si
            current_si = si
            seen_first_from = True
            continue

        if not seen_first_from:
            parsed.warnings.append(f"line {line}: {instr} before FROM ignored")
            continue

        if instr == "RUN":
            subcmds = _split_on_operators(value)
            parsed.run_commands.append(RunCommand(raw=value, subcommands=subcmds, line=line, stage_index=current_si))
            for sub in subcmds:
                argv = _shlex(sub)
                if not argv:
                    continue
                head = argv[0]
                if head in ("apt-get", "apt"):
                    parsed.system_packages.extend(_extract_apt(argv, sub, line, current_si))
                elif head == "apk":
                    parsed.system_packages.extend(_extract_apk(argv, sub, line, current_si))
                pip = _extract_pip(argv, sub, line, current_si)
                if pip is not None:
                    parsed.pip_installs.append(pip)

        elif instr in ("COPY", "ADD"):
            parsed.copies.append(_parse_copy(value, is_add=(instr == "ADD"), line=line, si=current_si))

        elif instr == "ENV":
            _parse_env(value, parsed.env)

        elif instr == "WORKDIR":
            parsed.workdir = value.strip()

        elif instr == "EXPOSE":
            parsed.exposed_ports.extend(value.split())

        elif instr == "USER":
            parsed.user = value.strip()

        elif instr == "CMD":
            parsed.cmd = _shell_or_exec(value)

        elif instr == "ENTRYPOINT":
            parsed.entrypoint = _shell_or_exec(value)

        elif instr == "HEALTHCHECK":
            parsed.healthcheck = value.strip()

    parsed.is_multistage = len(parsed.stages) > 1
    _refine_libc(parsed)
    return parsed


def _refine_libc(parsed: ParsedDockerfile):
    """Second pass (spec §4a detection source): refine libc from observed package
    managers when the base image tag was inconclusive; flag conflicts otherwise."""
    for stage in parsed.stages:
        pkgs = [p for p in parsed.system_packages if p.stage_index == stage.index]
        has_apk = any(p.pkg_manager == "apk" for p in pkgs)
        has_apt = any(p.pkg_manager == "apt" for p in pkgs)
        img = stage.base_image
        if img.libc_family == "unknown":
            if has_apk:
                img.libc_family = "musl"
                if img.distro_family == "unknown":
                    img.distro_family = "alpine"
                img.libc_detection_source = "apk_command"
            elif has_apt:
                img.libc_family = "glibc"
                if img.distro_family == "unknown":
                    img.distro_family = "debian"
                img.libc_detection_source = "apt_command"
        else:
            if img.libc_family == "glibc" and has_apk and not has_apt:
                parsed.warnings.append(f"stage {stage.index}: glibc base but apk usage seen")
            if img.libc_family == "musl" and has_apt and not has_apk:
                parsed.warnings.append(f"stage {stage.index}: musl base but apt usage seen")


def parse_dockerfile(path: str, tokenizer: str = "auto") -> ParsedDockerfile:
    with open(path, "r", encoding="utf-8") as fh:
        text = fh.read()
    return parse_dockerfile_text(text, path=path, tokenizer=tokenizer)


def parse_many(paths, tokenizer: str = "auto"):
    """Batch parse; one malformed file does not abort the run (spec §4)."""
    results = []
    for p in paths:
        try:
            results.append({"path": p, "parsed": parse_dockerfile(p, tokenizer=tokenizer), "error": None})
        except Exception as e:
            results.append({"path": p, "parsed": None, "error": f"{type(e).__name__}: {e}"})
    return results

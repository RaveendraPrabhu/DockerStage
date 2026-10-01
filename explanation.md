# DockerStage: System Architecture & Presentation Guide

If you are presenting this project to a panel, professor, or team, this document is designed to help you explain the system architecture confidently. It breaks down the system not just by *what* the code does, but *why* it was designed this way.

---

## 1. The Elevator Pitch (The "Why")
**The Problem:** Most developers write single-stage `Dockerfiles` for Python and ML projects. These are easy to write but result in massive, slow, and insecure images because they contain build tools (like compilers) that are only needed during installation, not at runtime.

**The Solution:** DockerStage is an automated refactoring tool. It reads a simple single-stage Dockerfile, deeply understands the Python dependencies, and rewrites it into a highly optimized, multi-stage Dockerfile. This strips out compilers and reduces the final image size dramatically without breaking the app.

---

## 2. Core Architectural Philosophy
When presenting the code, emphasize these three design principles:
1.  **Strict Data Models over Raw Strings:** Instead of using error-prone regular expressions to modify text, the system parses everything into strict Python objects (Dataclasses).
2.  **Data is Decoupled from Code:** The logic of *how* to build is separate from the *knowledge* of what to build. We use external JSON and YAML files for configuration and knowledge.
3.  **Modular Pipeline:** The project is split into independent phases. Phase 1 only reads. Phase 2 only stores knowledge. Phase 3 only detects context.

---

## 3. Module 1: The Parsing Engine (Phase 1)
*This is the "Data Extraction" phase.*

**How to explain it:** 
"Before we can optimize a Dockerfile, we need to understand it. In Phase 1, we built a parsing engine that reads the user's project and converts unstructured text into strict Python `Dataclasses`."

*   **`dockerfile_parser.py`**: We use a library to safely read the Dockerfile line-by-line. We extract `RUN` commands, `COPY` commands, and environment variables, packaging them into a `ParsedDockerfile` object.
*   **`base_image.py`**: We take the `FROM` line (e.g., `python:3.11-slim`) and decompose it. We extract the OS family (Debian vs Alpine) and the Python version. This is critical because a package builds differently on Alpine than on Debian.
*   **`manifest_parser.py`**: We scan the user's directory for `requirements.txt` or `pyproject.toml`. We parse these files using standard Python packaging libraries to get an exact list of the Python packages the user wants to install.

---

## 4. Module 2: The Knowledge Base (Phase 2)
*This is the "Brain" of the system.*

**How to explain it:** 
"Standard Python tools (like `pip`) know how to install Python packages, but they don't know what underlying Linux C-libraries are required to compile them. For example, `pip` doesn't know that installing `psycopg2` requires the Ubuntu library `libpq-dev`. We had to build a 'Brain' to bridge this gap."

*   **The Database (`knowledge_base.json`)**: We built a manually curated database of 119 popular Python packages (numpy, pandas, opencv). For each, we documented the Linux packages needed to *build* it versus the Linux packages needed to *run* it.
*   **Schema Validation (`knowledge_base.py`)**: To ensure no human error corrupts the database, we wrote a strict validator. It enforces naming conventions and checks for missing data.
*   **Automated CI Testing**: We don't just assume our database is right. We built tools (`verify_wheels.py` and `verify_imports.py`) that run in GitHub Actions. Every time we update the code, GitHub automatically pings the PyPI API and spins up real Docker containers to test if our dependency mappings actually work in the real world.

---

## 5. Module 3: Stack Detection (Phase 3)
*This is the "Context Awareness" phase.*

**How to explain it:** 
"Real-world projects are messy. A repository might be a pure Python script, or it might be a hybrid Web App that uses Node.js for the frontend. We built a Stack Detection engine to figure out what kind of app we are looking at."

*   **Config-Driven Scoring (`scoring_config.yaml`)**: We avoided hardcoding rules. Instead, we created a YAML file with a weighted scoring system. For example, seeing `npm install` awards 5 points to Node.js, while seeing `uvicorn` in the start command awards 10 points to Python.
*   **`stack_detection.py`**: This script tallies up the score. It identifies the **Primary Language** (the core of the app) and any **Secondary Languages** (like a Node.js build step that can be safely deleted in the final multi-stage image). 
*   **Architecture Detection**: It checks the network ports (`EXPOSE`) and start commands (`CMD`) to classify the app as a `WEB_API`, a `STATIC_SITE`, or a `BATCH_JOB`.

---

## 💡 Key Talking Points for Your Presentation
If you are asked questions by a panel, use these points to defend the architecture:

*   **"Why didn't you just use Regex to parse the Dockerfile?"**
    *   *Answer:* "Regex is fragile. Dockerfiles have line continuations (`\`), comments, and complex shell commands. By parsing everything into strict `Dataclasses` (defined in `models.py`), the rest of the pipeline is guaranteed to work with clean, structured data."
*   **"How do you maintain the Knowledge Base?"**
    *   *Answer:* "It's separated from the Python code as a JSON file. This means non-developers can update the database without touching the application logic. Furthermore, our GitHub Actions CI pipeline automatically tests the database in real containers, ensuring it never breaks."
*   **"Why use a scoring system for Stack Detection instead of a simple IF statement?"**
    *   *Answer:* "Because modern apps are hybrid. A Dockerfile might have a `FROM node` base image but actually be a Python app that just needed Node for a single frontend build step. By using a weighted multi-signal scoring system (reading the base image, RUN commands, and CMDs), we get a much more accurate classification."

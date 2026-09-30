# DockerStage Project: Comprehensive Architecture & Flow (Phases 1-3)

This document provides a deep, step-by-step technical explanation of the entire DockerStage project from the very beginning up to the completion of Phase 3. It explains the exact flow of data, every major file, what it takes as input, what it outputs, and how it connects to the next step.

---

## 1. The Big Picture Goal
The ultimate goal of this project is to take a user's slow, bulky, single-stage `Dockerfile` (typically for a Python or Machine Learning project) and automatically rewrite it into a highly optimized, multi-stage `Dockerfile`. 

To do this safely, the system cannot just guess. It must rigorously read the original file, understand exactly what dependencies are needed, figure out what underlying Linux C-libraries those dependencies require, and determine the architecture of the app.

---

## 2. The Core Data Models (`app/dockerstage/models.py`)
Before explaining the functions, we must understand the "Data Models". Instead of passing raw strings around (which is prone to bugs), every phase of our code communicates using strict Python objects.
*   **`ParsedDockerfile`**: The master object representing the user's entire Dockerfile. It holds lists of commands.
*   **`ImageRef`**: Represents a base image (like `python:3.11-slim`).
*   **`RunCommand`, `PipInstall`, `CopyInstruction`**: Objects representing specific lines in the Dockerfile.
*   **`Requirement`**: Represents a single Python package requested by the user (e.g., `pandas>=1.0`).

---

## 3. Phase 1: The Parsing Layer (Extraction)
Phase 1 is the "Reading" phase. It looks at the user's raw files and converts them into the Data Models mentioned above.

### A. Parsing the Dockerfile (`app/dockerstage/parser/dockerfile_parser.py`)
*   **What it is:** The entry point for the tool.
*   **Input:** The raw text of the user's `Dockerfile`.
*   **What it does:** It uses a third-party library (`dockerfile-parse`) to safely read the Dockerfile line-by-line, handling edge cases like line continuations (`\`) and comments.
*   **Output:** It groups instructions into `Stage` objects and returns a massive `ParsedDockerfile` object containing categorized lists of `RUN`, `COPY`, `ENV`, and `CMD` instructions.

### B. Analyzing the Base Image (`app/dockerstage/parser/base_image.py`)
*   **What it is:** A specialized parser just for the `FROM` line.
*   **Input:** The raw string from the Dockerfile, e.g., `"python:3.11-slim"`.
*   **What it does:** It splits the string into its registry, image name (`python`), tag (`3.11-slim`), and architecture variant (`slim`). 
*   **Output:** An `ImageRef` object. This allows later phases to ask questions like: *"Is this an Alpine or Debian image?"* or *"What version of Python is this?"*

### C. Parsing the Python Dependencies (`app/dockerstage/parser/manifest_parser.py`)
*   **What it is:** The parser that finds out what Python packages the user actually wants.
*   **Input:** The user's project directory.
*   **What it does:** 
    1.  **`detect_tier2_manifest()`**: Scans the directory to find `requirements.txt`, `pyproject.toml`, or `setup.py`.
    2.  **`parse_requirements_file()`**: Reads the found file using Python's official `packaging` library. It strips out comments, standardizes package names (turning `Flask` into `flask`), and parses version constraints (e.g., `>=2.0`).
*   **Output:** A `Tier2Manifest` object containing a clean list of `Requirement` objects.

---

## 4. Phase 2: The Knowledge Base (The Brain)
Phase 1 told us *what* the user wants to install (e.g., `psycopg2`). But standard Python tools do not know that `psycopg2` requires the Ubuntu system library `libpq-dev` to compile. Phase 2 builds a database to map Python packages to their required Linux system libraries.

### A. The Database (`app/dockerstage/data/knowledge_base.json`)
*   **What it is:** A massive, manually curated JSON file containing data on 119 of the most popular Python packages (numpy, scipy, pandas, opencv-python, etc.).
*   **What it holds:** For every package, it lists:
    *   `build_time_system_deps`: Ubuntu/Alpine packages needed to *compile* the Python package (e.g., `gcc`).
    *   `runtime_system_deps`: Ubuntu/Alpine packages needed to *run* the app (e.g., `libgomp1`).
    *   `import_names`: What you type in Python to use it (e.g., `import cv2`).

### B. Validating the Database (`app/dockerstage/knowledge_base.py`)
*   **What it is:** The script that loads and strictly enforces the rules of the JSON database.
*   **Input:** The `knowledge_base.json` file.
*   **What it does:** It iterates through all 119 entries and ensures that no human made a typo. It enforces PEP-503 naming standards, ensures no duplicates exist, and verifies all required keys are present according to our formal schema (`kb_schema.json`).
*   **Output:** A clean Python dictionary of the database that later phases can instantly query.

### C. Proving the Database works (The CI Tools)
We had to build tools to *prove* our JSON mappings were actually correct in the real world.
*   **`app/tools/verify_wheels.py`**: Loops through the database, makes live network requests to the Python Package Index (PyPI), and checks if pre-compiled versions (wheels) actually exist for each package.
*   **`app/tools/verify_imports.py`**: Spins up a real, temporary Docker container (`python:3.11-slim`), installs the system dependencies we listed in the JSON via `apt-get`, installs the Python package via `pip`, and tries to run `import [name]`. If it crashes, it means our database is missing a required Linux library.

### D. Continuous Integration (`.github/workflows/kb.yml`)
*   **What it is:** The automated pipeline on GitHub.
*   **What it does:** Every time we push code to GitHub, GitHub automatically spins up a clean Ubuntu server. It runs our unit tests (`pytest`), runs the schema validator, and runs both `verify_wheels.py` and `verify_imports.py`. If any of these fail, the pipeline turns red, preventing us from merging bad code. (As of right now, it is perfectly green!).

---

## 5. Phase 3: Stack and Architecture Detection
Now that we can parse the files (Phase 1) and look up dependencies (Phase 2), we need to understand the *context* of the user's application. Is it a pure Python app? Is it a hybrid app with a Node.js frontend? Is it a web server or a background script?

### A. The Scoring Configuration (`app/dockerstage/data/scoring_config.yaml`)
*   **What it is:** A configuration file that assigns point values to different "clues".
*   **Why it exists:** We cannot hardcode rules in the logic. By placing the weights in a YAML file, developers can easily tweak the sensitivity of the detection engine without rewriting code.
*   **Example Clues:** Seeing `python:3.11` as a base image gives 10 points to Python. Seeing `npm install` gives 5 points to Node.js. Seeing `requirements.txt` being copied gives 5 points to Python.

### B. The Detection Engine (`app/dockerstage/stack_detection.py`)
*   **Input:** The `ParsedDockerfile` object from Phase 1.
*   **What it does:** 
    1.  **Language Detection**: It scans the base image, the `RUN` commands, the files being `COPY`'d, and the `CMD` keywords. It tallies up the scores based on the `scoring_config.yaml`.
    2.  **Primary vs Secondary**: The language with the highest score is declared the `primary_language`. If another language (like Node.js) scores above a specific threshold (e.g., 5 points), it is flagged as a `secondary_language`. This tells future phases: *"Hey, this app uses Node.js just to build the frontend, you can delete Node.js in the final runtime image to save space!"*
    3.  **Architecture Detection**: It looks at the `EXPOSE` command (network ports) and `CMD` keywords. 
        *   If it sees `EXPOSE 80` or `uvicorn`/`flask`, it classifies it as a `WEB_API`.
        *   If it sees `npm run build` followed by copying to an `nginx` server, it classifies it as a `STATIC_SITE`.
        *   If it just runs a python script with no ports exposed, it classifies it as a `BATCH_JOB`.
*   **Output:** A `StackDetectionResult` object containing all of these answers.

---

## 6. Putting It All Together: The Flow So Far
If a user passes their Dockerfile to our tool today, here is exactly what happens:

1.  `dockerfile_parser.py` reads the text and creates a structured `ParsedDockerfile` object.
2.  `base_image.py` breaks down the `FROM python:3.11-slim` line so we know exactly what OS and Python version we are working with.
3.  `manifest_parser.py` finds their `requirements.txt` and creates a clean list of what Python packages they need.
4.  `stack_detection.py` analyzes the `ParsedDockerfile` and determines: *"This is a Python WEB_API, but it also has a Node.js secondary stack."*
5.  With all of this context gathered, the system is now perfectly primed to consult the `knowledge_base.json` (Phase 2) to figure out exactly how to build the optimized multi-stage Dockerfile (which we will begin building in Phase 4!).

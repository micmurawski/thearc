# Install

The core package requires Python 3.10 or newer. Optional runtime SDKs may require
a newer Python version. Start from a source checkout:

```sh
git clone https://github.com/micmurawski/thearc.git
cd thearc
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
thearc --help
```

On Windows PowerShell, activate with `.venv\Scripts\Activate.ps1` instead.
Run tutorial and cookbook commands from the repository root.

## Decide whether you need a runtime

Indexing logs, creating evidence, inspecting saved results, and installing
configuration do not call a model. Live reflection and curation do. Choose a
backend and configure it using [runtime setup](../reference/runtimes.md).
Neither reflection nor curation CLI chooses a provider for you.

## Prepare your inputs

For the first example you need an existing project with agent configuration and
native session logs relevant to that configuration. Use your own project; the
FastAPI fixture and previous experiment logs are not prerequisites.

Continue with [your first ACE run](first-ace.md).

For Graphify-specific tests and recipes, initialize the optional fixture:

```sh
git submodule update --init --recursive
```

That fixture is a separate repository under `tests/fastapi`. Some experiment
recipes also expect locally captured logs that are not supplied by a fresh clone.

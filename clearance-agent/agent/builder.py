from __future__ import annotations
import yaml
from pathlib import Path

# Agents SDK (Python)
from agents import Agent, Runner, tool

from .tools import read_text, write_text, run_cmd

REPO_ROOT = Path(__file__).resolve().parents[1]

@tool
def fs_read(path: str) -> str:
    return read_text(path)

@tool
def fs_write(path: str, content: str) -> str:
    return write_text(path, content)

@tool
def sh(cmd: str) -> str:
    return run_cmd(cmd)

def load_spec() -> dict:
    spec_path = REPO_ROOT / "spec" / "clearance_spec.yaml"
    return yaml.safe_load(spec_path.read_text(encoding="utf-8"))

def make_agents():
    data_agent = Agent(
        name="DataEngineer",
        instructions=(
            "Implement data schema helpers and feature engineering per spec. "
            "Do NOT implement clearance optimization here."
        ),
        tools=[fs_read, fs_write, sh],
    )

    elasticity_agent = Agent(
        name="ElasticityEngineer",
        instructions=(
            "Implement elasticity estimation per spec. "
            "Prefer a pooled fixed-effects approach with SKU and store effects. "
            "Write unit tests for sanity (sign of elasticity, fallback logic)."
        ),
        tools=[fs_read, fs_write, sh],
    )

    clearance_agent = Agent(
        name="ClearanceEngineer",
        instructions=(
            "Implement clearance schedule generator that uses elasticity outputs. "
            "Objective: minimize margin loss subject to max duration and discount ladder. "
            "Start with a single-SKU planner (no transfers). Add tests."
        ),
        tools=[fs_read, fs_write, sh],
    )

    app_agent = Agent(
        name="AppEngineer",
        instructions=(
            "Implement a Streamlit app that loads a CSV, runs elasticity, "
            "and can generate a clearance plan for a selected SKU."
        ),
        tools=[fs_read, fs_write, sh],
    )

    qa_agent = Agent(
        name="QA",
        instructions=(
            "Run pytest, read failures, and propose minimal fixes. "
            "Never change business logic without updating tests."
        ),
        tools=[fs_read, fs_write, sh],
    )

    orchestrator = Agent(
        name="Orchestrator",
        instructions=(
            "You coordinate the build. Read the spec. "
            "Ask sub-agents to implement modules. "
            "After each change, run tests and fix until green."
        ),
        tools=[fs_read, fs_write, sh],
        handoffs=[data_agent, elasticity_agent, clearance_agent, app_agent, qa_agent],
    )

    return orchestrator

def main():
    spec = load_spec()
    orchestrator = make_agents()

    prompt = f"""
    Build the repo modules per spec below.
    - Create clearance_tool/ modules and tests/.
    - Use statsmodels for fixed effects (or equivalent).
    - Provide a CLI entry in README with commands.
    Spec: {spec}
    """

    result = Runner.run_sync(orchestrator, prompt)
    print(result.final_output)

if __name__ == "__main__":
    main()
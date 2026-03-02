# CLAUDE.md

This file provides guidance to Claude Code when working in this repository.

## Repository Overview

A multi-agent system built on the Anthropic Agents SDK (Python) that automatically generates ice cream clearance schedules for Beijing chain stores using price elasticity modeling.

The project coordinates five specialist sub-agents (DataEngineer, ElasticityEngineer, ClearanceEngineer, AppEngineer, QA) via a central Orchestrator. It uses a pooled fixed-effects elasticity model to optimize clearance pricing with the objective of minimizing margin loss.

Key components:
- `clearance-agent/agent/` — Agent definitions and orchestration
- `clearance-agent/clearance_tool/` — Core Python modules (data, elasticity, planner, pdf_export)
- `clearance-agent/app.py` — Streamlit frontend
- `clearance-agent/spec/clearance_spec.yaml` — Business rules and configuration

## Development Commands

```bash
# Install dependencies
pip install -r clearance-agent/requirements.txt

# Run the Streamlit app
cd clearance-agent && streamlit run app.py

# Run the orchestrator agent
cd clearance-agent && python -m agent.builder

# Run tests
cd clearance-agent && pytest

# Run linter
cd clearance-agent && flake8 .
```

## Code Style

- Follow the conventions already present in the codebase
- Keep functions small and focused
- Write clear, descriptive variable and function names

## Git Workflow

- Branch names should be descriptive and start with `claude/` for Claude Code sessions
- Write clear, concise commit messages
- Do not push directly to the default branch

## Notes for Claude

- Always read files before modifying them
- Prefer editing existing files over creating new ones
- Avoid over-engineering — keep solutions simple and focused
- Run tests after making changes to verify correctness

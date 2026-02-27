# Clearance Agent

A multi-agent system built on the Anthropic Agents SDK (Python) that automatically generates ice cream clearance schedules for Beijing chain stores using price elasticity modeling.

## Project Structure

```
clearance-agent/
├── agent/
│   ├── builder.py      # Agent definitions and orchestration entry point
│   └── tools.py        # Filesystem and shell tool helpers
└── spec/
    └── clearance_spec.yaml  # Project configuration and business rules
```

## Spec (`spec/clearance_spec.yaml`)

| Parameter | Value |
|---|---|
| Category | Ice cream |
| Region | Beijing |
| Price scope | Chain-wide |
| Elasticity model | Pooled fixed effects (SKU + store effects) |
| Min distinct prices for SKU elasticity | 3 (falls back to category) |
| Clearance objective | Minimize margin loss |
| Margin definition | `selling_price - buying_price` |
| Allowed discount levels | 0%, 10%, 20%, 30%, 40%, 50% |
| Step interval | 7 days |
| Max clearance duration | 8 weeks |

## Agent Architecture

The orchestrator (`Orchestrator`) coordinates five specialist sub-agents via handoffs:

- **DataEngineer** — data schema helpers and feature engineering
- **ElasticityEngineer** — pooled fixed-effects elasticity estimation with unit tests
- **ClearanceEngineer** — single-SKU clearance schedule generator using elasticity outputs
- **AppEngineer** — Streamlit app (load CSV → run elasticity → generate clearance plan)
- **QA** — runs pytest, reads failures, proposes minimal fixes

All agents share the same three tools:
- `fs_read(path)` — read a file within the repo
- `fs_write(path, content)` — write a file within the repo
- `sh(cmd)` — run a shell command in the repo root

## Required Data Columns

`date`, `store_id`, `sku_id`, `product_name`, `product_status`, `buying_price`, `selling_price`, `units_sold`

## Running the Orchestrator

```bash
python -m agent.builder
```

The orchestrator reads the spec and instructs sub-agents to build:
- `clearance_tool/` — Python modules (data, elasticity, clearance planner)
- `tests/` — pytest test suite

## Key Conventions

- Use `statsmodels` for fixed-effects estimation (or equivalent)
- Elasticity sign must be negative; fallback to category elasticity when fewer than 3 distinct prices exist for a SKU
- Never change business logic without updating tests
- All file paths are validated to stay within the repo root (`tools.py:_safe_path`)

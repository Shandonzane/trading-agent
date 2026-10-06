"""Strategy specs: a small, safe rule language that both humans and the video learner write.

A spec is JSON. Rules compare two operands, e.g. "close crosses_above sma(50)".
No code is ever eval'd, so an LLM-written spec can't run arbitrary code.
"""
import json
from pathlib import Path
from typing import Literal, Optional

import pandas as pd
from pydantic import BaseModel, Field

from .indicators import INDICATORS, compute

Op = Literal[">", "<", ">=", "<=", "crosses_above", "crosses_below"]


class Operand(BaseModel):
    ind: Optional[str] = Field(None, description=f"Indicator name, one of: {', '.join(INDICATORS)}")
    period: Optional[int] = None
    std: Optional[float] = None
    value: Optional[float] = Field(None, description="A constant number, used instead of ind")


class Condition(BaseModel):
    left: Operand
    op: Op
    right: Operand


class RuleSet(BaseModel):
    mode: Literal["all", "any"] = "all"
    conditions: list[Condition]


class Evidence(BaseModel):
    rule: str = Field(description="Which part of the spec this supports, e.g. 'entry', 'exit', 'stop_loss'")
    quote: str = Field(description="Verbatim (or near-verbatim) transcript quote the rule came from")


class StrategySpec(BaseModel):
    name: str
    description: str = ""
    source_url: Optional[str] = None
    symbols: list[str] = ["SPY"]
    direction: Literal["long"] = "long"
    entry: RuleSet
    exit: Optional[RuleSet] = None
    stop_loss_pct: Optional[float] = Field(None, description="e.g. 0.05 for a 5% stop below entry")
    take_profit_pct: Optional[float] = None
    max_hold_days: Optional[int] = None
    position_size_pct: float = 0.10
    complete: bool = Field(True, description="False when the source left out entry, exit or risk rules")
    missing: list[str] = Field(default_factory=list, description="What the source did not specify")
    assumptions: list[str] = Field(default_factory=list, description="Defaults filled in that the source did not say")
    evidence: list[Evidence] = Field(default_factory=list)


def _series(df: pd.DataFrame, o: Operand) -> pd.Series:
    if o.value is not None and o.ind is None:
        return pd.Series(o.value, index=df.index, dtype=float)
    if o.ind is None:
        raise ValueError("Operand needs ind or value")
    return compute(df, o.ind, o.period, o.std).astype(float)


def _eval_cond(df: pd.DataFrame, c: Condition) -> pd.Series:
    a, b = _series(df, c.left), _series(df, c.right)
    if c.op == ">":
        r = a > b
    elif c.op == "<":
        r = a < b
    elif c.op == ">=":
        r = a >= b
    elif c.op == "<=":
        r = a <= b
    elif c.op == "crosses_above":
        r = (a > b) & (a.shift() <= b.shift())
    else:
        r = (a < b) & (a.shift() >= b.shift())
    return r.fillna(False)


def evaluate(df: pd.DataFrame, rules: Optional[RuleSet]) -> pd.Series:
    """Boolean Series, True on bars (at close) where the rule set fires."""
    if rules is None or not rules.conditions:
        return pd.Series(False, index=df.index)
    parts = [_eval_cond(df, c) for c in rules.conditions]
    out = parts[0]
    for p in parts[1:]:
        out = (out & p) if rules.mode == "all" else (out | p)
    return out


def load_spec(path: str | Path) -> StrategySpec:
    return StrategySpec.model_validate(json.loads(Path(path).read_text()))


def save_spec(spec: StrategySpec, path: str | Path) -> None:
    Path(path).write_text(spec.model_dump_json(indent=2, exclude_none=True))

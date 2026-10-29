"""Agent base class: every pipeline stage is an Agent with typed input and output contracts.

The orchestrator only talks to stages through these contracts, which is what lets different
people/agents (Moodle, Doodle, Poodle) own different stages independently.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any, ClassVar, Generic, Optional, TypeVar

from pydantic import BaseModel

I = TypeVar("I", bound=BaseModel)
O = TypeVar("O", bound=BaseModel)


class Agent(ABC, Generic[I, O]):
    stage: ClassVar[str]
    owner: ClassVar[str]                 # who owns the logic: Moodle / Doodle / Poodle
    input_model: ClassVar[type[BaseModel]]
    output_model: ClassVar[type[BaseModel]]

    @property
    def name(self) -> str:
        return type(self).__name__

    def __call__(self, inp: I) -> O:
        inp = self.input_model.model_validate(inp)            # contract check on the way in
        out = self.run(inp)  # type: ignore[arg-type]
        return self.output_model.model_validate(out)          # and on the way out  # type: ignore[return-value]

    @abstractmethod
    def run(self, inp: I) -> O: ...

    def summarize_input(self, inp: I) -> dict[str, Any]:
        return {}

    def summarize_output(self, out: O) -> dict[str, Any]:
        return {}

    def verdict(self, out: O) -> Optional[str]:
        return None

"""Triggers: actions out.

A trigger is anything the agent can *do* — print a card, append to a log,
pulse a relay over a serial line, call an actuator's CLI. The agent fires
every one of them through one contract (:class:`Trigger`): a name, a
description the LLM reads, a JSON schema for the input, and ``fire()``.

The LLM never fires a trigger. It *proposes* one with an input; the agent
validates the input against the schema and fires it. Write a trigger as a
class or a plain function (see :mod:`base`), ship it as a file next to the
profile or as a module, and list it under ``"triggers"`` in the profile.
This package imports nothing else in loquor.
"""

from loquor.triggers.base import FunctionTrigger, Trigger, TriggerError
from loquor.triggers.builtin import Command, Console

BUILTIN_TRIGGERS = {
    "console": Console,
    "command": Command,
}

__all__ = ["BUILTIN_TRIGGERS", "Command", "Console", "FunctionTrigger", "Trigger", "TriggerError"]

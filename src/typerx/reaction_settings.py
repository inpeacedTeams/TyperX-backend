"""Serializable reaction settings and a frontend-ready JSON Schema.

No transport/UI dependencies; settings are applied when a conversation starts.
"""
from __future__ import annotations

import math
import re
from dataclasses import asdict, dataclass, fields


@dataclass(frozen=True, slots=True)
class ReactionSettings:
    numeric_enabled: bool = True
    numeric_reply_only: bool = True
    numeric_max_digits: int = 12
    numeric_verbs: tuple[str, ...] = ("напиши", "повтори")
    semantic_enabled: bool = True
    semantic_reply_only: bool = False
    semantic_phrases: tuple[str, ...] = (
        "ты с читами", "ты с софтом", "нейронка", "гейронка", "автотайпер", "ты бот",
    )
    model: str = ""
    prompt: str = "Кратко ответь на последнее обращение в стиле основного пресета. Не отрицай использование автоматизации, если это неправда."
    max_tokens: int = 80
    context_messages: int = 12
    timeout_seconds: float = 8.0
    queue_capacity: int = 32
    on_error: str = "resume"

    @classmethod
    def from_dict(cls, raw: dict) -> ReactionSettings:
        if not isinstance(raw, dict):
            raise ValueError("reactions must be an object")
        unknown = set(raw) - {f.name for f in fields(cls)}
        if unknown:
            raise ValueError("Unknown reaction setting")
        values = dict(raw)
        for key in ("numeric_verbs", "semantic_phrases"):
            if key in values:
                if not isinstance(values[key], (list, tuple)):
                    raise ValueError(f"{key} must be an array")
                values[key] = tuple(values[key])
        return cls(**values).validate()

    def validate(self) -> ReactionSettings:
        for key in ("numeric_enabled", "numeric_reply_only", "semantic_enabled", "semantic_reply_only"):
            if type(getattr(self, key)) is not bool:
                raise ValueError(f"{key} must be boolean")
        for key, lo, hi in (("numeric_max_digits", 1, 12), ("max_tokens", 16, 256),
                            ("context_messages", 1, 50), ("queue_capacity", 1, 256)):
            value = getattr(self, key)
            if type(value) is not int or not lo <= value <= hi:
                raise ValueError(f"{key} is outside its allowed range")
        for key in ("numeric_verbs", "semantic_phrases"):
            values = getattr(self, key)
            if not isinstance(values, tuple) or not 1 <= len(values) <= 30:
                raise ValueError(f"{key} must contain 1..30 strings")
            if any(not isinstance(v, str) or not v.strip() or len(v) > 100 for v in values):
                raise ValueError(f"Invalid {key}")
        if not isinstance(self.model, str) or len(self.model) > 200:
            raise ValueError("Invalid reaction model")
        if not isinstance(self.prompt, str) or not self.prompt.strip() or len(self.prompt) > 6000:
            raise ValueError("Invalid reaction prompt")
        if (isinstance(self.timeout_seconds, bool) or
                not isinstance(self.timeout_seconds, int | float) or
                not math.isfinite(self.timeout_seconds) or not 0.1 <= self.timeout_seconds <= 60):
            raise ValueError("timeout_seconds must be 0.1..60")
        if self.on_error not in ("resume", "stop"):
            raise ValueError("on_error must be resume or stop")
        return self

    def to_dict(self) -> dict:
        result = asdict(self)
        result["numeric_verbs"] = list(self.numeric_verbs)
        result["semantic_phrases"] = list(self.semantic_phrases)
        return result

    @classmethod
    def schema(cls) -> dict:
        defaults = cls().to_dict()
        properties = {}
        ranges = {"numeric_max_digits": (1, 12), "max_tokens": (16, 256),
                  "context_messages": (1, 50), "queue_capacity": (1, 256)}
        for key, value in defaults.items():
            if type(value) is bool:
                rule = {"type": "boolean"}
            elif key in ranges:
                lo, hi = ranges[key]
                rule = {"type": "integer", "minimum": lo, "maximum": hi}
            elif isinstance(value, list):
                rule = {"type": "array", "minItems": 1, "maxItems": 30,
                        "items": {"type": "string", "minLength": 1, "maxLength": 100,
                                  "pattern": r"\S"}}
            elif key == "timeout_seconds":
                rule = {"type": "number", "minimum": 0.1, "maximum": 60}
            elif key == "on_error":
                rule = {"type": "string", "enum": ["resume", "stop"]}
            else:
                rule = {"type": "string", "maxLength": 200 if key == "model" else 6000}
                if key == "prompt":
                    rule.update(minLength=1, pattern=r"\S")
            properties[key] = {**rule, "default": value}
        return {"$schema": "https://json-schema.org/draft/2020-12/schema",
                "title": "TyperX Reaction Settings", "type": "object",
                "additionalProperties": False, "properties": properties}


class ReactionRules:
    def __init__(self, settings: ReactionSettings):
        self.settings = settings.validate()
        digits = rf"[0-9]{{1,{settings.numeric_max_digits}}}"
        alternatives = [rf"(?P<bare>{digits})", rf'"(?P<double>{digits})"',
                        rf"'(?P<single>{digits})'", rf"«(?P<angle>{digits})»",
                        rf"“(?P<curly>{digits})”"]
        verbs = "|".join(re.escape(v.strip()) for v in settings.numeric_verbs)
        self.number = re.compile(
            rf"(?:(?:{verbs})\s+(?:число\s+)?)?(?:{'|'.join(alternatives)})[.!?]?", re.I)
        phrases = "|".join(re.escape(v.strip()) for v in settings.semantic_phrases)
        self.semantic = re.compile(rf"(?<!\w)(?:{phrases})(?!\w)", re.I)

    def numeric(self, text: str, direct_reply: bool) -> str | None:
        if not self.settings.numeric_enabled or (self.settings.numeric_reply_only and not direct_reply):
            return None
        match = self.number.fullmatch(text.strip())
        return next((v for v in match.groupdict().values() if v is not None), None) if match else None

    def is_semantic(self, text: str, direct_reply: bool) -> bool:
        return bool(self.settings.semantic_enabled and
                    (direct_reply or not self.settings.semantic_reply_only) and
                    self.semantic.search(text))

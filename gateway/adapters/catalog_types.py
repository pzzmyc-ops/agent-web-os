from __future__ import annotations

from dataclasses import dataclass
from typing import Any


@dataclass
class CatalogParam:
    name: str
    type: str
    required: bool
    description: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "name": self.name,
            "type": self.type,
            "required": self.required,
            "description": self.description,
        }


@dataclass
class MediaOperation:
    operation: str
    product_code: str
    method: str
    path: str
    model: str = ""
    note: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "operation": self.operation,
            "product_code": self.product_code,
            "method": self.method,
            "path": self.path,
            "model": self.model,
            "note": self.note,
        }

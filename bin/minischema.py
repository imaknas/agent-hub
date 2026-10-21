"""JSON Schema 的一小部分(只用標準函式庫,系統的 python3 也能跑)——contracts/ 跟 muninn 的契約只用到這些:
type(含 ["string","null"])、required、properties、additionalProperties(true/false/schema)、items、
enum、const、pattern、minimum、minLength、maxLength、minItems、if/then/else、$ref(#/$defs/…)。
不認得的關鍵字直接忽略(跟 JSON Schema 規格一樣)。

  errors(schema, instance) -> list[str]     空的 = 合格
"""
from __future__ import annotations

import re

TYPES = {"object": dict, "array": list, "string": str, "boolean": bool, "null": type(None)}


def _type_ok(t: str, x) -> bool:
    if t == "integer":
        return isinstance(x, int) and not isinstance(x, bool)
    if t == "number":
        return isinstance(x, (int, float)) and not isinstance(x, bool)
    return isinstance(x, TYPES.get(t, object))


def errors(schema: dict, x, path: str = "$", root: dict | None = None) -> list[str]:
    root = root or schema
    if schema is True or schema == {}:
        return []
    if schema is False:
        return [f"{path}:不允許"]
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return [f"{path}:不支援的 $ref {ref}"]
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        return errors(target, x, path, root)
    out: list[str] = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(tt, x) for tt in ts):
            return [f"{path}:型別要是 {t},實際是 {type(x).__name__}"]
    if "const" in schema and x != schema["const"]:
        out.append(f"{path}:要是 {schema['const']!r}")
    if "enum" in schema and x not in schema["enum"]:
        out.append(f"{path}:{x!r} 不在 {schema['enum']}")
    if isinstance(x, str):
        if "pattern" in schema and not re.search(schema["pattern"], x):
            out.append(f"{path}:{x!r} 不符合 {schema['pattern']}")
        if "minLength" in schema and len(x) < schema["minLength"]:
            out.append(f"{path}:太短")
        if "maxLength" in schema and len(x) > schema["maxLength"]:
            out.append(f"{path}:太長")
    if isinstance(x, (int, float)) and not isinstance(x, bool) and "minimum" in schema and x < schema["minimum"]:
        out.append(f"{path}:要 >= {schema['minimum']}")
    if isinstance(x, dict):
        for k in schema.get("required", []):
            if k not in x:
                out.append(f"{path}:缺 {k}")
        props = schema.get("properties", {})
        for k, v in x.items():
            if k in props:
                out += errors(props[k], v, f"{path}.{k}", root)
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    out.append(f"{path}:多了 {k}")
                elif isinstance(ap, dict):
                    out += errors(ap, v, f"{path}.{k}", root)
    if isinstance(x, list):
        if "minItems" in schema and len(x) < schema["minItems"]:
            out.append(f"{path}:至少 {schema['minItems']} 項")
        if "items" in schema:
            for i, v in enumerate(x):
                out += errors(schema["items"], v, f"{path}[{i}]", root)
    if "if" in schema:
        branch = "then" if not errors(schema["if"], x, path, root) else "else"
        if branch in schema:
            out += errors(schema[branch], x, path, root)
    return out

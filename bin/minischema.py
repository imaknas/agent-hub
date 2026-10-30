"""JSON Schema 的一小部分(只用標準函式庫)——contracts/ 跟 muninn 的契約只用到這些:
type(含 ["string","null"])、required、properties、additionalProperties(true/false/schema)、items、
enum、const、pattern、minimum、minLength、maxLength、minItems、if/then/else、anyOf、$ref(#/$defs/…)。
不認得的關鍵字直接忽略(跟 JSON Schema 規格一樣)。

  errors(schema, instance) -> list[str]     空的 = 合格
"""
from __future__ import annotations

import re

import i18n

_ = i18n.domain(__file__)

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
        return [_("{path}: not allowed", path=path)]
    if "$ref" in schema:
        ref = schema["$ref"]
        if not ref.startswith("#/"):
            return [_("{path}: unsupported $ref {ref}", path=path, ref=ref)]
        target = root
        for part in ref[2:].split("/"):
            target = target[part]
        return errors(target, x, path, root)
    out: list[str] = []
    t = schema.get("type")
    if t is not None:
        ts = t if isinstance(t, list) else [t]
        if not any(_type_ok(tt, x) for tt in ts):
            return [_("{path}: the type must be {type}, got {actual}", path=path, type=t, actual=type(x).__name__)]
    if "const" in schema and x != schema["const"]:
        out.append(_("{path}: must be {const!r}", path=path, const=schema["const"]))
    if "enum" in schema and x not in schema["enum"]:
        out.append(_("{path}: {value!r} is not in {enum}", path=path, value=x, enum=schema["enum"]))
    if isinstance(x, str):
        if "pattern" in schema and not re.search(schema["pattern"], x):
            out.append(_("{path}: {value!r} does not match {pattern}", path=path, value=x, pattern=schema["pattern"]))
        if "minLength" in schema and len(x) < schema["minLength"]:
            out.append(_("{path}: too short", path=path))
        if "maxLength" in schema and len(x) > schema["maxLength"]:
            out.append(_("{path}: too long", path=path))
    if isinstance(x, (int, float)) and not isinstance(x, bool) and "minimum" in schema and x < schema["minimum"]:
        out.append(_("{path}: must be >= {minimum}", path=path, minimum=schema["minimum"]))
    if isinstance(x, dict):
        for k in schema.get("required", []):
            if k not in x:
                out.append(_("{path}: missing {key}", path=path, key=k))
        props = schema.get("properties", {})
        for k, v in x.items():
            if k in props:
                out += errors(props[k], v, f"{path}.{k}", root)
            else:
                ap = schema.get("additionalProperties", True)
                if ap is False:
                    out.append(_("{path}: unexpected {key}", path=path, key=k))
                elif isinstance(ap, dict):
                    out += errors(ap, v, f"{path}.{k}", root)
    if isinstance(x, list):
        if "minItems" in schema and len(x) < schema["minItems"]:
            out.append(_("{path}: needs at least {n} items", path=path, n=schema["minItems"]))
        if "items" in schema:
            for i, v in enumerate(x):
                out += errors(schema["items"], v, f"{path}[{i}]", root)
    if "if" in schema:
        branch = "then" if not errors(schema["if"], x, path, root) else "else"
        if branch in schema:
            out += errors(schema[branch], x, path, root)
    if "anyOf" in schema and all(errors(sub, x, path, root) for sub in schema["anyOf"]):
        out.append(_("{path}: matches none of the anyOf conditions", path=path))
    return out

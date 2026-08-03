#!/usr/bin/env python3
"""Validates every rendered custom resource against this chart's own crds/*.yaml schemas.

`helm template`/`helm lint` only check YAML syntax and Go-template correctness - they don't
know anything about slipmesh.net's own CRD schemas (format: cidr, maxLength, required fields,
etc.), so a chart that renders "successfully" can still produce a CustomResource the apiserver
would reject outright. This closes that gap in CI, offline, without a real cluster.

Usage: helm template slipmesh . -f .ci/example-values.yaml | .ci/validate-crs.py
"""
import ipaddress
import sys

import jsonschema
import yaml

CRD_FILES = [
    "crds/mesh-crds.yaml",
    "crds/router-crds.yaml",
    "crds/nftables-crds.yaml",
    "crds/roadwarriors-crds.yaml",
]

# "cidr" and "int32" aren't part of JSON Schema's own format vocabulary (they're OpenAPI-flavor
# annotations schemars emits - see mesh-types' own doc comment on why), so jsonschema's stock
# FormatChecker has no built-in validator for either and silently no-ops on them by default.
# Without these two @checks registrations, `format: cidr`/`format: int32` in crds/*.yaml would
# never actually be enforced here regardless of whether a FormatChecker is passed at all.
FORMAT_CHECKER = jsonschema.FormatChecker()


@FORMAT_CHECKER.checks("cidr", raises=ValueError)
def _check_cidr(value):
    if not isinstance(value, str):
        return True
    ipaddress.ip_network(value, strict=True)
    return True


@FORMAT_CHECKER.checks("int32", raises=ValueError)
def _check_int32(value):
    if not isinstance(value, int):
        return True
    if not (-(2**31) <= value <= 2**31 - 1):
        raise ValueError(f"{value} does not fit in a signed 32-bit int")
    return True


def load_schemas():
    schemas = {}
    for path in CRD_FILES:
        with open(path) as f:
            for doc in yaml.safe_load_all(f):
                if not doc:
                    continue
                kind = doc["spec"]["names"]["kind"]
                for version in doc["spec"]["versions"]:
                    schemas[kind] = version["schema"]["openAPIV3Schema"]
    return schemas


def main() -> int:
    schemas = load_schemas()
    rendered = list(yaml.safe_load_all(sys.stdin))

    checked = 0
    failed = 0
    for doc in rendered:
        if not doc:
            continue
        kind = doc.get("kind")
        if kind not in schemas:
            continue
        checked += 1
        try:
            jsonschema.validate(
                instance=doc, schema=schemas[kind], format_checker=FORMAT_CHECKER
            )
        except jsonschema.ValidationError as e:
            failed += 1
            name = doc.get("metadata", {}).get("name", "<unnamed>")
            print(f"FAIL {kind}/{name}: {e.message}", file=sys.stderr)

    print(f"{checked} custom resources checked, {failed} failed")
    return 1 if failed else 0


if __name__ == "__main__":
    sys.exit(main())

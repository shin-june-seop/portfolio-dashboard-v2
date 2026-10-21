"""Static inventory of Supabase table/RPC calls in a legacy Streamlit source file.

This is source-code discovery only, not a live database schema inspection.
No source code is executed and no database is contacted.
"""
from __future__ import annotations
import argparse
import ast
import csv
from collections import defaultdict
from pathlib import Path

OPS = {"select", "insert", "upsert", "update", "delete", "rpc"}


def const_value(node, constants):
    if isinstance(node, ast.Constant) and isinstance(node.value, str):
        return node.value
    if isinstance(node, ast.Name):
        return constants.get(node.id, f"<dynamic:{node.id}>")
    return "<dynamic-expression>"


def nearest_function(node, parents):
    cur = node
    while cur in parents:
        cur = parents[cur]
        if isinstance(cur, (ast.FunctionDef, ast.AsyncFunctionDef)):
            return cur.name
    return "<module>"


def extract_columns(node):
    if not node.args:
        return ""
    arg = node.args[0]
    if isinstance(arg, ast.Constant) and isinstance(arg.value, str):
        return arg.value
    if isinstance(arg, (ast.Dict,)):
        keys = []
        for key in arg.keys:
            if isinstance(key, ast.Constant) and isinstance(key.value, str):
                keys.append(key.value)
            elif key is not None:
                keys.append("<dynamic-key>")
        return ",".join(keys)
    if isinstance(arg, (ast.List, ast.Tuple)):
        return ",".join(str(e.value) for e in arg.elts if isinstance(e, ast.Constant))
    return "<dynamic>"


def inventory(source_path: Path):
    tree = ast.parse(source_path.read_text(encoding="utf-8", errors="replace"))
    constants = {}
    for node in tree.body:
        if isinstance(node, (ast.Assign, ast.AnnAssign)):
            targets = node.targets if isinstance(node, ast.Assign) else [node.target]
            value = node.value
            if isinstance(value, ast.Constant) and isinstance(value.value, str):
                for target in targets:
                    if isinstance(target, ast.Name):
                        constants[target.id] = value.value
    parents = {}
    for parent in ast.walk(tree):
        for child in ast.iter_child_nodes(parent):
            parents[child] = parent

    rows = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute) or node.func.attr not in OPS:
            continue
        method = node.func.attr
        expr = node.func.value
        table_name = None
        # Find the `.table(...)` or `.rpc(...)` call feeding this query chain.
        for inner in ast.walk(expr):
            if isinstance(inner, ast.Call) and isinstance(inner.func, ast.Attribute) and inner.func.attr in {"table", "rpc"}:
                table_name = const_value(inner.args[0], constants) if inner.args else "<missing-arg>"
                if inner.func.attr == "rpc":
                    table_name = f"RPC:{table_name}"
                break
        if table_name is None:
            continue
        rows.append({
            "table_or_rpc": table_name,
            "operation": method,
            "function": nearest_function(node, parents),
            "literal_columns_or_keys": extract_columns(node),
            "line": getattr(node, "lineno", ""),
        })
    # exact duplicate chains/visits are removed, lines retained for traceability
    unique = {(r["table_or_rpc"], r["operation"], r["function"], r["literal_columns_or_keys"], r["line"]): r for r in rows}
    return sorted(unique.values(), key=lambda r: (r["table_or_rpc"], r["operation"], r["line"]))


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source", type=Path, help="legacy app.py file to inspect")
    parser.add_argument("--output-dir", type=Path, default=Path("legacy_db_inventory"))
    args = parser.parse_args()
    rows = inventory(args.source)
    args.output_dir.mkdir(parents=True, exist_ok=True)
    csv_path = args.output_dir / "legacy_db_operations.csv"
    with csv_path.open("w", newline="", encoding="utf-8-sig") as f:
        writer = csv.DictWriter(f, fieldnames=["table_or_rpc", "operation", "function", "literal_columns_or_keys", "line"])
        writer.writeheader(); writer.writerows(rows)
    by_table = defaultdict(list)
    for row in rows:
        by_table[row["table_or_rpc"]].append(row)
    md = ["# Legacy Supabase Static Operation Inventory", "",
          f"Source: `{args.source.name}`; call sites found: **{len(rows)}**.", "",
          "This is code-level static discovery only. It does not inspect the live Supabase schema, column types, keys, constraints, RLS policies, triggers, views, or stored procedures.", "",
          "| Table/RPC | Operations found | Functions found |", "|---|---|---|"]
    for table, ops in sorted(by_table.items()):
        op_list = ", ".join(sorted(set(r["operation"] for r in ops)))
        funcs = ", ".join(sorted(set(r["function"] for r in ops)))
        md.append(f"| `{table}` | {op_list} | {funcs} |")
    md += ["", "## Review priorities", "",
           "1. Obtain a schema-only export or user-reviewed table/column screenshots from Supabase; do not enable V2 adapter based on this report alone.",
           "2. Map every write-capable legacy path, especially transaction replacement, watchlist edits, PP trades/entries/exits/adds, and snapshot upserts.",
           "3. Record primary/unique keys, foreign keys, data types, nullability, RLS policies, and trigger behavior before defining any V2 mappings.",
           "4. V2 must remain read-only and disabled until exact read allowlists and projections are approved.", ""]
    md_path = args.output_dir / "LEGACY_DB_INVENTORY.md"
    md_path.write_text("\n".join(md), encoding="utf-8")
    print(f"Static call sites: {len(rows)}")
    print(f"Tables/RPCs: {len(by_table)}")
    print(f"CSV: {csv_path}")
    print(f"Markdown: {md_path}")

if __name__ == "__main__":
    main()

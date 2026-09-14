"""Persistent, isolated ASC session. JSON lines are the only stdout protocol."""
import contextlib
import json
import os
import sys
import time
import traceback


def main():
    if os.name == "posix":
        os.setsid()
    output = sys.stdout

    def emit(event, **payload):
        output.write(json.dumps({"event": event, **payload}, ensure_ascii=True) + "\n")
        output.flush()

    with contextlib.redirect_stdout(sys.stderr):
        from src.asc_client.gui.runtime import GuiDexStore
        store = None
        emit("ready")
        for line in sys.stdin:
            request = json.loads(line)
            op = request["op"]
            started = time.perf_counter()
            try:
                if op == "load":
                    store = GuiDexStore(request["path"], max_workers=4)
                    store.load(lambda done, total, dex, count: emit(
                        "progress", done=done, total=total, text=f"{dex} · {count:,} 个类"))
                    result = {"classes": store.class_names, "dex_count": len(store.dex_buffers)}
                elif store is None:
                    raise RuntimeError("请先打开 APK")
                elif op == "source":
                    dex, source = store.get_source(request["class_name"])
                    result = {"source": source, "dex": dex, "class_name": request["class_name"]}
                    # Keep only the most recent 32 source strings in this process.
                    while len(store.source_cache) > 32:
                        store.source_cache.pop(next(iter(store.source_cache)))
                elif op == "definitions":
                    from .definitions import find_definitions
                    result = find_definitions(store, request)
                elif op == "manifest":
                    from src.asc_client.manifest_handler import get_manifest_xml
                    result = {"source": get_manifest_xml(store.apk_path)}
                elif op == "search":
                    if request.get('reference_request'):
                        from .references import exact_references
                        result = exact_references(store, request['reference_request'], progress=lambda done, total, hits: emit(
                            'progress', done=done, total=total, text=f'已扫描 {done}/{total} DEX · {hits:,} 条命中'))
                    elif request["kind"] in ("method", "field"):
                        result = store.search_members(request["kind"], request["query"])
                    else:
                        result = store.search(
                            request["kind"], request["query"],
                            class_name=request.get("class_filter") or None,
                            fuzzy_class=request.get("fuzzy", False),
                            result_callback=lambda rows, *_: emit("rows", rows=rows),
                            progress_callback=lambda done, total, hits: emit(
                                "progress", done=done, total=total, text=f"已扫描 {done}/{total} DEX · {hits:,} 条命中"))
                else:
                    raise ValueError(f"未知操作: {op}")
                emit("result", op=op, result=result, elapsed=time.perf_counter() - started)
            except Exception as exc:
                emit("error", op=op, message=str(exc), detail=traceback.format_exc())


if __name__ == "__main__":
    main()

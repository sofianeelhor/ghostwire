import json, time

# V8 .heapsnapshot model. The snapshot is one JSON blob: a flat `nodes` int array (each node
# is len(node_fields) ints), a flat `edges` int array (each edge is len(edge_fields) ints, laid
# out consecutively in node order), and a `strings` table. A node's `name` is an index into
# strings; an edge's `to_node` is a BYTE offset into nodes (divide by field count for the index).
# Fields are read from snapshot.meta, never hardcoded — Chrome versions differ (e.g. some drop
# trace_node_id). Retainer (reverse-edge) and edge-offset indexes are built lazily, once.

NUMERIC_TYPES = ("string", "number")
STRONG_EDGES = ("property", "internal", "element", "context")
NAMED_EDGES = ("property", "internal", "context", "shortcut", "hidden")


def take_snapshot(engine, session_id=None, numeric=True, timeout=60):
    sid = session_id if session_id is not None else engine.page_session
    chunks = []
    collect = lambda params, s=None: chunks.append(params["chunk"])
    engine.cdp.on("HeapProfiler.addHeapSnapshotChunk", collect)
    try:
        engine.send("HeapProfiler.enable", session_id=sid)
        engine.send("HeapProfiler.takeHeapSnapshot",
                    {"reportProgress": False, "captureNumericValue": numeric}, session_id=sid)
        # the reply lands before the dispatcher has drained every chunk event; the blob is a
        # complete JSON exactly when the last chunk has arrived, so poll-parse until it is.
        deadline = time.time() + timeout
        while True:
            try:
                data = json.loads("".join(chunks))
                break
            except ValueError:
                if time.time() > deadline:
                    raise RuntimeError("heap snapshot did not assemble within timeout")
                time.sleep(0.05)
    finally:
        engine.cdp.off("HeapProfiler.addHeapSnapshotChunk", collect)
    return Snapshot(data)


class Snapshot:
    def __init__(self, data):
        meta = data["snapshot"]["meta"]
        self.nodes, self.edges, self.strings = data["nodes"], data["edges"], data["strings"]
        self.node_count = data["snapshot"]["node_count"]
        node_fields, edge_fields = meta["node_fields"], meta["edge_fields"]
        self.node_width, self.edge_width = len(node_fields), len(edge_fields)
        self.f_type, self.f_name, self.f_id, self.f_size, self.f_edges = (
            node_fields.index("type"), node_fields.index("name"), node_fields.index("id"),
            node_fields.index("self_size"), node_fields.index("edge_count"))
        self.e_type, self.e_name, self.e_to = (
            edge_fields.index("type"), edge_fields.index("name_or_index"), edge_fields.index("to_node"))
        self.node_type_names = meta["node_types"][0]
        self.edge_type_names = meta["edge_types"][0]
        self._edge_offset = None        # node index -> first edge index
        self._retainers = None          # node index -> [(from, edge_type_name, edge_name_or_index)]

    def name(self, node):
        return self.strings[self.nodes[node * self.node_width + self.f_name]]

    def type(self, node):
        return self.node_type_names[self.nodes[node * self.node_width + self.f_type]]

    def node_id(self, node):
        return self.nodes[node * self.node_width + self.f_id]

    def self_size(self, node):
        return self.nodes[node * self.node_width + self.f_size]

    def edge_count(self, node):
        return self.nodes[node * self.node_width + self.f_edges]

    def _index_edges(self):
        if self._edge_offset is not None:
            return
        offset = [0] * self.node_count
        running = 0
        for node in range(self.node_count):
            offset[node] = running
            running += self.nodes[node * self.node_width + self.f_edges]
        self._edge_offset = offset

    def edges_of(self, node):
        self._index_edges()
        result = []
        first = self._edge_offset[node]
        for k in range(self.edge_count(node)):
            base = (first + k) * self.edge_width
            result.append((self.edge_type_names[self.edges[base + self.e_type]],
                           self.edges[base + self.e_name],
                           self.edges[base + self.e_to] // self.node_width))
        return result

    def _index_retainers(self):
        if self._retainers is not None:
            return
        self._index_edges()
        retainers = {}
        edges, width = self.edges, self.edge_width
        e_type, e_name, e_to, node_width = self.e_type, self.e_name, self.e_to, self.node_width
        for node in range(self.node_count):
            first = self._edge_offset[node]
            for k in range(self.edge_count(node)):
                base = (first + k) * width
                target = edges[base + e_to] // node_width
                retainers.setdefault(target, []).append(
                    (node, self.edge_type_names[edges[base + e_type]], edges[base + e_name]))
        self._retainers = retainers

    def _edge_label(self, edge_type, name_or_index):
        return self.strings[name_or_index] if edge_type in NAMED_EDGES else name_or_index

    def retainers(self, node):
        self._index_retainers()
        return [(frm, edge_type, self._edge_label(edge_type, raw))
                for frm, edge_type, raw in self._retainers.get(node, [])]

    def find_value(self, value):
        wanted = str(value)
        return [node for node in range(self.node_count)
                if self.type(node) in NUMERIC_TYPES and self.name(node) == wanted]

    def retaining_path(self, node, max_depth=8):
        path, seen, current = [], set(), node
        for _ in range(max_depth):
            if current in seen:
                break
            seen.add(current)
            strong = [r for r in self.retainers(current) if r[1] in STRONG_EDGES]
            if not strong:
                break
            frm, edge_type, label = strong[0]
            path.append({"holder": self.name(frm), "type": self.type(frm), "via": label})
            current = frm
        return path

    def find_objects(self, value=None, constructor=None, key=None, limit=50):
        results, seen_ids = [], set()

        def emit(obj_node, prop, val):
            oid = self.node_id(obj_node)
            if oid in seen_ids:
                return
            seen_ids.add(oid)
            results.append({"id": oid, "constructor": self.name(obj_node), "type": self.type(obj_node),
                            "property": prop, "value": val, "path": self.retaining_path(obj_node)})

        if value is not None:
            for string_node in self.find_value(value):
                for frm, edge_type, label in self.retainers(string_node):
                    if edge_type == "property" and self.type(frm) in ("object", "closure"):
                        emit(frm, label, value)
                        if len(results) >= limit:
                            return results

        if constructor is not None or key is not None:
            for node in range(self.node_count):
                if self.type(node) != "object":
                    continue
                if constructor is not None and self.name(node) != constructor:
                    continue
                if key is not None and not any(
                        et == "property" and self._edge_label(et, raw) == key
                        for et, raw, _ in self.edges_of(node)):
                    continue
                emit(node, key, None)
                if len(results) >= limit:
                    break
        return results

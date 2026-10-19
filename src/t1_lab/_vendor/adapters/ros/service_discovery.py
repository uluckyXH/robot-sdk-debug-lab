"""Keep service reachability separate from per-node provenance evidence."""

from collections import Counter
import logging


def inspect_service_sources(node, services):
    unknown = {kind: None for kind in services}
    providers = {kind: [] for kind in services}
    by_name = {path: kind for kind, (path, _) in services.items()}
    try:
        nodes = node.get_node_names_and_namespaces()
    except Exception:
        return unknown, {}
    if len(nodes) > 256:
        return unknown, {}
    complete = True
    for (name, namespace), count in Counter(nodes).items():
        try:
            rows = node.get_service_names_and_types_by_node(name, namespace)
        except Exception:
            # A node may leave between the two graph queries. This must not
            # erase typed-client reachability for every unrelated service.
            complete = False
            continue
        if len(rows) > 2048:
            complete = False
            continue
        for path, types in rows:
            if path in by_name:
                providers[by_name[path]].extend([(name, namespace, tuple(types))] * min(count, 2))
    sources, conflicts = {}, {}
    for kind, values in providers.items():
        expected = ("aimdk_msgs/srv/" + services[kind][1],)
        if len(values) > 1:
            conflicts[kind] = "服务图报告多个提供者或同名提供节点"
        elif values and values[0][2] != expected:
            conflicts[kind] = "服务图类型与 SDK 不匹配"
        # An incomplete graph cannot establish uniqueness, even if a typed
        # client can communicate. Never promote readiness to device identity.
        sources[kind] = values[0] if complete and len(values) == 1 and kind not in conflicts else None
    return sources, conflicts


class MotionServiceReadiness:
    """Read-only client checks; never probe a write RPC by sending a request."""

    def __init__(self, clients, services):
        self.clients, self.services = clients, services
        self.conflicts, self.reasons = {}, {}
        self.generation = 0

    def refresh(self):
        reasons = {}
        for kind, (path, type_name) in self.services.items():
            reason = self.conflicts.get(kind)
            if not reason:
                try:
                    ready = self.clients[kind].service_is_ready()
                    reason = None if ready else "SDK 客户端尚未发现可调用服务"
                except Exception as exc:
                    reason = "SDK 服务就绪检查失败（" + type(exc).__name__ + "）"
            reasons[kind] = reason
            if kind not in self.reasons or reason != self.reasons[kind]:
                logging.getLogger("roboweave.bridge.motion").info(
                    "motion_service service=%s type=aimdk_msgs/srv/%s ready=%s detail=%s",
                    path,
                    type_name,
                    reason is None,
                    reason or "SDK 客户端就绪",
                )
        if reasons != self.reasons:
            self.generation += 1
        self.reasons = reasons

    def reason(self, kinds):
        self.refresh()
        missing = [
            self.services[kind][0] + "：" + self.reasons[kind]
            for kind in kinds
            if self.reasons[kind] is not None
        ]
        return "；".join(missing) or None

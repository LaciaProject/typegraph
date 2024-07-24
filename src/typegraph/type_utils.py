from typing import Type

import networkx as nx


def get_subclass_types(cls: Type):
    if hasattr(cls, "__subclasses__"):
        for subclass in cls.__subclasses__():
            yield subclass
            yield from get_subclass_types(subclass)


def get_connected_nodes(graph, node):
    if node not in graph:
        return set()

    # 获取正向连通的节点
    successors = set(nx.descendants(graph, node))

    # 获取逆向连通的节点
    predecessors = set(nx.ancestors(graph, node))

    # 合并所有连通的节点
    connected_nodes = successors | predecessors | {node}

    return connected_nodes


def get_connected_subgraph(graph, node):
    connected_nodes = get_connected_nodes(graph, node)
    subgraph = graph.subgraph(connected_nodes).copy()
    return subgraph


def iter_type_args(tp):
    args = tp.args
    if args:
        for arg in args:
            if isinstance(arg, list):
                for i in arg:
                    yield i
                    yield from iter_type_args(i)
            else:
                yield arg
                yield from iter_type_args(arg)

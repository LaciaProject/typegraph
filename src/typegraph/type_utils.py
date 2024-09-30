from typing import Type, Literal
from typing_extensions import get_args, get_origin

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


def show_mermaid_graph(
    graph: nx.DiGraph, env: Literal["jupyter", "markdown", "marimo"] = "markdown"
):
    mermaid = mermaid_graph(graph).strip()
    if env == "jupyter":
        from IPython.display import display, Markdown

        display(Markdown(f"```mermaid\n{mermaid}\n```"))
    elif env == "markdown":
        print(f"```mermaid\n{mermaid}\n```")
    elif env == "marimo":
        import marimo as mo

        return mo.mermaid(mermaid)


def mermaid_graph(graph: nx.DiGraph):
    import typing

    nodes = []

    def get_name(cls):
        if get_origin(cls) in (typing.Annotated,):
            return str(cls)
        if type(cls) in (typing._GenericAlias, typing.GenericAlias):  # type: ignore
            return str(cls)
        elif hasattr(cls, "__name__"):
            return cls.__name__
        return str(cls)

    def get_node_name(cls):
        return f"node{nodes.index(cls)}"

    text = "graph TD;\n"
    for edge in graph.edges(data=True):
        if edge[0] not in nodes:
            nodes.append(edge[0])
        if edge[1] not in nodes:
            nodes.append(edge[1])
        line_style = "--" if edge[2].get("line", False) else "-.-"
        text += f'    {get_node_name(edge[0])}["{get_name(edge[0])}"] {line_style}> {get_node_name(edge[1])}["{get_name(edge[1])}"]\n'
    return text

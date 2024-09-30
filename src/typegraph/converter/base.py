import inspect
import asyncio
import itertools
from typing import (
    TypeVar,
    ParamSpec,
    Callable,
    Iterable,
    cast,
    Type,
    Awaitable,
    Any,
    Optional,
    Mapping,
    get_type_hints,
    Literal,
    Generic,
)
from functools import wraps, reduce

import networkx as nx
from typing_extensions import get_args, get_origin
from typing_tool import (
    like_isinstance,
    like_issubclass,
    extract_typevar_mapping,
    gen_typevar_model,
)
from typing_tool.type_utils import deep_type, is_structural_type

from ..type_utils import get_connected_subgraph, show_mermaid_graph


T = TypeVar("T")
In = TypeVar("In", contravariant=True)
Out = TypeVar("Out")
P = ParamSpec("P")


class Converter:
    G: nx.DiGraph
    sG: nx.DiGraph
    tG: nx.DiGraph
    qG: nx.DiGraph

    def __init__(self):
        self.G = nx.DiGraph()
        self.sG = nx.DiGraph()
        self.tG = nx.DiGraph()
        self.qG = nx.DiGraph()

    def get_graph(self, graphs: Optional[list[nx.DiGraph]] = None):
        return nx.all.compose_all([self.G, self.sG, self.qG] + (graphs or []))

    def _gen_edge(
        self, in_type: Type[In], out_type: Type[Out], converter: Callable[P, Out]
    ):
        edges = []

        for node in self.get_graph().nodes():
            group = [
                (in_type, node),
                (out_type, node),
                (node, in_type),
                (node, out_type),
            ]
            for u, v in group:
                if u == v:
                    continue
                if self.like_issubclass(u, v):
                    edges.append((u, v, {"converter": lambda x: x, "line": False}))

        self.G.add_edge(in_type, out_type, converter=converter, line=True)

        for u, v, d in edges:
            self.sG.add_edge(u, v, **d)

    def register_generic_converter(self, input_type: Type, out_type: Type):
        def decorator(func: Callable[P, T]):
            self.tG.add_edge(input_type, out_type, converter=func)
            return func

        return decorator

    def register_converter(self, input_type: Type[In], out_type: Type[Out]):
        def decorator(func: Callable[P, T]) -> Callable[P, Out]:
            self._gen_edge(input_type, out_type, func)
            return cast(Callable[P, Out], func)

        return decorator

    def async_register_converter(self, input_type: Type[In], out_type: Type[Out]):
        def decorator(func: Callable[P, Awaitable[Out]]):
            self._gen_edge(input_type, out_type, func)
            return func

        return decorator

    def like_issubclass(self, obj, cls: Type):
        return like_issubclass(obj, cls)

    def like_isinstance(self, obj, cls: Type):
        return like_isinstance(obj, cls)


class PdtConverter(Converter):
    
    def add_converter(self, converter: Converter):
        for u, v, c in converter.G.edges(data=True):
            self._gen_edge(u, v, c["converter"])
        for u, v, c in converter.tG.edges(data=True):
            self.tG.add_edge(u, v, **c)

    def _gen_graph(self, in_type: Type[In], out_type: Type[Out], deep: int = 2):
        tmp_G = nx.DiGraph()
        if deep == 0:
            return tmp_G

        im = gen_typevar_model(in_type)
        om = gen_typevar_model(out_type)

        def _gen_sub_graph(mapping, node):
            for su, sv, sc in get_connected_subgraph(self.tG, node).edges(data=True):
                tmp_G.add_edge(su.get_instance(mapping), sv.get_instance(mapping), **sc)

        for u, v, c in self.tG.edges(data=True):
            um = gen_typevar_model(u)
            vm = gen_typevar_model(v)
            combos = [(um, im), (vm, im), (um, om), (vm, om)]
            for t, i in combos:
                try:
                    mapping = extract_typevar_mapping(t, i)
                    tmp_G.add_edge(
                        um.get_instance(mapping), vm.get_instance(mapping), **c
                    )
                    _gen_sub_graph(mapping, t)
                except Exception:
                    ...
        for u, v, c in tmp_G.edges(data=True):
            tmp_G = nx.compose(tmp_G, self._gen_graph(u, v, deep - 1))

        self.qG = nx.compose(self.qG, tmp_G)
        return tmp_G

    def can_convert(self, in_type: Type[In], out_type: Type[Out]) -> bool:
        try:
            nx.has_path(self.get_graph(), in_type, out_type)
            res = True
        except (nx.NodeNotFound, nx.NetworkXNoPath):
            res = False
        return res

    def get_converter(self, in_type: Type[In], out_type: Type[Out]):
        G = self.get_graph()
        if self.can_convert(in_type, out_type):
            try:
                for path in nx.shortest_simple_paths(G, in_type, out_type):
                    converters = [
                        G.get_edge_data(path[i], path[i + 1])["converter"]
                        for i in range(len(path) - 1)
                    ]
                    if len(path) == 1 and len(converters) == 0:
                        path, converters = path * 2, [lambda x: x]
                    func = reduce(lambda f, g: lambda x: g(f(x)), converters)
                    yield path, func
            except nx.NetworkXNoPath:
                ...

    async def async_get_converter(self, in_type: Type[In], out_type: Type[Out]):
        def async_wrapper(converters):
            async def async_converter(input_value):
                for converter in converters:
                    if inspect.iscoroutinefunction(converter):
                        input_value = await converter(input_value)
                    else:
                        input_value = converter(input_value)
                return input_value

            return async_converter

        G = self.get_graph()
        if self.can_convert(in_type, out_type):
            try:
                for path in nx.shortest_simple_paths(G, in_type, out_type):
                    converters = [
                        G.get_edge_data(path[i], path[i + 1])["converter"]
                        for i in range(len(path) - 1)
                    ]
                    if len(path) == 1 and len(converters) == 0:
                        path, converters = path * 2, [lambda x: x]
                    yield path, async_wrapper(converters)
            except nx.NetworkXNoPath:
                ...

    def convert(
        self, input_value, out_type: Type[Out], debug: bool = False, depth: int = 3
    ) -> Out:
        if self.like_isinstance(input_value, out_type):
            return cast(Out, input_value)
        input_type = deep_type(input_value)
        for source, target in self.iter_all_paths(input_value, out_type, depth=depth):
            for path, func in self.get_converter(source, target):
                if debug:
                    print(f"Converting {source} to {target} using {path}, {func}")
                try:
                    return func(input_value)
                except Exception:
                    ...
        if is_structural_type(input_type) and is_structural_type(out_type):
            in_origin = get_origin(input_type)
            out_origin = get_origin(out_type)
            if self.like_issubclass(in_origin, out_origin):  # type: ignore
                out_args = get_args(out_type)

                def _iter_func(item):
                    return self.convert(
                        item,
                        out_args[0],
                        debug=debug,
                    )

                def __iter_func_dict(item):
                    k, v = item
                    return self.convert(
                        k,
                        out_args[0],
                        debug=debug,
                    ), self.convert(
                        v,
                        out_args[1],
                        debug=debug,
                    )

                if self.like_issubclass(in_origin, list):
                    res = list(map(_iter_func, input_value))
                elif self.like_issubclass(in_origin, tuple):
                    res = tuple(map(_iter_func, input_value))
                elif self.like_issubclass(in_origin, set):
                    res = set(map(_iter_func, input_value))
                elif self.like_issubclass(in_origin, Mapping):
                    res = dict(map(__iter_func_dict, input_value.items()))
                elif self.like_issubclass(out_origin, Iterable):
                    res = list(map(_iter_func, input_value))
                else:
                    raise ValueError(
                        f"Unsupported structural_type {input_type} to {out_type}, {input_value}"
                    )
                return cast(Out, res)

        raise ValueError(
            f"No converter registered for {input_type} to {out_type}, {input_value}"
        )

    async def async_convert(
        self, input_value, out_type: Type[Out], debug: bool = False, depth: int = 3
    ) -> Out:
        if self.like_isinstance(input_value, out_type):
            return cast(Out, input_value)
        input_type = deep_type(input_value)
        for source, target in self.iter_all_paths(input_value, out_type, depth=depth):
            async for path, func in self.async_get_converter(source, target):
                if debug:
                    print(f"Converting {source} to {target} using {path}, {func}")
                try:
                    return await func(input_value)
                except Exception as e:
                    if debug:
                        print(e)
        if is_structural_type(input_type) and is_structural_type(out_type):
            in_origin = get_origin(input_type)
            out_origin = get_origin(out_type)
            if self.like_issubclass(in_origin, out_origin):  # type: ignore
                out_args = get_args(out_type)

                async def _iter_func(item):
                    return await self.async_convert(
                        item,
                        out_args[0],
                        debug=debug,
                    )

                async def __iter_func_dict(item):
                    k, v = item
                    return await self.async_convert(
                        k,
                        out_args[0],
                        debug=debug,
                    ), await self.async_convert(
                        v,
                        out_args[1],
                        debug=debug,
                    )

                if self.like_issubclass(in_origin, list):
                    res = await asyncio.gather(*map(_iter_func, input_value))
                elif self.like_issubclass(in_origin, tuple):
                    res = tuple(await asyncio.gather(*map(_iter_func, input_value)))
                elif self.like_issubclass(in_origin, set):
                    res = set(await asyncio.gather(*map(_iter_func, input_value)))
                elif self.like_issubclass(in_origin, Mapping):
                    items = map(__iter_func_dict, input_value.items())
                    res = dict(await asyncio.gather(*items))
                elif self.like_issubclass(out_origin, Iterable):
                    res = await asyncio.gather(*map(_iter_func, input_value))
                else:
                    raise ValueError(
                        f"Unsupported structural_type {input_type} to {out_type}, {input_value}"
                    )
                return cast(Out, res)

        raise ValueError(
            f"No converter registered for {input_type} to {out_type}, {input_value}"
        )

    def auto_convert(
        self,
        ignore_error: bool = False,
        localns: dict[str, Any] | None = None,
        globalns: dict[str, Any] | None = None,
    ):
        def decorator(func: Callable[P, T]):
            sig = inspect.signature(func)
            hints = get_type_hints(
                func, include_extras=True, globalns=globalns, localns=localns
            )

            @wraps(func)
            def wrapper(*args, **kwargs) -> T:
                bound = sig.bind(*args, **kwargs)
                for name, value in bound.arguments.items():
                    if name in hints:
                        try:
                            bound.arguments[name] = self.convert(
                                value,
                                hints[name],
                            )
                        except Exception as e:
                            if ignore_error:
                                continue
                            raise e
                return func(*bound.args, **bound.kwargs)

            return wrapper

        return decorator

    def async_auto_convert(
        self,
        ignore_error: bool = False,
        localns: dict[str, Any] | None = None,
        globalns: dict[str, Any] | None = None,
    ):
        def decorator(func: Callable[P, Awaitable[T]]):
            sig = inspect.signature(func)
            hints = get_type_hints(
                func, include_extras=True, globalns=globalns, localns=localns
            )

            @wraps(func)
            async def wrapper(*args, **kwargs) -> T:
                bound = sig.bind(*args, **kwargs)
                for name, value in bound.arguments.items():
                    if name in hints:
                        try:
                            bound.arguments[name] = await self.async_convert(
                                value,
                                hints[name],
                            )
                        except Exception as e:
                            if ignore_error:
                                continue
                            raise e
                return await func(*bound.args, **bound.kwargs)

            return wrapper

        return decorator

    def show_mermaid_graph(
        self, env: Literal["jupyter", "markdown", "marimo"] = "jupyter"
    ):
        return show_mermaid_graph(self.get_graph(), env)

    def iter_all_paths(self, in_value, out_type: Type[Out], depth: int = 3):
        if depth == 0:
            return

        self.check_connected()
        paths = self.iter_basis_paths(in_value, out_type)
        if paths:
            for source, target in paths:
                yield source, target
            return

        self.iter_generic_paths(in_value, out_type, depth)

        yield from self.iter_all_paths(in_value, out_type, depth - 1)

    def iter_generic_paths(self, in_value, out_type: Type[Out], depth: int = 3):
        if depth == 0:
            return

        for u, v, c in self.tG.edges(data=True):
            um = gen_typevar_model(u)
            vm = gen_typevar_model(v)
            combos = [(um, out_type), (vm, out_type)]
            for t, i in combos:
                try:
                    mapping = extract_typevar_mapping(t, i)
                    if mapping:
                        new_in, new_out = (
                            um.get_instance(mapping),
                            vm.get_instance(mapping),
                        )
                        self.qG.add_edge(new_in, new_out, **c)
                        self.iter_generic_paths(in_value, new_in, depth - 1)  # type: ignore
                except Exception:
                    ...

    def iter_basis_paths(self, in_value, out_type: Type[Out]):
        source, target = set(), set()
        in_type = deep_type(in_value)
        for node in self.get_graph().nodes():
            if self.like_isinstance(in_value, node):
                source.add(node)
            if self.like_issubclass(node, out_type):
                target.add(node)
            if self.like_issubclass(in_type, node):
                source.add(node)
        return list(itertools.product(source, target))

    def check_connected(self, graphs: Optional[list[nx.DiGraph]] = None):
        G = self.get_graph(graphs)
        nodes = list(G.nodes())
        for source, target in itertools.combinations(nodes, 2):
            if self.like_issubclass(source, target):
                self.sG.add_edge(source, target, line=False, converter=lambda x: x)

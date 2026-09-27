"""ECS架构 - 实体/组件/系统核心引擎"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Component:
    entity_id: int
    data: dict = field(default_factory=dict)
    alive: bool = True


class Entity:
    def __init__(self, eid: int):
        self.id = eid
        self.components: dict[str, Component] = {}
        self.alive = True


class System:
    def __init__(self, name: str, update_fn: Callable, depends_on: list[str] = None):
        self.name = name
        self.update_fn = update_fn
        self.depends_on = depends_on or []
        self.active_entities: list[int] = []


class EventBus:
    def __init__(self):
        self.handlers: dict[str, list[Callable]] = {}

    def on(self, event: str, handler: Callable):
        self.handlers.setdefault(event, []).append(handler)

    def off(self, event: str, handler: Callable):
        handlers = self.handlers.get(event)
        if handlers and handler in handlers:
            handlers.remove(handler)

    def emit(self, event: str, data: Any = None):
        # 快照迭代：回调中销毁实体/增删处理器都不会影响本次派发
        for handler in list(self.handlers.get(event, ())):
            handler(data)


class ComponentPool:
    def __init__(self, comp_type: str, size: int = 100):
        self.comp_type = comp_type
        self.size = size
        self.pool: list[Optional[Component]] = [None] * size
        self.free: list[int] = list(range(size))

    def acquire(self, entity_id: int) -> Component:
        if self.free:
            idx = self.free.pop()
            comp = self.pool[idx]
        else:
            idx = len(self.pool)
            self.pool.append(None)
            comp = None
        if comp is None:
            comp = Component(entity_id)
            self.pool[idx] = comp
        else:
            # 复用前清零上一个使用者的全部残留字段
            comp.entity_id = entity_id
            comp.data = {}
            comp.alive = True
        return comp

    def release(self, comp: Component):
        for i, slot in enumerate(self.pool):
            if slot is comp and i not in self.free:
                comp.alive = False
                comp.entity_id = -1
                comp.data = {}
                self.free.append(i)
                return


class QueryCache:
    def __init__(self):
        self.cache: dict[str, tuple[int, list[int]]] = {}
        self.version = 0

    def invalidate(self):
        self.version += 1

    def get(self, key: str, build_fn: Callable) -> list[int]:
        entry = self.cache.get(key)
        if entry is None or entry[0] != self.version:
            result = build_fn()
            self.cache[key] = (self.version, result)
            return result
        return entry[1]


class ECS:
    def __init__(self):
        self.entities: dict[int, Entity] = {}
        self.systems: list[System] = []
        self.event_bus = EventBus()
        self.pools: dict[str, ComponentPool] = {}
        self.query_cache = QueryCache()
        self._next_id = 1
        self._ordered_systems: list[System] = []

    def create_entity(self) -> Entity:
        eid = self._next_id
        self._next_id += 1
        ent = Entity(eid)
        self.entities[eid] = ent
        self.query_cache.invalidate()
        return ent

    def destroy_entity(self, eid: int):
        ent = self.entities.pop(eid, None)
        if ent is None:
            return
        ent.alive = False
        # 释放组件回池，组件引用随之失效
        for comp_type, comp in list(ent.components.items()):
            pool = self.pools.get(comp_type)
            if pool is not None:
                pool.release(comp)
        ent.components.clear()
        # 同步更新所有系统的活跃实体缓存（原地修改，外部别名也可见）
        for sys in self.systems:
            sys.active_entities[:] = [
                other for other in sys.active_entities if other != eid
            ]
        self.query_cache.invalidate()

    def add_component(self, eid: int, comp_type: str, data: dict = None) -> Component:
        ent = self.entities[eid]
        if comp_type not in self.pools:
            self.pools[comp_type] = ComponentPool(comp_type)
        pool = self.pools[comp_type]
        old = ent.components.get(comp_type)
        if old is not None:
            pool.release(old)
        comp = pool.acquire(eid)
        comp.data = data or {}
        ent.components[comp_type] = comp
        self.query_cache.invalidate()
        return comp

    def get_component(self, eid: int, comp_type: str) -> Optional[Component]:
        ent = self.entities.get(eid)
        if ent is None:
            return None
        comp = ent.components.get(comp_type)
        if comp is None or not comp.alive:
            return None
        return comp

    def register_system(self, system: System):
        self.systems.append(system)
        self._ordered_systems = self._topological_sort()

    def _topological_sort(self) -> list[System]:
        by_name = {sys.name: sys for sys in self.systems}
        indegree = {sys.name: 0 for sys in self.systems}
        dependents: dict[str, list[str]] = {sys.name: [] for sys in self.systems}
        for sys in self.systems:
            for dep in sys.depends_on:
                if dep in by_name:
                    indegree[sys.name] += 1
                    dependents[dep].append(sys.name)

        ordered_names: list[str] = []
        remaining = set(by_name)
        # 每轮按注册顺序挑选就绪节点，同优先级保持注册顺序
        while remaining:
            advanced = False
            for sys in self.systems:
                if sys.name in remaining and indegree[sys.name] == 0:
                    ordered_names.append(sys.name)
                    remaining.remove(sys.name)
                    for dependent in dependents[sys.name]:
                        indegree[dependent] -= 1
                    advanced = True
                    break
            if not advanced:
                # 依赖成环时不再阻塞，按注册顺序附加剩余系统
                ordered_names.extend(
                    sys.name for sys in self.systems if sys.name in remaining
                )
                break
        return [by_name[name] for name in ordered_names]

    def update(self, dt: float = 1.0):
        for sys in self._ordered_systems:
            alive = [eid for eid in sys.active_entities if eid in self.entities]
            sys.active_entities[:] = alive
            sys.update_fn(self, list(alive), dt)

    def query(self, comp_types: list[str]) -> list[int]:
        key = "+".join(sorted(comp_types))

        def build():
            result = []
            for eid, ent in self.entities.items():
                if all(ct in ent.components for ct in comp_types):
                    result.append(eid)
            return result

        return self.query_cache.get(key, build)

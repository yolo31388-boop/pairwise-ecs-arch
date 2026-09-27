"""ECS架构核心"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional


@dataclass
class Component:
    entity_id: int
    data: dict = field(default_factory=dict)
    valid: bool = True


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

    def emit(self, event: str, data: Any = None):
        # 快照迭代：回调里增删handler或销毁发送者不影响本次派发
        for handler in list(self.handlers.get(event, [])):
            handler(data)


class ComponentPool:
    def __init__(self, comp_type: str, size: int = 100):
        self.comp_type = comp_type
        self.size = size
        self.pool: list[Optional[Component]] = [None] * size
        self.free: list[int] = list(range(size))

    def acquire(self, entity_id: int) -> Component:
        if not self.free:
            idx = len(self.pool)
            self.pool.append(None)
        else:
            idx = self.free.pop()
        comp = self.pool[idx]
        if comp is None:
            comp = Component(entity_id)
            self.pool[idx] = comp
        else:
            # 复用前清零上一个实体的所有字段
            comp.entity_id = entity_id
            comp.data.clear()
            comp.valid = True
        return comp

    def release(self, comp: Component):
        for i, c in enumerate(self.pool):
            if c is comp:
                comp.valid = False
                self.free.append(i)
                break


class QueryCache:
    def __init__(self):
        self.cache: dict[str, list[int]] = {}
        self.dirty = True

    def get(self, key: str, build_fn: Callable) -> list[int]:
        if self.dirty or key not in self.cache:
            self.cache[key] = build_fn()
            self.dirty = False
        return self.cache[key]


class ECS:
    def __init__(self):
        self.entities: dict[int, Entity] = {}
        self.systems: list[System] = []
        self.event_bus = EventBus()
        self.pools: dict[str, ComponentPool] = {}
        self.query_cache = QueryCache()
        self._next_id = 1
        self._execution_order: list[str] = []

    def create_entity(self) -> Entity:
        eid = self._next_id
        self._next_id += 1
        ent = Entity(eid)
        self.entities[eid] = ent
        self.query_cache.dirty = True
        return ent

    def destroy_entity(self, eid: int):
        ent = self.entities.pop(eid, None)
        if ent is None:
            return
        ent.alive = False
        # 释放组件并让外部持有的组件引用失效
        for comp_type, comp in ent.components.items():
            pool = self.pools.get(comp_type)
            if pool is not None:
                pool.release(comp)
            comp.valid = False
        ent.components.clear()
        # 同步更新所有系统的活跃实体缓存
        for sys in self.systems:
            if eid in sys.active_entities:
                sys.active_entities = [e for e in sys.active_entities if e != eid]
        self.query_cache.dirty = True

    def add_component(self, eid: int, comp_type: str, data: dict = None) -> Component:
        if comp_type not in self.pools:
            self.pools[comp_type] = ComponentPool(comp_type)
        comp = self.pools[comp_type].acquire(eid)
        comp.data = data or {}
        self.entities[eid].components[comp_type] = comp
        self.query_cache.dirty = True
        return comp

    def get_component(self, eid: int, comp_type: str) -> Optional[Component]:
        ent = self.entities.get(eid)
        if ent is None or not ent.alive:
            return None
        comp = ent.components.get(comp_type)
        if comp is None or not comp.valid:
            return None
        return comp

    def register_system(self, system: System):
        self.systems.append(system)

    def _sorted_systems(self) -> list[System]:
        """按依赖声明拓扑排序，同级保持注册顺序；循环依赖时打断回边"""
        by_name = {s.name: s for s in self.systems}
        order: list[System] = []
        state: dict[str, int] = {}  # 1=visiting, 2=done

        def visit(name: str):
            if state.get(name) or name not in by_name:
                return
            state[name] = 1
            for dep in by_name[name].depends_on:
                if state.get(dep) != 1:
                    visit(dep)
            state[name] = 2
            order.append(by_name[name])

        for s in self.systems:
            visit(s.name)
        return order

    def update(self, dt: float = 1.0):
        for sys in self._sorted_systems():
            alive = [eid for eid in sys.active_entities if eid in self.entities]
            sys.active_entities = alive
            sys.update_fn(self, alive, dt)

    def query(self, comp_types: list[str]) -> list[int]:
        key = "+".join(sorted(comp_types))
        def build():
            result = []
            for eid, ent in self.entities.items():
                if all(ct in ent.components for ct in comp_types):
                    result.append(eid)
            return result
        return self.query_cache.get(key, build)

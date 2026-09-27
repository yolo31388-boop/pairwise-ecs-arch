"""ECS架构 - 带6个bug的引擎"""
from dataclasses import dataclass, field
from typing import Any, Callable, Optional
import heapq


@dataclass
class Component:
    entity_id: int
    data: dict = field(default_factory=dict)


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
        self.active_entities: list[int] = []  # bug1: 销毁后不更新缓存


class EventBus:
    def __init__(self):
        self.handlers: dict[str, list[Callable]] = {}

    def on(self, event: str, handler: Callable):
        self.handlers.setdefault(event, []).append(handler)

    def emit(self, event: str, data: Any = None):
        # bug5: 直接迭代handlers，回调里销毁发送者会导致迭代器失效
        for handler in self.handlers.get(event, []):
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
        # bug6: 复用后不清零旧数据
        comp = Component(entity_id)
        self.pool[idx] = comp
        return comp

    def release(self, comp: Component):
        for i, c in enumerate(self.pool):
            if c is comp:
                self.pool[i] = None
                self.free.append(i)
                break


class QueryCache:
    def __init__(self):
        self.cache: dict[str, list[int]] = {}
        self.dirty = True  # bug4: 增删实体后不标记失效

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
        # bug1: 只从主列表删，不通知系统缓存
        if eid in self.entities:
            del self.entities[eid]
        # bug2: 不通知其他实体的组件引用失效
        self.query_cache.dirty = True

    def add_component(self, eid: int, comp_type: str, data: dict = None) -> Component:
        if comp_type not in self.pools:
            self.pools[comp_type] = ComponentPool(comp_type)
        comp = self.pools[comp_type].acquire(eid)
        comp.data = data or {}
        self.entities[eid].components[comp_type] = comp
        return comp

    def get_component(self, eid: int, comp_type: str) -> Optional[Component]:
        ent = self.entities.get(eid)
        if ent:
            return ent.components.get(comp_type)
        return None  # bug2: 应该检测引用失效但直接返回None

    def register_system(self, system: System):
        self.systems.append(system)
        # bug3: 按注册顺序执行，不做拓扑排序

    def update(self, dt: float = 1.0):
        # bug3: 固定注册顺序
        for sys in self.systems:
            alive = [eid for eid in sys.active_entities if eid in self.entities]
            sys.active_entities = alive  # bug1: 只在这里更新，销毁后下一帧才生效
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

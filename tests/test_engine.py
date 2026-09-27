"""ECS测试 - 8个失败用例"""
import pytest
import sys
import os
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..'))

from src.engine import ECS, System, EventBus, Component


class TestECSDestroy:
    def test_destroy_entity_removes_from_system_cache(self):
        """销毁实体后系统缓存应立即更新，下一帧不遍历已死实体"""
        ecs = ECS()
        e1 = ecs.create_entity()
        e2 = ecs.create_entity()
        ecs.add_component(e1.id, "position", {"x": 0})
        ecs.add_component(e2.id, "position", {"x": 1})

        processed = []
        def move_sys(ecs, entities, dt):
            processed.extend(entities)

        sys = System("move", move_sys)
        sys.active_entities = [e1.id, e2.id]
        ecs.register_system(sys)

        ecs.destroy_entity(e1.id)
        ecs.update()
        assert e1.id not in processed, "已销毁实体不应被系统处理"

    def test_component_reference_invalidation(self):
        """其他实体持有已销毁实体的组件引用，应检测到失效"""
        ecs = ECS()
        e1 = ecs.create_entity()
        e2 = ecs.create_entity()
        comp = ecs.add_component(e1.id, "health", {"hp": 100})
        ecs.add_component(e2.id, "target", {"ref": e1.id})

        ecs.destroy_entity(e1.id)
        result = ecs.get_component(e1.id, "health")
        assert result is None, "已销毁实体的组件应返回None"


class TestECSSystemOrder:
    def test_system_execution_order_by_dependency(self):
        """系统应按依赖拓扑排序执行，输入系统应在移动系统前"""
        ecs = ECS()
        order = []

        def input_sys(ecs, ents, dt):
            order.append("input")
        def move_sys(ecs, ents, dt):
            order.append("move")
        def render_sys(ecs, ents, dt):
            order.append("render")

        ecs.register_system(System("move", move_sys, depends_on=["input"]))
        ecs.register_system(System("input", input_sys))
        ecs.register_system(System("render", render_sys, depends_on=["move"]))

        ecs.update()
        assert order == ["input", "move", "render"], f"执行顺序错误: {order}"


class TestECSQueryCache:
    def test_query_cache_invalidates_on_entity_add(self):
        """新增实体后查询缓存应失效，新实体应被查询到"""
        ecs = ECS()
        e1 = ecs.create_entity()
        ecs.add_component(e1.id, "pos", {})
        result1 = ecs.query(["pos"])
        assert e1.id in result1

        e2 = ecs.create_entity()
        ecs.add_component(e2.id, "pos", {})
        result2 = ecs.query(["pos"])
        assert e2.id in result2, "新增实体后缓存未失效"


class TestECSEventBus:
    def test_event_dispatch_safe_during_callback_destroy(self):
        """事件回调中销毁发送者，后续回调不应丢失"""
        ecs = ECS()
        bus = ecs.event_bus
        results = []

        def handler1(data):
            results.append("h1")
            ecs.destroy_entity(data)

        def handler2(data):
            results.append("h2")

        e1 = ecs.create_entity()
        bus.on("test", handler1)
        bus.on("test", handler2)
        bus.emit("test", e1.id)
        assert "h2" in results, "回调中销毁发送者导致后续回调丢失"


class TestECSComponentPool:
    def test_component_pool_reuse_clears_old_data(self):
        """组件池复用后应清零旧数据，新实体不应带旧实体残留"""
        ecs = ECS()
        e1 = ecs.create_entity()
        c1 = ecs.add_component(e1.id, "stats", {"level": 99, "name": "old"})
        pool = ecs.pools["stats"]
        pool.release(c1)

        e2 = ecs.create_entity()
        c2 = pool.acquire(e2.id)
        assert c2.data == {}, f"复用后残留旧数据: {c2.data}"


class TestECSIntegration:
    def test_full_game_loop_no_crash(self):
        """完整游戏循环不崩溃"""
        ecs = ECS()
        for i in range(5):
            e = ecs.create_entity()
            ecs.add_component(e.id, "pos", {"x": i})
            ecs.add_component(e.id, "vel", {"v": 1})

        def move(ecs, ents, dt):
            for eid in ents:
                comp = ecs.get_component(eid, "pos")
                if comp:
                    comp.data["x"] += 1

        ecs.register_system(System("move", move))
        for _ in range(10):
            ecs.update()
        assert True

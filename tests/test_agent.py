# coding: utf-8
"""
Unit tests for FreeThreadedAgent (azazael/src/CORE/Agent.py)

Run with: python -m pytest tests/test_agent.py -v
Or:        python tests/test_agent.py
"""

import sys, os, time
sys.path.insert(0, os.path.join(os.path.dirname(__file__), '..', 'src'))

# Prevent PAMS recording during tests
os.environ['PAMS_URL'] = 'http://127.0.0.1:99999'

from CORE.Agent import (
    FreeThreadedAgent, AgentConfig, AgentTask,
    IS_FREE_THREADED, HAS_PAMS,
)


def test_agent_creation():
    """Test that agent can be created with default config."""
    agent = FreeThreadedAgent()
    assert agent.agent_id is not None
    assert len(agent.agent_id) > 0
    stats = agent.get_stats()
    assert stats['tasks_submitted'] == 0
    assert stats['tasks_completed'] == 0
    assert stats['tasks_failed'] == 0
    assert agent.config.agent_name == 'unnamed'


def test_agent_creation_with_config():
    """Test agent creation with custom config."""
    config = AgentConfig(
        agent_name='test-agent',
        max_workers=4,
        max_recursion=3,
        record_to_pams=False,
        log_level='debug',
    )
    agent = FreeThreadedAgent(config)
    assert agent.config.agent_name == 'test-agent'
    assert agent.config.max_workers == 4
    assert agent.config.record_to_pams is False
    assert agent.config.log_level == 'debug'


def test_single_task_execution():
    """Test executing a single task synchronously."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    result = agent.execute({'data': 'hello'}, task_type='test')
    assert result.success is True
    assert result.task_type == 'test'
    assert result.error is None
    assert result.duration_ms >= 0
    assert result.status == 'completed'


def test_single_task_with_custom_run():
    """Test agent with overridden _run method."""
    class MathAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            n = task.get('data', 0)
            return {'square': n * n, 'cube': n * n * n}

    agent = MathAgent(AgentConfig(record_to_pams=False))
    result = agent.execute({'data': 5}, task_type='math')
    assert result.success is True
    assert result.result == {'square': 25, 'cube': 125}


def test_batch_execution():
    """Test execute_many with multiple tasks in parallel."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False, max_workers=4))
    tasks = [{'n': i} for i in range(10)]
    results = agent.execute_many(tasks, task_type='batch', max_workers=4)
    assert len(results) == 10
    for r in results:
        assert r.success is True
        assert r.task_type == 'batch'
    succeeded = sum(1 for r in results if r.success)
    assert succeeded == 10


def test_batch_empty():
    """Test execute_many with empty list."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    results = agent.execute_many([], task_type='batch')
    assert results == []


def test_error_handling():
    """Test that errors in _run are captured properly."""
    class BrokenAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            raise ValueError('Intentional test error')

    agent = BrokenAgent(AgentConfig(record_to_pams=False))
    result = agent.execute({'data': 'boom'})
    assert result.success is False
    assert result.error is not None
    assert 'Intentional test error' in result.error
    assert result.status == 'failed'


def test_stats_tracking():
    """Test that stats are tracked correctly across operations."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    
    # Run a few tasks
    for i in range(5):
        agent.execute({'data': i}, task_type='stats-test')
    
    stats = agent.get_stats()
    assert stats['tasks_submitted'] == 5
    assert stats['tasks_completed'] == 5
    assert stats['tasks_failed'] == 0
    assert stats['success_rate'] == 100.0
    assert stats['avg_duration_ms'] >= 0
    assert stats['task_history_count'] >= 5


def test_stats_with_failures():
    """Test stats track failures correctly."""
    class MixedAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            n = task.get('data', 0)
            if n % 2 == 0:
                raise ValueError('Even numbers fail')
            return {'ok': n}

    agent = MixedAgent(AgentConfig(record_to_pams=False))
    for i in range(4):
        agent.execute({'data': i})

    stats = agent.get_stats()
    assert stats['tasks_submitted'] == 4
    assert stats['tasks_completed'] == 2
    assert stats['tasks_failed'] == 2
    assert stats['success_rate'] == 50.0


def test_state_management():
    """Test thread-safe state get/set."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    agent.set_state('key1', 'value1')
    agent.set_state('counter', 42)
    assert agent.get_state('key1') == 'value1'
    assert agent.get_state('counter') == 42
    assert agent.get_state('nonexistent', 'default') == 'default'


def test_sub_agent_spawning():
    """Test spawning a child agent."""
    class TestAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            return {'echo': task.get('data', '')}

    parent = TestAgent(AgentConfig(record_to_pams=False, agent_name='parent'))
    child = parent.spawn_sub_agent(TestAgent)
    assert child is not None
    assert child.agent_id != parent.agent_id
    assert len(parent._sub_agents) == 1
    
    # Child should be functional
    result = child.execute({'data': 'child-work'})
    assert result.success is True


def test_task_history():
    """Test task history storage."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    for i in range(10):
        agent.execute({'data': i})
    
    history = agent.get_task_history(limit=5)
    assert len(history) == 5  # Last 5 out of 10
    assert history[0].success is True


def test_default_run_pass_through():
    """Test the default _run returns data as-is."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False, log_level='debug'))
    result = agent.execute({'data': 'passthrough'})
    assert result.success is True
    assert result.result == 'passthrough'


def test_custom_run_with_pool():
    """Test _run receives the pool object correctly."""
    class PoolAwareAgent(FreeThreadedAgent):
        def _run(self, task, pool):
            # pool should be None for single execute()
            return {'pool_provided': pool is not None, 'task_data': task}

    agent = PoolAwareAgent(AgentConfig(record_to_pams=False))
    result = agent.execute({'test': True})
    assert result.result['pool_provided'] is False  # single task, no pool


def test_agent_repr():
    """Test string representation."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    r = repr(agent)
    assert 'FreeThreadedAgent' in r
    assert agent.agent_id in r


def test_get_all_state():
    """Test get_all_state returns a snapshot."""
    agent = FreeThreadedAgent(AgentConfig(record_to_pams=False))
    agent.set_state('a', 1)
    agent.set_state('b', 2)
    all_state = agent.get_all_state()
    assert all_state.get('a') == 1
    assert all_state.get('b') == 2


if __name__ == '__main__':
    # Run tests manually
    tests = [
        ('agent_creation', test_agent_creation),
        ('agent_creation_with_config', test_agent_creation_with_config),
        ('single_task_execution', test_single_task_execution),
        ('single_task_with_custom_run', test_single_task_with_custom_run),
        ('batch_execution', test_batch_execution),
        ('batch_empty', test_batch_empty),
        ('error_handling', test_error_handling),
        ('stats_tracking', test_stats_tracking),
        ('stats_with_failures', test_stats_with_failures),
        ('state_management', test_state_management),
        ('sub_agent_spawning', test_sub_agent_spawning),
        ('task_history', test_task_history),
        ('default_run_pass_through', test_default_run_pass_through),
        ('custom_run_with_pool', test_custom_run_with_pool),
        ('agent_repr', test_agent_repr),
        ('get_all_state', test_get_all_state),
    ]
    passed = 0
    failed = 0
    print(f"\n  FreeThreadedAgent Tests ({len(tests)} tests)")
    print(f"  {'='*50}")
    for name, fn in tests:
        try:
            fn()
            print(f"  [OK] {name}")
            passed += 1
        except Exception as e:
            print(f"  [FAIL] {name}: {e}")
            failed += 1
    print(f"  {'='*50}")
    print(f"  Passed: {passed}, Failed: {failed}")

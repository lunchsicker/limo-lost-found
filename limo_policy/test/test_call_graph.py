"""Call graph helpers (moved from limo_llm/test/test_function_parser.py)."""

from limo_policy.call_graph import build_call_graph, find_cycle, topological_order


def test_call_graph_cycle_and_order():
    helpers = {
        'a': 'def a(x):\n  return b(x)\n',
        'b': 'def b(x):\n  return c(x)\n',
        'c': 'def c(x):\n  return goto_loc(x)\n',
    }
    graph = build_call_graph(helpers)
    assert graph == {'a': {'b'}, 'b': {'c'}, 'c': set()}
    assert find_cycle(graph) is None
    assert topological_order(graph) == ['c', 'b', 'a']


def test_cycles_detected():
    assert find_cycle({'a': {'a'}}) == ['a', 'a']
    cyc = find_cycle({'a': {'b'}, 'b': {'a'}})
    assert cyc[0] == cyc[-1] and set(cyc) == {'a', 'b'}

"""FunctionParser (original CaP logic). Call-graph tests: limo_policy/test/test_call_graph.py."""

from limo_llm.function_parser import (call_arity, find_undefined_functions,
                                      parse_called_functions)


def test_parser_prefers_assignment_signature_like_original():
    code = "say('hi')\nfound_loc = find_obj_in_locs('cup', get_search_locations())\n"
    fs = parse_called_functions(code)
    assert fs['say'] == "say('hi')"
    assert fs['find_obj_in_locs'] == \
        "found_loc = find_obj_in_locs('cup', get_search_locations())"
    assert fs['get_search_locations'] == 'get_search_locations()'


def test_nested_calls_are_collected():
    fs = parse_called_functions('x = a(b(c(1)))\n')
    assert set(fs) == {'a', 'b', 'c'}


def test_attribute_calls_are_not_function_names():
    fs = parse_called_functions('x = obj.method(1)\n')
    assert fs == {}


def test_undefined_functions_exclude_known_and_local_defs():
    code = ('def helper(x):\n  return x\n'
            "y = helper(1)\nz = other_fn(2)\ngoto_loc('sofa')\n")
    undefined = find_undefined_functions(code, {'goto_loc'})
    assert set(undefined) == {'other_fn'}
    assert undefined['other_fn'] == 'z = other_fn(2)'


def test_call_arity():
    assert call_arity("found = f('cup', locs)") == (2, [])
    assert call_arity('f(1, y=2)') == (1, ['y'])

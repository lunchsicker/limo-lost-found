"""Hierarchical code generation (LMPFGen) with a scripted mock LLM."""

from conftest import CHECK_OBJ_HERE, FGEN_RESPONSES, FIND_OBJ_IN_LOCS, hierarchical_policy


def test_recursive_generation_order_and_prompts(make_generator):
    gen, llm = make_generator([('컵', hierarchical_policy('cup', '컵'))] + FGEN_RESPONSES)
    r = gen.generate('컵 좀 찾아줘.')
    assert r.success, r.error_message
    assert [h.name for h in r.bundle.helpers] == ['check_obj_here', 'find_obj_in_locs']
    assert r.bundle.helpers[1].calls == ['check_obj_here']
    queries = [c['query'] for c in llm.calls]
    assert queries[0] == '# 컵 좀 찾아줘.'
    # the signature given to fgen is the assignment, as in the original CaP
    assert queries[1] == ("# define function: found_loc = find_obj_in_locs('cup', "
                          'get_search_locations()).')
    assert queries[2] == '# define function: check_obj_here(obj_name).'
    assert llm.calls[1]['stop'] == ['# define', '# example']
    assert llm.calls[1]['prompt'].count('# define function: ') >= 6   # few-shot + query


def test_three_level_recursion(make_generator):
    f1 = ('def find_obj_in_locs(obj_name, loc_names):\n'
          '  for loc_name in loc_names:\n'
          '    if visit_and_check(loc_name, obj_name):\n'
          '      report_found(obj_name, loc_name)\n'
          '      return loc_name\n'
          '  return None\n')
    f2 = ('def visit_and_check(loc_name, obj_name):\n'
          '  if not goto_loc(loc_name):\n'
          '    return False\n'
          '  return check_obj_here(obj_name)\n')
    gen, llm = make_generator([
        ('find_obj_in_locs', f1), ('visit_and_check', f2), ('check_obj_here', CHECK_OBJ_HERE),
        ('책', hierarchical_policy('book', '책'))])
    r = gen.generate('내 책 찾아줘.')
    assert r.success, r.error_message
    assert [h.name for h in r.bundle.helpers] == \
        ['check_obj_here', 'visit_and_check', 'find_obj_in_locs']


def test_duplicate_generation_prevented(make_generator):
    policy = ("a = find_obj_in_locs('cup', get_search_locations())\n"
              "b = find_obj_in_locs('book', get_search_locations())\n"
              "c = check_obj_here('handbag')\n")
    gen, llm = make_generator([('컵', policy)] + FGEN_RESPONSES)
    r = gen.generate('컵이랑 책이랑 가방')
    assert r.success, r.error_message
    fgen_queries = [c['query'] for c in llm.calls if 'define function' in c['query']]
    assert len(fgen_queries) == 2      # each helper generated exactly once


def test_mutual_recursion_rejected(make_generator):
    a = 'def helper_a(x):\n  return helper_b(x)\n'
    b = 'def helper_b(x):\n  return helper_a(x)\n'
    gen, _ = make_generator([('helper_a', a), ('helper_b', b), ('컵', "y = helper_a('cup')\n")],
                            cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and r.error_code == 'FGEN_ERROR' and 'RECURSION' in r.error_message


def test_self_recursion_rejected(make_generator):
    a = 'def helper_a(x):\n  return helper_a(x)\n'
    gen, _ = make_generator([('helper_a', a), ('컵', "y = helper_a('cup')\n")],
                            cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and 'RECURSION' in r.error_message


def test_depth_limit(make_generator):
    chain = [(f' lvl{i}(',
              f'def lvl{i}(x):\n  return lvl{i + 1}(x)\n') for i in range(10)]
    gen, _ = make_generator(chain + [('컵', "y = lvl0('cup')\n")],
                            cfg={'max_attempts': 1, 'fgen': {'max_depth': 3}})
    r = gen.generate('컵')
    assert not r.success and 'DEPTH_LIMIT' in r.error_message


def test_function_count_limit(make_generator):
    policy = ''.join(f"y{i} = h{i}('cup')\n" for i in range(5))
    fgen = [(f'define function: y{i} = h{i}(', f'def h{i}(x):\n  return x\n') for i in range(5)]
    gen, _ = make_generator(fgen + [('컵', policy)],
                            cfg={'max_attempts': 1, 'fgen': {'max_functions': 3}})
    r = gen.generate('컵')
    assert not r.success and 'TOO_MANY_FUNCTIONS' in r.error_message


def test_generated_function_with_unsupported_syntax_rejected(make_generator):
    bad = 'def find_obj_in_locs(obj_name, loc_names):\n  while True:\n    return None\n'
    gen, _ = make_generator([('find_obj_in_locs', bad), ('컵', hierarchical_policy('cup', '컵'))],
                            cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and r.error_code == 'FGEN_ERROR'
    assert 'INVALID_FUNCTION' in r.error_message


def test_wrong_function_name_rejected(make_generator):
    gen, _ = make_generator([('find_obj_in_locs', CHECK_OBJ_HERE),
                             ('컵', hierarchical_policy('cup', '컵'))], cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and 'BAD_FUNCTION' in r.error_message


def test_signature_mismatch_rejected(make_generator):
    wrong = 'def find_obj_in_locs(obj_name):\n  return None\n'
    gen, _ = make_generator([('find_obj_in_locs', wrong),
                             ('컵', hierarchical_policy('cup', '컵'))], cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and 'SIGNATURE_MISMATCH' in r.error_message


def test_api_name_is_never_generated(make_generator):
    gen, llm = make_generator([('컵', "goto_loc('sofa')\nsay('ok')\n")])
    r = gen.generate('컵')
    assert r.success
    assert not any('define function' in c['query'] for c in llm.calls)


def test_policy_defining_api_name_rejected(make_generator):
    gen, _ = make_generator([('컵', 'def goto_loc(x):\n  return True\ngoto_loc(1)\n')],
                            cfg={'max_attempts': 1})
    r = gen.generate('컵')
    assert not r.success and 'NAME_COLLISION' in r.error_message


def test_fenced_and_chatty_fgen_output_is_cleaned(make_generator):
    fenced = '```python\n' + FIND_OBJ_IN_LOCS + "\nfind_obj_in_locs('cup', [])\n```\n"
    gen, _ = make_generator([('find_obj_in_locs', fenced), FGEN_RESPONSES[1],
                             ('컵', hierarchical_policy('cup', '컵'))])
    r = gen.generate('컵')
    assert r.success, r.error_message
    assert any('dropped 1 extra' in w for w in r.bundle.warnings)
    assert r.bundle.helper_sources['find_obj_in_locs'].strip() == FIND_OBJ_IN_LOCS.strip()

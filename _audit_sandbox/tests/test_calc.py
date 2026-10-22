from pkg.calc import add, div

def test_add():
    assert add(1, 2) == 3

def test_div():
    assert div(4, 2) == 2

def test_fail():
    assert add(1, 1) == 3

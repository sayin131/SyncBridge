from planner import plan, Action

def test_new_on_laptop():
    base = {}
    laptop = {"a.text":"H1"}
    phone = {}
    result = plan(base, laptop, phone)
    assert result == [Action("PUSH", "a.text")]
    
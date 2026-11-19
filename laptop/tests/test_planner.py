import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))

from planner import plan, Action  # noqa: E402


def test_new_on_laptop():
    assert plan({}, {"a.txt": "H1"}, {}) == [Action("PUSH", "a.txt")]


def test_new_on_phone():
    assert plan({}, {}, {"a.txt": "H1"}) == [Action("PULL", "a.txt")]


def test_no_change():
    base = laptop = phone = {"a.txt": "H1"}
    assert plan(base, laptop, phone) == []


def test_laptop_edited():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H2"}
    phone = {"a.txt": "H1"}
    assert plan(base, laptop, phone) == [Action("PUSH", "a.txt")]


def test_phone_edited():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H1"}
    phone = {"a.txt": "H2"}
    assert plan(base, laptop, phone) == [Action("PULL", "a.txt")]


def test_conflict_both_edited_differently():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H2"}
    phone = {"a.txt": "H3"}
    assert plan(base, laptop, phone) == [Action("CONFLICT", "a.txt")]


def test_both_edited_same_way_no_conflict():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H2"}
    phone = {"a.txt": "H2"}
    assert plan(base, laptop, phone) == []


def test_deleted_on_laptop():
    base = {"a.txt": "H1"}
    laptop = {}
    phone = {"a.txt": "H1"}
    assert plan(base, laptop, phone) == [Action("DELETE_PHONE", "a.txt")]


def test_deleted_on_phone():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H1"}
    phone = {}
    assert plan(base, laptop, phone) == [Action("DELETE_LAPTOP", "a.txt")]


def test_edit_delete_conflict_laptop_edit_phone_delete():
    base = {"a.txt": "H1"}
    laptop = {"a.txt": "H2"}
    phone = {}
    assert plan(base, laptop, phone) == [Action("CONFLICT", "a.txt")]


def test_edit_delete_conflict_phone_edit_laptop_delete():
    base = {"a.txt": "H1"}
    laptop = {}
    phone = {"a.txt": "H2"}
    assert plan(base, laptop, phone) == [Action("CONFLICT", "a.txt")]


def test_both_deleted():
    base = {"a.txt": "H1"}
    laptop = {}
    phone = {}
    assert plan(base, laptop, phone) == []


def test_new_identical_on_both():
    assert plan({}, {"a.txt": "H1"}, {"a.txt": "H1"}) == []


def test_new_conflicting_on_both():
    assert plan({}, {"a.txt": "H1"}, {"a.txt": "H2"}) == [Action("CONFLICT", "a.txt")]


def test_all_empty():
    assert plan({}, {}, {}) == []


def test_multiple_files_independent():
    base = {"a.txt": "H1", "b.txt": "H2"}
    laptop = {"a.txt": "H1X", "b.txt": "H2"}   # a edited
    phone = {"a.txt": "H1", "b.txt": "H2Y"}    # b edited
    result = plan(base, laptop, phone)
    assert Action("PUSH", "a.txt") in result
    assert Action("PULL", "b.txt") in result
    assert len(result) == 2


def test_order_independent():
    base = {"z.txt": "H1", "a.txt": "H2"}
    laptop = {"z.txt": "H1X", "a.txt": "H2X"}
    phone = {"z.txt": "H1", "a.txt": "H2"}
    # dict insertion order shouldn't matter — result should be sorted by path
    result = plan(base, laptop, phone)
    assert [a.path for a in result] == ["a.txt", "z.txt"]

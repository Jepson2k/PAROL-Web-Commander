"""Tests for multi-tab editor functionality."""

import pytest
from parol_commander.state import (
    EditorTab,
    EditorTabsState,
    editor_tabs_state,
    PathSegment,
    ProgramTarget,
)


class TestEditorTab:
    """Tests for EditorTab dataclass."""

    def test_create_editor_tab(self):
        """Test creating an EditorTab with required fields."""
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path="/path/test.py",
            content="# test content",
            saved_content="# test content",
        )
        assert tab.id == "test1"
        assert tab.filename == "test.py"
        assert tab.file_path == "/path/test.py"
        assert tab.content == "# test content"
        assert tab.saved_content == "# test content"

    def test_is_dirty_false_when_unchanged(self):
        """Test that is_dirty is False when content matches saved_content."""
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="# test",
            saved_content="# test",
        )
        assert not tab.is_dirty

    def test_is_dirty_true_when_modified(self):
        """Test that is_dirty is True when content differs from saved_content."""
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="# modified",
            saved_content="# original",
        )
        assert tab.is_dirty

    def test_is_dirty_after_modification(self):
        """Test that modifying content updates is_dirty."""
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="# initial",
            saved_content="# initial",
        )
        assert not tab.is_dirty

        tab.content = "# modified"
        assert tab.is_dirty

        tab.saved_content = "# modified"
        assert not tab.is_dirty

    def test_default_lists_empty(self):
        """Test that default list fields are empty."""
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="",
            saved_content="",
        )
        assert tab.output_log == []
        assert tab.path_segments == []
        assert tab.targets == []

    def test_path_segments_stored(self):
        """Test storing path segments in tab."""
        segment = PathSegment(
            points=[[0, 0, 0], [1, 1, 1]],
            color="#00ff00",
            is_valid=True,
            line_number=1,
            joints=[0, 0, 0, 0, 0, 0],
        )
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="",
            saved_content="",
            path_segments=[segment],
        )
        assert len(tab.path_segments) == 1
        assert tab.path_segments[0].is_valid

    def test_targets_stored(self):
        """Test storing targets in tab."""
        target = ProgramTarget(
            id="target1",
            line_number=5,
            pose=[100, 200, 300, 0, 0, 0],
            move_type="cartesian",
            scene_object_id="marker1",
        )
        tab = EditorTab(
            id="test1",
            filename="test.py",
            file_path=None,
            content="",
            saved_content="",
            targets=[target],
        )
        assert len(tab.targets) == 1
        assert tab.targets[0].move_type == "cartesian"


class TestEditorTabsState:
    """Tests for EditorTabsState management."""

    def test_initial_state_empty(self):
        """Test that new EditorTabsState starts empty."""
        state = EditorTabsState()
        assert len(state.tabs) == 0
        assert state.active_tab_id is None

    def test_add_tab(self):
        """Test adding a tab to state."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1",
            filename="file1.py",
            file_path=None,
            content="",
            saved_content="",
        )
        state.add_tab(tab)
        assert len(state.tabs) == 1
        assert state.tabs[0].id == "tab1"

    def test_add_multiple_tabs(self):
        """Test adding multiple tabs."""
        state = EditorTabsState()
        for i in range(3):
            tab = EditorTab(
                id=f"tab{i}",
                filename=f"file{i}.py",
                file_path=None,
                content="",
                saved_content="",
            )
            state.add_tab(tab)
        assert len(state.tabs) == 3

    def test_remove_tab(self):
        """Test removing a tab from state."""
        state = EditorTabsState()
        tab1 = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        tab2 = EditorTab(
            id="tab2", filename="file2.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab1)
        state.add_tab(tab2)

        state.remove_tab("tab1")
        assert len(state.tabs) == 1
        assert state.tabs[0].id == "tab2"

    def test_remove_active_tab_switches_to_another(self):
        """Test that removing active tab switches to another tab."""
        state = EditorTabsState()
        tab1 = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        tab2 = EditorTab(
            id="tab2", filename="file2.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab1)
        state.add_tab(tab2)
        state.active_tab_id = "tab1"

        state.remove_tab("tab1")
        assert state.active_tab_id == "tab2"

    def test_remove_only_tab_clears_active(self):
        """Test that removing the only tab clears active_tab_id."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)
        state.active_tab_id = "tab1"

        state.remove_tab("tab1")
        assert state.active_tab_id is None
        assert len(state.tabs) == 0

    def test_get_active_tab(self):
        """Test getting the active tab."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)
        state.active_tab_id = "tab1"

        active = state.get_active_tab()
        assert active is not None
        assert active.id == "tab1"

    def test_get_active_tab_returns_none_when_no_active(self):
        """Test get_active_tab returns None when no tab is active."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)
        # Don't set active_tab_id

        assert state.get_active_tab() is None

    def test_find_tab_by_path(self):
        """Test finding a tab by file path."""
        state = EditorTabsState()
        tab1 = EditorTab(
            id="tab1",
            filename="file1.py",
            file_path="/path/file1.py",
            content="",
            saved_content="",
        )
        tab2 = EditorTab(
            id="tab2",
            filename="file2.py",
            file_path="/path/file2.py",
            content="",
            saved_content="",
        )
        state.add_tab(tab1)
        state.add_tab(tab2)

        found = state.find_tab_by_path("/path/file2.py")
        assert found is not None
        assert found.id == "tab2"

    def test_find_tab_by_path_returns_none_for_unknown(self):
        """Test find_tab_by_path returns None for unknown path."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1",
            filename="file1.py",
            file_path="/path/file1.py",
            content="",
            saved_content="",
        )
        state.add_tab(tab)

        found = state.find_tab_by_path("/unknown/path.py")
        assert found is None

    def test_find_tab_by_path_with_none_path(self):
        """Test find_tab_by_path doesn't match tabs with None path."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1",
            filename="untitled.py",
            file_path=None,
            content="",
            saved_content="",
        )
        state.add_tab(tab)

        # Searching for None should not match
        found = state.find_tab_by_path(None)
        assert found is None

    def test_change_listener_called_on_add(self):
        """Test that change listeners are called when adding a tab."""
        state = EditorTabsState()
        calls = []
        state.add_change_listener(lambda: calls.append("called"))

        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)

        assert len(calls) == 1

    def test_change_listener_called_on_remove(self):
        """Test that change listeners are called when removing a tab."""
        state = EditorTabsState()
        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)

        calls = []
        state.add_change_listener(lambda: calls.append("called"))

        state.remove_tab("tab1")
        assert len(calls) == 1

    def test_remove_change_listener(self):
        """Test removing a change listener."""
        state = EditorTabsState()
        calls = []
        listener = lambda: calls.append("called")
        state.add_change_listener(listener)
        state.remove_change_listener(listener)

        tab = EditorTab(
            id="tab1", filename="file1.py", file_path=None, content="", saved_content=""
        )
        state.add_tab(tab)

        assert len(calls) == 0


class TestEditorTabsSingleton:
    """Tests for the global editor_tabs_state singleton."""

    def test_singleton_exists(self):
        """Test that the singleton is properly initialized."""
        assert editor_tabs_state is not None
        assert isinstance(editor_tabs_state, EditorTabsState)

    def test_singleton_is_usable(self):
        """Test that the singleton can be used."""
        # Clear any existing state first
        original_tabs = list(editor_tabs_state.tabs)
        original_active = editor_tabs_state.active_tab_id

        try:
            # Clear state for test
            editor_tabs_state.tabs.clear()
            editor_tabs_state.active_tab_id = None

            # Test functionality
            tab = EditorTab(
                id="singleton_test",
                filename="singleton.py",
                file_path=None,
                content="",
                saved_content="",
            )
            editor_tabs_state.add_tab(tab)
            editor_tabs_state.active_tab_id = "singleton_test"

            assert editor_tabs_state.get_active_tab() is not None
            assert editor_tabs_state.get_active_tab().filename == "singleton.py"
        finally:
            # Restore original state
            editor_tabs_state.tabs.clear()
            editor_tabs_state.tabs.extend(original_tabs)
            editor_tabs_state.active_tab_id = original_active

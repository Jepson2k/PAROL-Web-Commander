"""Unit tests for editor completion generation."""

import pytest


@pytest.mark.unit
def test_completions_have_required_fields() -> None:
    """Test that each completion has all required CodeMirror fields.

    Verifies that completions follow the CodeMirror completion item schema.
    """
    from parol_commander.components.editor import generate_completions_from_commands

    completions = generate_completions_from_commands()
    required_fields = {"label", "detail", "info", "apply", "type"}

    for completion in completions:
        assert isinstance(completion, dict), f"Expected dict, got {type(completion)}"
        missing = required_fields - set(completion.keys())
        assert not missing, (
            f"Completion {completion.get('label', '?')} missing fields: {missing}"
        )


@pytest.mark.unit
def test_completions_include_async_robot_client_methods() -> None:
    """Test that completions include methods from AsyncRobotClient.

    Verifies that robot commands are discovered and included.
    """
    from parol_commander.components.editor import generate_completions_from_commands
    from parol6 import AsyncRobotClient

    completions = generate_completions_from_commands()
    completion_labels = {c["label"] for c in completions}

    # Check that key AsyncRobotClient methods are present
    # These are common methods that should always exist
    expected_methods = ["home", "enable", "disable", "stop", "get_status"]

    for method in expected_methods:
        # Methods should be prefixed with "rbt."
        expected_label = f"rbt.{method}"
        assert expected_label in completion_labels, (
            f"Expected completion for {expected_label} not found"
        )


@pytest.mark.unit
def test_completions_have_function_type_for_methods() -> None:
    """Test that robot method completions have type='function'.

    Verifies that completion types are correctly set.
    """
    from parol_commander.components.editor import generate_completions_from_commands

    completions = generate_completions_from_commands()

    # Find a robot method completion (starts with "rbt." and has parentheses in label)
    robot_method_completions = [
        c for c in completions if c["label"].startswith("rbt.") and c["label"] != "rbt"
    ]

    assert len(robot_method_completions) > 0, (
        "Expected at least one robot method completion"
    )

    for completion in robot_method_completions:
        assert completion["type"] == "function", (
            f"Expected type='function' for {completion['label']}, got '{completion['type']}'"
        )


@pytest.mark.unit
def test_discover_robot_commands_returns_categorized_commands() -> None:
    """Test that discover_robot_commands returns commands with categories.

    Verifies the command discovery mechanism works correctly.
    """
    from parol_commander.components.editor import discover_robot_commands

    commands = discover_robot_commands()

    assert isinstance(commands, dict)
    assert len(commands) > 0, "Expected at least one command"

    # Check that each command has required metadata
    for name, cmd in commands.items():
        assert "title" in cmd, f"Command {name} missing 'title'"
        assert "category" in cmd, f"Command {name} missing 'category'"
        assert "signature" in cmd, f"Command {name} missing 'signature'"
        assert "docstring" in cmd, f"Command {name} missing 'docstring'"


@pytest.mark.unit
def test_categorize_command_returns_valid_categories() -> None:
    """Test that categorize_command returns valid category names.

    Verifies the categorization logic works for different method types.
    """
    from parol_commander.components.editor import categorize_command

    # Test known categorizations
    test_cases = [
        ("move_joints", "", "Motion"),
        ("jog_joint", "", "Motion"),
        ("smooth_move", "", "Smooth Motion"),
        ("get_status", "", "Query"),
        ("get_angles", "", "Query"),
        ("ping", "", "Query"),
        ("is_enabled", "", "Query"),
        ("wait_for_motion", "", "Query"),
        ("gripper_open", "", "Gripper"),
        ("gripper_close", "", "Gripper"),
        ("gcode_command", "", "GCODE"),
        ("enable", "", "Control & System"),
        ("disable", "", "Control & System"),
        ("home", "", "Control & System"),
        ("stop", "", "Control & System"),
        ("set_io", "", "IO"),
        ("unknown_method", "", "Other"),
    ]

    for method_name, doc, expected_category in test_cases:
        result = categorize_command(method_name, doc)
        assert result == expected_category, (
            f"categorize_command('{method_name}') returned '{result}', expected '{expected_category}'"
        )

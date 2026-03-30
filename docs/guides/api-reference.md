# API Reference

The interfaces below are defined in [waldoctl](https://github.com/Jepson2k/waldoctl), the abstraction layer that all backends implement. When you write `from parol6 import RobotClient` in a script, that `RobotClient` is a concrete subclass of `waldoctl.RobotClient` — it inherits the same methods documented here, plus any backend-specific extras. The same applies to `Robot`, tool specs, and status types.

In short: this reference covers everything available to your scripts regardless of which backend you're using.

## Robot

::: waldoctl.Robot

## RobotClient

::: waldoctl.RobotClient

## DryRunClient

::: waldoctl.DryRunClient

## Joint Configuration

::: waldoctl.JointsSpec

::: waldoctl.JointLimits

::: waldoctl.PositionLimits

::: waldoctl.KinodynamicLimits

::: waldoctl.HomePosition

::: waldoctl.CartesianKinodynamicLimits

::: waldoctl.LinearAngularLimits

## Results

::: waldoctl.IKResult

::: waldoctl.DryRunResult

::: waldoctl.IKResultData

::: waldoctl.DryRunResultData

## Status

::: waldoctl.StatusBuffer

::: waldoctl.PingResult

::: waldoctl.ToolResult

::: waldoctl.ActionState

## Tools

::: waldoctl.ToolSpec

::: waldoctl.ToolsSpec

::: waldoctl.GripperTool

::: waldoctl.PneumaticGripperTool

::: waldoctl.ElectricGripperTool

::: waldoctl.ToolType

::: waldoctl.GripperType

::: waldoctl.ActivationType

::: waldoctl.ToggleMode

::: waldoctl.ToolState

::: waldoctl.ToolStatus

::: waldoctl.ToolVariant

::: waldoctl.MeshSpec

::: waldoctl.MeshRole

::: waldoctl.LinearMotion

::: waldoctl.RotaryMotion

::: waldoctl.ChannelDescriptor

## Types

::: waldoctl.types.Frame

::: waldoctl.types.Axis

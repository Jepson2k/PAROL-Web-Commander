# Writing Programs

This guide builds a single script from scratch, adding capabilities section by section. By the end you'll have a program that demos joint and Cartesian moves, curved paths, scan patterns, tool control, and precision orientation changes — a tour of what the robot can do.

For the full method reference, see the [API Reference](api-reference.md).

## Connect and home

Every program starts by importing the backend's sync client and connecting to the controller. The editor pre-fills the host and port from your configuration.

```python
import math
import time
from parol6 import RobotClient

rbt = RobotClient(host='127.0.0.1', port=5001)

print("Angles:", rbt.get_angles())
print("Pose:", rbt.get_pose_rpy())

rbt.home()
```

`home()` blocks until all joints reach the home position. All motion commands block by default — the sync client sets `wait=True` unless you say otherwise.

<!-- video: connect and home -->

## moveJ vs moveL

`moveJ` interpolates in joint space — each joint takes the shortest path to its target angle. `moveL` moves the TCP in a straight line through Cartesian space. The difference is visible: moveJ produces a curved TCP path, moveL produces a straight one.

To show this, we'll move across the X axis with moveJ, then back with moveL. Watch the TCP path in the 3D view — the moveJ arc vs the moveL straight line.

```python
# moveJ across — TCP follows a curved arc
rbt.moveJ([-60, -90, 90, 0, 45, 0], speed=0.5)

# moveL back — TCP travels in a straight line
rbt.moveL([60, 280, 200, 90, 0, 90], speed=0.5)
```

`speed` is normalized 0.0–1.0. You can also use `duration` (seconds) or `accel` (0.0–1.0). Relative moves offset from the current position with `rel=True`, and `frame="TRF"` switches to the Tool Reference Frame for Cartesian moves.

<!-- video: moveJ vs moveL -->

## Curved motion

`moveC` draws a circular arc through a via-point to an end-point. `moveP` follows a series of waypoints at constant TCP speed. `moveS` fits a smooth spline through waypoints.

We'll draw three circles side by side, each with a different command, then thread a spline through all of them.

```python
ORIENTATION = [90, 0, 90]
RADIUS = 30
SPEED = 0.4

CENTERS = [(-70, 280, 200), (0, 280, 200), (70, 280, 200)]


def circle_pt(cx, cz, angle_deg):
    a = math.radians(angle_deg)
    return [cx + RADIUS * math.cos(a), 280, cz + RADIUS * math.sin(a)] + ORIENTATION


# Circle 1: single moveC — full circle in one command
cx, _, cz = CENTERS[0]
rbt.moveL(circle_pt(cx, cz, 0), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 180), end=circle_pt(cx, cz, 0), speed=SPEED)

# Circle 2: two moveC arcs — half-circles joined
cx, _, cz = CENTERS[1]
rbt.moveL(circle_pt(cx, cz, 0), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 90), end=circle_pt(cx, cz, 180), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 270), end=circle_pt(cx, cz, 0), speed=SPEED)

# Circle 3: moveP through 12 waypoints — constant TCP speed
cx, _, cz = CENTERS[2]
waypoints = [circle_pt(cx, cz, i * 30) for i in range(12)]
waypoints.append(waypoints[0])
rbt.moveL(waypoints[0], speed=SPEED)
rbt.moveP(waypoints, speed=SPEED)

# Spline threading through all three circles
spline = []
for cx, _, cz in CENTERS:
    for angle in range(0, 360, 45):
        spline.append(circle_pt(cx, cz, angle))
spline.append(spline[0])
rbt.moveL(spline[0], speed=SPEED)
rbt.moveS(spline, speed=SPEED)
```

These commands raise `NotImplementedError` on backends that don't support them.

<!-- video: curved motion -->

## Zig-zag scan with blend radius

Now we rotate to a new workspace area and do a raster scan. The blend radius `r` parameter rounds the corners so the robot doesn't stop at each turn — it blends the end of one move into the start of the next.

Setting `wait=False` queues moves so the controller can blend them. `wait_motion_complete()` blocks until the queue drains.

```python
# Rotate to a new area
rbt.moveJ([90, -90, 90, 0, 45, 0], speed=0.5)

ROWS = 5
X_MIN, X_MAX = -80, 80
Z_MIN, Z_MAX = 150, 250
Y = 280
BLEND = 15

start = [X_MIN, Y, Z_MAX + 30] + ORIENTATION
rbt.moveL(start, speed=0.5)

z_step = (Z_MAX - Z_MIN) / (ROWS - 1)

for row in range(ROWS):
    z = Z_MAX - row * z_step
    is_last = row == ROWS - 1
    x_start, x_end = (X_MIN, X_MAX) if row % 2 == 0 else (X_MAX, X_MIN)

    rbt.moveL([x_start, Y, z] + ORIENTATION, speed=0.5, r=BLEND, wait=False)
    rbt.moveL([x_end, Y, z] + ORIENTATION, speed=0.5, r=0 if is_last else BLEND, wait=False)

rbt.wait_motion_complete()
```

<!-- video: zig-zag scan -->

## Tool control

Rotate to another area, attach a tool, and work with it. `set_tool` selects the active end-effector and updates the TCP offset. The `tool` object gives you `open()`, `close()`, and `set_position()`.

```python
# Rotate to a new area
rbt.moveJ([180, -90, 90, 0, 45, 0], speed=0.5)

rbt.set_tool("SSG-48")

rbt.tool.open()
time.sleep(0.5)
rbt.tool.close()
time.sleep(0.5)
rbt.tool.set_position(0.5)  # 0.0 = fully open, 1.0 = fully closed
```

Electric grippers accept `speed` and `current` keyword arguments for finer control.

<!-- video: tool control -->

## Precision orientation

Finally, we demo the robot's ability to maintain an exact TCP position while changing orientation — rotating around a fixed point in space. This is useful for inspection, welding, or any task where the tool tip stays put but the approach angle changes.

```python
# Move to a fixed point
TCP = [0, 250, 80]
rbt.moveL(TCP + [0, 0, 90], speed=1.0)

# Sweep RX: tilt forward and back while TCP stays at the same XYZ
rbt.moveL(TCP + [-50, 0, 90], speed=1.0)
rbt.moveL(TCP + [50, 0, 90], speed=1.0)
rbt.moveL(TCP + [0, 0, 90], speed=1.0)

# Sweep RY: tilt side to side
rbt.moveL(TCP + [0, 50, 90], speed=1.0)
rbt.moveL(TCP + [0, -50, 90], speed=1.0)
rbt.moveL(TCP + [0, 0, 90], speed=1.0)

rbt.home()
print("Done!")
```

<!-- video: precision orientation -->

## The complete script

Here's everything together — one continuous program:

```python
import math
import time
from parol6 import RobotClient

rbt = RobotClient(host='127.0.0.1', port=5001)
ORIENTATION = [90, 0, 90]

# ── Home ──────────────────────────────────────────────────────────────
rbt.home()

# ── moveJ vs moveL ───────────────────────────────────────────────────
rbt.moveJ([-60, -90, 90, 0, 45, 0], speed=0.5)
rbt.moveL([60, 280, 200, 90, 0, 90], speed=0.5)

# ── Curved motion: three circles + spline ─────────────────────────────
RADIUS = 30
SPEED = 0.4
CENTERS = [(-70, 280, 200), (0, 280, 200), (70, 280, 200)]


def circle_pt(cx, cz, angle_deg):
    a = math.radians(angle_deg)
    return [cx + RADIUS * math.cos(a), 280, cz + RADIUS * math.sin(a)] + ORIENTATION


cx, _, cz = CENTERS[0]
rbt.moveL(circle_pt(cx, cz, 0), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 180), end=circle_pt(cx, cz, 0), speed=SPEED)

cx, _, cz = CENTERS[1]
rbt.moveL(circle_pt(cx, cz, 0), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 90), end=circle_pt(cx, cz, 180), speed=SPEED)
rbt.moveC(via=circle_pt(cx, cz, 270), end=circle_pt(cx, cz, 0), speed=SPEED)

cx, _, cz = CENTERS[2]
waypoints = [circle_pt(cx, cz, i * 30) for i in range(12)]
waypoints.append(waypoints[0])
rbt.moveL(waypoints[0], speed=SPEED)
rbt.moveP(waypoints, speed=SPEED)

spline = []
for cx, _, cz in CENTERS:
    for angle in range(0, 360, 45):
        spline.append(circle_pt(cx, cz, angle))
spline.append(spline[0])
rbt.moveL(spline[0], speed=SPEED)
rbt.moveS(spline, speed=SPEED)

# ── Zig-zag scan ──────────────────────────────────────────────────────
rbt.moveJ([90, -90, 90, 0, 45, 0], speed=0.5)

ROWS = 5
X_MIN, X_MAX = -80, 80
Z_MIN, Z_MAX = 150, 250
Y = 280
BLEND = 15

rbt.moveL([X_MIN, Y, Z_MAX + 30] + ORIENTATION, speed=0.5)
z_step = (Z_MAX - Z_MIN) / (ROWS - 1)
for row in range(ROWS):
    z = Z_MAX - row * z_step
    is_last = row == ROWS - 1
    x_start, x_end = (X_MIN, X_MAX) if row % 2 == 0 else (X_MAX, X_MIN)
    rbt.moveL([x_start, Y, z] + ORIENTATION, speed=0.5, r=BLEND, wait=False)
    rbt.moveL([x_end, Y, z] + ORIENTATION, speed=0.5, r=0 if is_last else BLEND, wait=False)
rbt.wait_motion_complete()

# ── Tool control ──────────────────────────────────────────────────────
rbt.moveJ([180, -90, 90, 0, 45, 0], speed=0.5)
rbt.set_tool("SSG-48")
rbt.tool.open()
time.sleep(0.5)
rbt.tool.close()
time.sleep(0.5)
rbt.tool.set_position(0.5)

# ── Precision orientation ─────────────────────────────────────────────
TCP = [0, 250, 80]
rbt.moveL(TCP + [0, 0, 90], speed=1.0)
rbt.moveL(TCP + [-50, 0, 90], speed=1.0)
rbt.moveL(TCP + [50, 0, 90], speed=1.0)
rbt.moveL(TCP + [0, 0, 90], speed=1.0)
rbt.moveL(TCP + [0, 50, 90], speed=1.0)
rbt.moveL(TCP + [0, -50, 90], speed=1.0)
rbt.moveL(TCP + [0, 0, 90], speed=1.0)

rbt.home()
print("Done!")
```

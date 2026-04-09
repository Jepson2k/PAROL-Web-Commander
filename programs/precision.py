"""Precision demo: TCP offset, TRF moves, and orientation rotations.

Picks up a pencil, offsets the TCP to the pencil tip, then demonstrates
linear and rotational moves in the Tool Reference Frame (TRF). The pencil
tip stays at a fixed point while the wrist rotates around it.
"""

from parol6 import RobotClient

rbt = RobotClient(host="127.0.0.1", port=5001)

rbt.select_tool("SSG-48")
rbt.tool.calibrate()
rbt.home()

PRECISION_POSE = [0, -250, 350, -90, 0, -90]
rbt.move_j(pose=PRECISION_POSE, speed=0.5)

# Test gripper: two quick close/open cycles
rbt.tool.close(speed=1.0)
rbt.tool.open(speed=1.0)
rbt.tool.close(speed=1.0)
rbt.tool.open(speed=1.0)

# Approach pencil: move_j to 100mm above, descend linearly, grab, retract
PENCIL_ABOVE = [-90, -81.6, 161.8, 0, -69.4, 180]
rbt.move_j(angles=PENCIL_ABOVE, speed=0.3)
rbt.move_l([0, 0, -100, 0, 0, 0], rel=True, speed=0.2)
rbt.tool.close(wait=True)
rbt.move_l([0, 0, 100, 0, 0, 0], rel=True, speed=0.2)
rbt.move_j(pose=PRECISION_POSE, speed=0.3)

# Offset TCP to pencil tip (~100mm exposed below gripper)
rbt.set_tcp_offset(-100, 0, 0)

# Pencil tip traces straight lines (linear precision demo)
# Forward/back (tool Z = world -Y at this pose)
rbt.move_l([0, 0, 100, 0, 0, 0], speed=0.3, frame="TRF", rel=True)
rbt.move_l([0, 0, -200, 0, 0, 0], speed=0.3, frame="TRF", rel=True)
rbt.move_l([0, 0, 100, 0, 0, 0], speed=0.3, frame="TRF", rel=True)
# Side to side (tool Y = world -X at this pose)
rbt.move_l([0, 60, 0, 0, 0, 0], speed=0.3, frame="TRF", rel=True)
rbt.move_l([0, -120, 0, 0, 0, 0], speed=0.3, frame="TRF", rel=True)
rbt.move_l([0, 60, 0, 0, 0, 0], speed=0.3, frame="TRF", rel=True)

# Precision TRF rotations — pencil tip stays stationary while wrist rotates
SWEEP = 20
for axis in range(3):
    delta = [0, 0, 0, 0, 0, 0]
    delta[3 + axis] = -SWEEP
    rbt.move_l(delta, speed=0.5, frame="TRF", rel=True)
    delta[3 + axis] = SWEEP
    rbt.move_l(delta, speed=0.5, frame="TRF", rel=True)
    rbt.move_l(delta, speed=0.5, frame="TRF", rel=True)
    delta[3 + axis] = -SWEEP
    rbt.move_l(delta, speed=0.5, frame="TRF", rel=True)

# Place pencil back: descend linearly, release, retract
rbt.set_tcp_offset(0, 0, 0)
rbt.move_j(angles=PENCIL_ABOVE, speed=0.3)
rbt.move_l([0, 0, -100, 0, 0, 0], rel=True, speed=0.2)
rbt.tool.open(wait=True)
rbt.move_l([0, 0, 100, 0, 0, 0], rel=True, speed=0.2)

rbt.move_j(pose=PRECISION_POSE, speed=0.3)
rbt.home()
print("Done!")

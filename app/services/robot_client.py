from __future__ import annotations

import socket
from typing import Optional, List


class RobotClient:
    """
    Thin UDP client for the PAROL6 headless controller.

    This client mirrors the subset of the protocol used by the initial NiceGUI dashboard:
      - HOME / STOP
      - GET_ANGLES / GET_IO / GET_GRIPPER
      - SET_PORT (best-effort; requires server support)
      - ENABLE / DISABLE / CLEAR_ERROR (requires server support)

    Notes:
      - Methods that expect a response use a request/response pattern with a short timeout.
      - Methods that do not require a response simply send a datagram and return a confirmation string.
      - Host and port are configurable to support non-local deployments during development.
    """

    def __init__(self, host: str, port: int, timeout: float = 2.0, retries: int = 1) -> None:
        self.host = host
        self.port = port
        self.timeout = timeout
        self.retries = retries

    # --------------- Internal helpers ---------------

    def _send(self, message: str) -> str:
        """Fire-and-forget UDP send."""
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
            sock.sendto(message.encode("utf-8"), (self.host, self.port))
        return f"Sent: {message}"

    def _request(self, message: str, bufsize: int = 2048) -> Optional[str]:
        """Send a request and wait for a UDP response (with retry)."""
        last_err: Optional[Exception] = None
        for _ in range(self.retries + 1):
            try:
                with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as sock:
                    sock.settimeout(self.timeout)
                    sock.sendto(message.encode("utf-8"), (self.host, self.port))
                    data, _ = sock.recvfrom(bufsize)
                    return data.decode("utf-8")
            except socket.timeout as e:
                last_err = e
                continue
            except Exception as e:
                last_err = e
                break
        return None

    # --------------- Motion / Control ---------------

    def home(self) -> str:
        return self._send("HOME")

    def stop(self) -> str:
        return self._send("STOP")

    def enable(self) -> str:
        """Requires server support for ENABLE command."""
        return self._send("ENABLE")

    def disable(self) -> str:
        """Requires server support for DISABLE command."""
        return self._send("DISABLE")

    def clear_error(self) -> str:
        """Requires server support for CLEAR_ERROR command."""
        return self._send("CLEAR_ERROR")

    def set_com_port(self, port_str: str) -> str:
        """
        Best-effort COM port change. Requires server support to take effect immediately.
        For current controller versions, the UI also seeds com_port.txt on start via ServerManager.
        """
        if not port_str:
            return "No port provided"
        return self._send(f"SET_PORT|{port_str}")

    # --------------- Status / Queries ---------------

    def get_angles(self) -> Optional[List[float]]:
        """
        Returns list of 6 angles in degrees or None on failure.
        Expected wire format: "ANGLES|j1,j2,j3,j4,j5,j6"
        """
        resp = self._request("GET_ANGLES", bufsize=1024)
        if not resp:
            return None
        parts = resp.split("|")
        if len(parts) != 2 or parts[0] != "ANGLES":
            return None
        try:
            return [float(v) for v in parts[1].split(",")]
        except Exception:
            return None

    def get_io(self) -> Optional[List[int]]:
        """
        Returns [IN1, IN2, OUT1, OUT2, ESTOP] or None on failure.
        Expected wire format: "IO|in1,in2,out1,out2,estop"
        """
        resp = self._request("GET_IO", bufsize=1024)
        if not resp:
            return None
        parts = resp.split("|")
        if len(parts) != 2 or parts[0] != "IO":
            return None
        try:
            return [int(v) for v in parts[1].split(",")]
        except Exception:
            return None

    def get_gripper_status(self) -> Optional[List[int]]:
        """
        Returns [ID, Position, Speed, Current, StatusByte, ObjectDetected] or None.
        Expected wire format: "GRIPPER|id,pos,spd,cur,status,obj"
        """
        resp = self._request("GET_GRIPPER", bufsize=1024)
        if not resp:
            return None
        parts = resp.split("|")
        if len(parts) != 2 or parts[0] != "GRIPPER":
            return None
        try:
            return [int(v) for v in parts[1].split(",")]
        except Exception:
            return None

    def get_status(self) -> Optional[dict]:
        """
        Aggregate status if supported by controller.
        Expected format:
          STATUS|POSE=p0,p1,...,p15|ANGLES=a0,...,a5|IO=in1,in2,out1,out2,estop|GRIPPER=id,pos,spd,cur,status,obj
        Returns dict with keys: pose (list[float] len=16), angles (list[float] len=6),
                                io (list[int] len=5), gripper (list[int] len>=6)
        """
        resp = self._request("GET_STATUS", bufsize=4096)
        if not resp or not resp.startswith("STATUS|"):
            return None
        try:
            # Split top-level sections after "STATUS|"
            sections = resp.split("|")[1:]
            result: dict[str, object] = {"pose": None, "angles": None, "io": None, "gripper": None}
            for sec in sections:
                if sec.startswith("POSE="):
                    vals = [float(x) for x in sec[len("POSE="):].split(",") if x]
                    result["pose"] = vals
                elif sec.startswith("ANGLES="):
                    vals = [float(x) for x in sec[len("ANGLES="):].split(",") if x]
                    result["angles"] = vals
                elif sec.startswith("IO="):
                    vals = [int(x) for x in sec[len("IO="):].split(",") if x]
                    result["io"] = vals
                elif sec.startswith("GRIPPER="):
                    vals = [int(x) for x in sec[len("GRIPPER="):].split(",") if x]
                    result["gripper"] = vals
            return result
        except Exception:
            return None

    def ping(self) -> bool:
        """True if the controller responds with a 'PONG' message."""
        resp = self._request("PING", bufsize=256)
        return bool(resp and resp.strip().upper().startswith("PONG"))

    # --------------- Extended controls / motion ---------------

    def move_joints(
        self,
        joint_angles: List[float],
        duration: Optional[float] = None,
        speed_percentage: Optional[int] = None,
        accel_percentage: Optional[int] = None,
        profile: Optional[str] = None,      # 'TRAP' or 'POLY'
        tracking: Optional[str] = None,     # 'SPEED' or 'POSITION'
    ) -> str:
        """
        Send a MOVEJOINT command with optional planning hints.
        Args:
            joint_angles: six angles in degrees
            duration: total time in seconds, or None
            speed_percentage: 1..100, or None (mutually exclusive with duration)
            accel_percentage: 1..100 (used for trapezoidal planning), optional
            profile: 'TRAP' or 'POLY' (poly = jtraj). Default is implementation-defined.
            tracking: 'SPEED' to stream velocities, otherwise position tracking.
        """
        angles_str = "|".join(str(a) for a in joint_angles)
        dur_str = "NONE" if duration is None else str(duration)
        spd_str = "NONE" if speed_percentage is None else str(speed_percentage)
        acc_str = "NONE" if accel_percentage is None else str(int(accel_percentage))
        prof_str = (profile or "NONE").upper()
        track_str = (tracking or "NONE").upper()
        return self._send(f"MOVEJOINT|{angles_str}|{dur_str}|{spd_str}|{acc_str}|{prof_str}|{track_str}")

    def move_pose(
        self,
        pose: List[float],
        duration: Optional[float] = None,
        speed_percentage: Optional[int] = None,
        accel_percentage: Optional[int] = None,
        profile: Optional[str] = None,      # 'TRAP' or 'POLY'
        tracking: Optional[str] = None,     # 'SPEED' or 'POSITION'
    ) -> str:
        """
        Send a MOVEPOSE command with optional planning hints.
        Args:
            pose: [x, y, z, rx, ry, rz] (mm/deg)
            duration/speed_percentage: mutually exclusive
            accel_percentage/profile/tracking: optional advanced parameters
        """
        pose_str = "|".join(str(v) for v in pose)
        dur_str = "NONE" if duration is None else str(duration)
        spd_str = "NONE" if speed_percentage is None else str(speed_percentage)
        acc_str = "NONE" if accel_percentage is None else str(int(accel_percentage))
        prof_str = (profile or "NONE").upper()
        track_str = (tracking or "NONE").upper()
        return self._send(f"MOVEPOSE|{pose_str}|{dur_str}|{spd_str}|{acc_str}|{prof_str}|{track_str}")

    def move_cartesian(
        self,
        pose: List[float],
        duration: Optional[float] = None,
        speed_percentage: Optional[float] = None,
        accel_percentage: Optional[int] = None,
        profile: Optional[str] = None,      # 'TRAP' or 'POLY' (used by controller if supported)
        tracking: Optional[str] = None,     # 'SPEED' or 'POSITION'
    ) -> str:
        """
        Send a MOVECART (straight-line) command.
        Provide either duration or speed_percentage (1..100).
        Optional: accel_percentage, trajectory profile, and tracking mode.
        """
        pose_str = "|".join(str(v) for v in pose)
        dur_str = "NONE" if duration is None else str(duration)
        spd_str = "NONE" if speed_percentage is None else str(speed_percentage)
        acc_str = "NONE" if accel_percentage is None else str(int(accel_percentage))
        prof_str = (profile or "NONE").upper()
        track_str = (tracking or "NONE").upper()
        return self._send(f"MOVECART|{pose_str}|{dur_str}|{spd_str}|{acc_str}|{prof_str}|{track_str}")

    def move_cartesian_rel_trf(
        self,
        deltas: List[float],                     # [dx, dy, dz, rx, ry, rz] in mm/deg relative to TRF
        duration: Optional[float] = None,
        speed_percentage: Optional[float] = None,
        accel_percentage: Optional[int] = None,
        profile: Optional[str] = None,
        tracking: Optional[str] = None,
    ) -> str:
        """
        Send a MOVECARTRELTRF (relative straight-line in TRF) command.
        Provide either duration or speed_percentage (1..100).
        Optional: accel_percentage, trajectory profile, and tracking mode.
        """
        delta_str = "|".join(str(v) for v in deltas)
        dur_str = "NONE" if duration is None else str(duration)
        spd_str = "NONE" if speed_percentage is None else str(speed_percentage)
        acc_str = "NONE" if accel_percentage is None else str(int(accel_percentage))
        prof_str = (profile or "NONE").upper()
        track_str = (tracking or "NONE").upper()
        return self._send(f"MOVECARTRELTRF|{delta_str}|{dur_str}|{spd_str}|{acc_str}|{prof_str}|{track_str}")

    def jog_joint(self, joint_index: int, speed_percentage: int, duration: Optional[float] = None, distance_deg: Optional[float] = None) -> str:
        """
        Send a JOG command for a single joint (0..5 positive, 6..11 negative for reverse).
        duration and distance_deg are optional; at least one should be provided for one-shot jog.
        For press-and-hold UI, send short duration repeatedly.
        """
        dur_str = "NONE" if duration is None else str(duration)
        dist_str = "NONE" if distance_deg is None else str(distance_deg)
        return self._send(f"JOG|{joint_index}|{speed_percentage}|{dur_str}|{dist_str}")

    def jog_cartesian(self, frame: str, axis: str, speed_percentage: int, duration: float) -> str:
        """
        Send a CARTJOG command (frame 'TRF' or 'WRF', axis in {X+/X-/Y+/.../RZ-}).
        """
        return self._send(f"CARTJOG|{frame}|{axis}|{speed_percentage}|{duration}")

    def jog_multiple(self, joints: List[int], speeds: List[float], duration: float) -> str:
        """
        Send a MULTIJOG command to jog multiple joints simultaneously for 'duration' seconds.
        """
        joints_str = ",".join(str(j) for j in joints)
        speeds_str = ",".join(str(s) for s in speeds)
        return self._send(f"MULTIJOG|{joints_str}|{speeds_str}|{duration}")

    # --------------- IO / Gripper ---------------

    def control_pneumatic_gripper(self, action: str, port: int) -> str:
        """
        Control pneumatic gripper via digital outputs.
        action: 'open' or 'close'
        port: 1 or 2
        """
        action = action.lower()
        if action not in ("open", "close"):
            return "Invalid pneumatic action"
        if port not in (1, 2):
            return "Invalid pneumatic port"
        return self._send(f"PNEUMATICGRIPPER|{action}|{port}")

    def control_electric_gripper(
        self,
        action: str,
        position: Optional[int] = 255,
        speed: Optional[int] = 150,
        current: Optional[int] = 500,
    ) -> str:
        """
        Control electric gripper.
        action: 'move' or 'calibrate'
        position: 0..255
        speed: 0..255
        current: 100..1000 (mA)
        """
        action = action.lower()
        if action not in ("move", "calibrate"):
            return "Invalid electric gripper action"
        pos = 0 if position is None else int(position)
        spd = 0 if speed is None else int(speed)
        cur = 100 if current is None else int(current)
        return self._send(f"ELECTRICGRIPPER|{action}|{pos}|{spd}|{cur}")

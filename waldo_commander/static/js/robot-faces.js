/* Animated robot face system for the connection status indicator. */

function sleep(ms) { return new Promise(r => setTimeout(r, ms)); }
function rand(a, b) { return a + Math.random() * (b - a); }

class Robot {
  constructor(prefix) {
    this.prefix = prefix;
    this.eyeL = document.getElementById(`${prefix}-eyeL`);
    this.eyeR = document.getElementById(`${prefix}-eyeR`);
    this.pupilL = document.getElementById(`${prefix}-pupilL`);
    this.pupilR = document.getElementById(`${prefix}-pupilR`);
    this.blinkEl = document.getElementById(`${prefix}-blink`);
    this.squintEl = document.getElementById(`${prefix}-squint`) || null;
    this.droopEl = document.getElementById(`${prefix}-droop`) || null;
    this.mouthContainer = document.getElementById(`${prefix}-mouth`);
    this.antenna = document.getElementById(`${prefix}-antenna`);
    this.busy = false;
    if (this.mouthContainer) {
      for (const c of this.mouthContainer.children) {
        c.style.transition = 'opacity 0.35s ease';
      }
    }
  }

  movePupils(dx, dy, speed = 0.45) {
    const t = `translate(${dx}px, ${dy}px)`;
    if (this.pupilL) {
      this.pupilL.style.transition = `transform ${speed}s ease`;
      this.pupilL.style.transform = t;
    }
    if (this.pupilR) {
      this.pupilR.style.transition = `transform ${speed}s ease`;
      this.pupilR.style.transform = t;
    }
  }

  resetPupils(speed = 0.4) { this.movePupils(0, 0, speed); }

  setEyeSize(r, speed = 0.3) {
    if (this.eyeL) this.eyeL.setAttribute('r', r);
    if (this.eyeR) this.eyeR.setAttribute('r', r);
  }

  resetEyeSize(speed = 0.3) { this.setEyeSize(1.8, speed); }

  hideEyes() {
    [this.eyeL, this.eyeR, this.pupilL, this.pupilR].forEach(el => {
      if (el) el.style.opacity = '0';
    });
  }

  showEyes(speed = 0.25) {
    [this.eyeL, this.eyeR, this.pupilL, this.pupilR].forEach(el => {
      if (el) {
        el.style.transition = `opacity ${speed}s ease`;
        el.style.opacity = '1';
      }
    });
  }

  async blink(duration = 120) {
    this.hideEyes();
    if (this.blinkEl) this.blinkEl.setAttribute('opacity', '1');
    await sleep(duration);
    if (this.blinkEl) this.blinkEl.setAttribute('opacity', '0');
    this.showEyes(0.08);
  }

  async showOverlay(el, holdMs, fadeIn = 400) {
    if (!el) return;
    this.hideEyes();
    el.style.transition = `opacity ${fadeIn}ms ease`;
    el.setAttribute('opacity', '1');
    await sleep(holdMs);
    el.style.transition = 'opacity 0.3s ease';
    el.setAttribute('opacity', '0');
    this.showEyes(0.2);
  }

  setMouth(state, fadeMs = 300) {
    if (!this.mouthContainer) return;
    for (const c of this.mouthContainer.children) {
      c.style.transition = `opacity ${fadeMs}ms ease`;
      c.setAttribute('opacity', c.dataset.state === state ? '1' : '0');
    }
  }

  gentleWiggle(ms = 600) {
    const el = this.antenna;
    if (!el) return;
    el.style.transition = `transform ${ms * 0.3}ms ease`;
    el.style.transform = 'rotate(3deg)';
    setTimeout(() => {
      el.style.transition = `transform ${ms * 0.35}ms ease`;
      el.style.transform = 'rotate(-2deg)';
    }, ms * 0.3);
    setTimeout(() => {
      el.style.transition = `transform ${ms * 0.35}ms ease`;
      el.style.transform = 'rotate(0deg)';
    }, ms * 0.65);
  }

  gentleDroop(ms = 2500) {
    const el = this.antenna;
    if (!el) return;
    el.style.transition = `transform ${ms * 0.4}ms ease`;
    el.style.transform = 'rotate(-2.5deg) translateY(0.5px)';
    setTimeout(() => {
      el.style.transition = `transform ${ms * 0.6}ms ease`;
      el.style.transform = 'rotate(0deg) translateY(0)';
    }, ms * 0.5);
  }

  async perform(fn) {
    if (this.busy) return;
    this.busy = true;
    await fn();
    await sleep(500);
    this.busy = false;
  }
}

/* Active timer IDs so we can cancel on face switch. */
let _activeTimers = [];

function schedule(fn, minMs, maxMs) {
  const idx = _activeTimers.length;
  function tick() {
    fn();
    _activeTimers[idx] = setTimeout(tick, rand(minMs, maxMs));
  }
  _activeTimers[idx] = setTimeout(tick, rand(minMs, maxMs));
}

/**
 * Cancel all scheduled animations.
 */
window.stopRobotFace = function() {
  for (const id of _activeTimers) {
    clearTimeout(id);
  }
  _activeTimers = [];
};

/**
 * Initialize animations for a robot face.
 * @param {string} prefix - "happy", "neutral", or "sad"
 */
window.initRobotFace = function(prefix) {
  const r = new Robot(prefix);

  // Bail out if SVG elements aren't in the DOM yet
  if (!r.eyeL) return;

  // Set default mouth state
  if (prefix === 'happy') {
    r.setMouth('smile');
    scheduleHappy(r);
  } else if (prefix === 'neutral') {
    r.setMouth('flat');
    scheduleNeutral(r);
  } else if (prefix === 'sad') {
    r.setMouth('frown');
    scheduleSad(r);
  }
};

/* ===== HAPPY animations ===== */
function scheduleHappy(r) {
  // Regular blink
  schedule(() => r.perform(async () => {
    await r.blink(100);
  }), 2500, 5000);

  // Double blink
  schedule(() => r.perform(async () => {
    await r.blink(80);
    await sleep(120);
    await r.blink(80);
  }), 8000, 14000);

  // Squint + grin
  schedule(() => r.perform(async () => {
    r.setMouth('grin');
    await r.showOverlay(r.squintEl, 1800, 350);
    r.setMouth('smile');
  }), 6000, 12000);

  // Excited: big eyes + open-smile
  schedule(() => r.perform(async () => {
    r.setEyeSize(2.2);
    r.setMouth('open-smile');
    await sleep(1200);
    r.resetEyeSize();
    r.setMouth('smile');
  }), 10000, 18000);

  // Antenna wiggle
  schedule(() => r.gentleWiggle(), 4000, 9000);
}

/* ===== NEUTRAL animations ===== */
function scheduleNeutral(r) {
  // Regular blink
  schedule(() => r.perform(async () => {
    await r.blink(130);
  }), 3000, 6000);

  // Look left then right
  schedule(() => r.perform(async () => {
    r.movePupils(-0.5, 0, 0.5);
    await sleep(800);
    r.movePupils(0.5, 0, 0.6);
    await sleep(800);
    r.resetPupils();
  }), 5000, 10000);

  // Look up thinking
  schedule(() => r.perform(async () => {
    r.movePupils(0.3, -0.4, 0.5);
    r.setMouth('slant');
    await sleep(1500);
    r.resetPupils();
    r.setMouth('flat');
  }), 8000, 15000);

  // Suspicious squint + zigzag mouth
  schedule(() => r.perform(async () => {
    r.setEyeSize(1.3);
    r.setMouth('zigzag');
    await sleep(1600);
    r.resetEyeSize();
    r.setMouth('flat');
  }), 10000, 18000);

  // Mouth twitch
  schedule(() => r.perform(async () => {
    r.setMouth('slant');
    await sleep(600);
    r.setMouth('flat');
  }), 6000, 11000);

  // Antenna twitch
  schedule(() => r.gentleWiggle(400), 7000, 14000);
}

/* ===== SAD animations ===== */
function scheduleSad(r) {
  // Slow blink
  schedule(() => r.perform(async () => {
    await r.blink(200);
  }), 4000, 8000);

  // Look down
  schedule(() => r.perform(async () => {
    r.movePupils(0, 0.5, 0.6);
    await sleep(1800);
    r.resetPupils(0.5);
  }), 5000, 10000);

  // Look away
  schedule(() => r.perform(async () => {
    r.movePupils(-0.5, 0.2, 0.7);
    await sleep(1400);
    r.resetPupils(0.5);
  }), 7000, 13000);

  // Droopy eyes + deep frown
  schedule(() => r.perform(async () => {
    r.setMouth('deep-frown');
    await r.showOverlay(r.droopEl, 2200, 500);
    r.setMouth('frown');
  }), 8000, 15000);

  // Watery big eyes + tremble mouth
  schedule(() => r.perform(async () => {
    r.setEyeSize(2.1);
    r.setMouth('tremble');
    await sleep(1800);
    r.resetEyeSize();
    r.setMouth('frown');
  }), 12000, 20000);

  // Antenna droop
  schedule(() => r.gentleDroop(), 6000, 12000);
}

/* ===== Bouncing sad robot for the takeover overlay =====
 * DVD-screensaver-style: the face moves at a constant velocity and reflects
 * off the viewport edges and the centered card. raf-driven so the motion
 * stays smooth at any frame rate. The slow spin lives in CSS (.takeover-face
 * animation: takeover-spin), independent of position, so it composes with
 * the SVG's own breathing animation.
 */
window.startRobotMope = function() {
  // The face element may not be in the DOM yet when this runs (NiceGUI
  // dispatches run_javascript over the websocket; the DOM mount can lag
  // by a tick or two). Poll briefly before giving up.
  let tries = 0;
  function start() {
    const face = document.querySelector('.takeover-face');
    if (!face) {
      if (tries++ < 30) {
        setTimeout(start, 50);
      } else {
        console.warn('startRobotMope: .takeover-face not found');
      }
      return;
    }
    runMope(face);
  }
  start();
};

function runMope(face) {
  const FACE_SIZE = 96;
  const SPEED = 90;        // pixels per second
  const SPIN_DEG_PER_S = 30; // 360° every 12s, slow and steady
  const CARD_W = 460;      // approximate card footprint with breathing room
  const CARD_H = 320;

  function randomVelocity() {
    // Avoid axes — pick from a quadrant rotated by a random multiple of 90°.
    const angle = (0.15 + Math.random() * 0.7) * Math.PI / 2 +
                  Math.floor(Math.random() * 4) * Math.PI / 2;
    return { vx: Math.cos(angle) * SPEED, vy: Math.sin(angle) * SPEED };
  }

  function randomStart() {
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight / 2;
    const cardL = cx - CARD_W / 2, cardR = cx + CARD_W / 2;
    const cardT = cy - CARD_H / 2, cardB = cy + CARD_H / 2;
    for (let i = 0; i < 32; i++) {
      const px = Math.random() * (window.innerWidth - FACE_SIZE);
      const py = Math.random() * (window.innerHeight - FACE_SIZE);
      const intersects = px < cardR && px + FACE_SIZE > cardL &&
                         py < cardB && py + FACE_SIZE > cardT;
      if (!intersects) return { x: px, y: py };
    }
    return { x: 40, y: 40 };
  }

  let { x, y } = randomStart();
  let { vx, vy } = randomVelocity();
  let angle = 0;        // degrees
  let lastTime = null;

  // Initial paint so we don't flash at (0,0).
  face.style.transform = `translate(${x}px, ${y}px) rotate(${angle}deg)`;

  function step(now) {
    if (lastTime === null) {
      lastTime = now;
      requestAnimationFrame(step);
      return;
    }
    // Cap dt so a backgrounded tab doesn't teleport the face on resume.
    const dt = Math.min(0.05, (now - lastTime) / 1000);
    lastTime = now;

    let nx = x + vx * dt;
    let ny = y + vy * dt;
    angle = (angle + SPIN_DEG_PER_S * dt) % 360;

    // Viewport edges.
    const maxX = window.innerWidth - FACE_SIZE;
    const maxY = window.innerHeight - FACE_SIZE;
    if (nx < 0)    { nx = 0;    vx = Math.abs(vx); }
    if (nx > maxX) { nx = maxX; vx = -Math.abs(vx); }
    if (ny < 0)    { ny = 0;    vy = Math.abs(vy); }
    if (ny > maxY) { ny = maxY; vy = -Math.abs(vy); }

    // Card collision: push out along the axis of shallowest penetration.
    const cx = window.innerWidth / 2;
    const cy = window.innerHeight / 2;
    const cardL = cx - CARD_W / 2, cardR = cx + CARD_W / 2;
    const cardT = cy - CARD_H / 2, cardB = cy + CARD_H / 2;
    const overlapsCard = nx < cardR && nx + FACE_SIZE > cardL &&
                         ny < cardB && ny + FACE_SIZE > cardT;
    if (overlapsCard) {
      const penL = (nx + FACE_SIZE) - cardL;
      const penR = cardR - nx;
      const penT = (ny + FACE_SIZE) - cardT;
      const penB = cardB - ny;
      const minPen = Math.min(penL, penR, penT, penB);
      if (minPen === penL)      { nx = cardL - FACE_SIZE; vx = -Math.abs(vx); }
      else if (minPen === penR) { nx = cardR;             vx =  Math.abs(vx); }
      else if (minPen === penT) { ny = cardT - FACE_SIZE; vy = -Math.abs(vy); }
      else                      { ny = cardB;             vy =  Math.abs(vy); }
    }

    x = nx;
    y = ny;
    // Single transform string drives both translate and rotate so nothing
    // can race or get overridden by the browser's animation engine.
    face.style.transform = `translate(${x}px, ${y}px) rotate(${angle}deg)`;

    requestAnimationFrame(step);
  }

  requestAnimationFrame(step);
}

// Food house — 3D-офис будущего (Three.js).
// Экспортирует API через window.__officeResolve(api). Координаты плана — в «пикселях» 1200×720, как в 2D-версии.
import * as THREE from "three";
import { OrbitControls } from "./OrbitControls.js";

const W = 1200, H = 720, U = 40;              // 40 px плана = 1 метр
const X = x => (x - W / 2) / U, Z = y => (y - H / 2) / U;
const SPEED = 3.2;                             // м/с — бодрый шаг

const ROOMS = {
  marketer:  {x:0,   y:0,   w:225, h:300, door:"b", c:0xff4d8d},
  financier: {x:225, y:0,   w:225, h:300, door:"b", c:0x22c55e},
  accountant:{x:450, y:0,   w:225, h:300, door:"b", c:0xa78bfa},
  chef:      {x:675, y:0,   w:225, h:300, door:"b", c:0xf59e0b},
  smm:       {x:0,   y:420, w:300, h:300, door:"t", c:0xec4899},
  hr:        {x:300, y:420, w:300, h:300, door:"t", c:0x38bdf8},
  buyer:     {x:600, y:420, w:300, h:300, door:"t", c:0xeab308},
  director:  {x:900, y:0,   w:300, h:720, door:"l", c:0x22d3ee},
};
const LOOK = {
  director:  {suit:0x1e293b, pants:0x0f172a, hair:0x2b1d14, skin:0xe8b98f},
  marketer:  {suit:0x9f1239, pants:0x1e293b, hair:0x6b3b1e, skin:0xf1c7a1},
  financier: {suit:0x14532d, pants:0x111827, hair:0x111111, skin:0xd9a27b},
  accountant:{suit:0x4c1d95, pants:0x1f2937, hair:0xa0522d, skin:0xf3cfb0},
  chef:      {suit:0xf1f5f9, pants:0x334155, hair:0x3d2a1c, skin:0xe0ac84},
  smm:       {suit:0x831843, pants:0x1e3a5f, hair:0xe8c35a, skin:0xf6d2b6},
  hr:        {suit:0x075985, pants:0x374151, hair:0x2a1a10, skin:0xc98d64},
  buyer:     {suit:0x92400e, pants:0x44403c, hair:0x5a4030, skin:0xe9bb94},
};
const HOLO = {marketer:"📣", financier:"📈", accountant:"🧾", chef:"🍳", smm:"🎬", hr:"🤝", buyer:"📦", director:"🌐"};

function seatOf(k){
  const r = ROOMS[k];
  if (k === "director") return {x:1125, y:420, dir:-1};
  return r.y === 0 ? {x:r.x+60, y:200, dir:1} : {x:r.x+90, y:610, dir:1};
}
const VISIT = [985, 420], BOARD = [1060, 92], COFFEE = [70, 372];
function doorPath(k){ const r = ROOMS[k], cx = r.x + r.w/2; return [[cx, r.y===0 ? r.y+r.h-30 : r.y+30], [cx, 362]]; }
function toDirector(k){ return [...doorPath(k), [880,362], [930,375], VISIT]; }
function homeFrom(path, k){ const s = seatOf(k); return [...path.slice(0,-1).reverse(), [s.x, s.y]]; }

// ---------------------------------------------------------------- сцена
let renderer, scene, camera, controls, overlay, container, clock;
const AGENTS = {}, SCREENS = {}, LABELS = [], FLOATERS = [];
let boardCanvas, boardTex, papers = [], busy = false, namesFn = k => k;

function mat(color, o = {}) { return new THREE.MeshStandardMaterial({color, roughness:.45, metalness:.35, ...o}); }
function glow(color, intensity = 1.6) { return new THREE.MeshStandardMaterial({color, emissive:color, emissiveIntensity:intensity, roughness:.3}); }

function wall(x1, y1, x2, y2, color){
  const len = Math.hypot(x2-x1, y2-y1) / U; if (len < .05) return;
  const g = new THREE.Group();
  const glass = new THREE.Mesh(new THREE.BoxGeometry(len, 1.25, .06),
    new THREE.MeshPhysicalMaterial({color:0x9be7ff, transparent:true, opacity:.13, roughness:.05, metalness:.1, transmission:.4}));
  glass.position.y = .62; g.add(glass);
  const rim = new THREE.Mesh(new THREE.BoxGeometry(len, .05, .08), glow(color, 2.2)); rim.position.y = 1.26; g.add(rim);
  const base = new THREE.Mesh(new THREE.BoxGeometry(len, .06, .1), glow(color, .9)); base.position.y = .03; g.add(base);
  g.position.set((X(x1)+X(x2))/2, 0, (Z(y1)+Z(y2))/2);
  g.rotation.y = -Math.atan2(y2-y1, x2-x1);
  scene.add(g);
}

function emojiSprite(e, size = 1){
  const c = document.createElement("canvas"); c.width = c.height = 128;
  const x = c.getContext("2d"); x.font = "96px 'Apple Color Emoji','Segoe UI Emoji','Noto Color Emoji',sans-serif";
  x.textAlign = "center"; x.textBaseline = "middle"; x.fillText(e, 64, 72);
  const s = new THREE.Sprite(new THREE.SpriteMaterial({map:new THREE.CanvasTexture(c), transparent:true, depthWrite:false}));
  s.scale.set(size, size, 1); return s;
}

function buildRoom(k){
  const r = ROOMS[k], cx = r.x + r.w/2, cy = r.y + r.h/2;
  // пол кабинета
  const floor = new THREE.Mesh(new THREE.PlaneGeometry(r.w/U - .1, r.h/U - .1),
    new THREE.MeshStandardMaterial({color:0x0b1220, emissive:r.c, emissiveIntensity:.05, roughness:.2, metalness:.6}));
  floor.rotation.x = -Math.PI/2; floor.position.set(X(cx), .005, Z(cy)); floor.receiveShadow = true; scene.add(floor);
  // стены с дверным проёмом
  const L = r.x, R = r.x + r.w, T = r.y, B = r.y + r.h;
  if (r.door === "b") { wall(L,B,cx-34,B,r.c); wall(cx+34,B,R,B,r.c); } else wall(L,B,R,B,r.c);
  if (r.door === "t") { wall(L,T,cx-34,T,r.c); wall(cx+34,T,R,T,r.c); } else wall(L,T,R,T,r.c);
  if (r.door === "l") { wall(L,T,L,330,r.c); wall(L,396,L,B,r.c); } else wall(L,T,L,B,r.c);
  wall(R,T,R,B,r.c);
  // световая полоса в проёме
  const dp = r.door === "l" ? [L, 363] : [cx, r.door === "b" ? B : T];
  const strip = new THREE.Mesh(new THREE.BoxGeometry(r.door === "l" ? .08 : 1.6, .02, r.door === "l" ? 1.6 : .08), glow(r.c, 3));
  strip.position.set(X(dp[0]), .02, Z(dp[1])); scene.add(strip);
  // свет кабинета
  const pl = new THREE.PointLight(r.c, 6, 9, 1.6); pl.position.set(X(cx), 2.6, Z(cy)); scene.add(pl);
  // стол, кресло, голо-экран
  const s = seatOf(k), big = k === "director", d = s.dir;
  const deskW = big ? 2.6 : 1.9, deskD = big ? 1.1 : .9;
  const desk = new THREE.Group();
  const top = new THREE.Mesh(new THREE.BoxGeometry(deskD, .06, deskW), new THREE.MeshPhysicalMaterial({color:0xcfe9ff, transparent:true, opacity:.35, roughness:.05, metalness:.2}));
  top.position.y = .82; desk.add(top);
  const edge = new THREE.Mesh(new THREE.BoxGeometry(deskD + .02, .015, deskW + .02), glow(r.c, 2.5)); edge.position.y = .79; desk.add(edge);
  const leg = new THREE.Mesh(new THREE.CylinderGeometry(.08, .18, .8, 16), mat(0x1f2937)); leg.position.y = .4; desk.add(leg);
  desk.position.set(X(s.x) + d * 1.25, 0, Z(s.y)); desk.traverse(o => { o.castShadow = true; o.receiveShadow = true; }); scene.add(desk);
  const scr = new THREE.Mesh(new THREE.PlaneGeometry(big ? 1.4 : 1.05, big ? .75 : .6),
    new THREE.MeshStandardMaterial({color:0x0a1a2f, emissive:r.c, emissiveIntensity:.25, transparent:true, opacity:.75, side:THREE.DoubleSide}));
  scr.position.set(X(s.x) + d * 1.55, 1.3, Z(s.y)); scr.rotation.y = d > 0 ? -Math.PI/2 : Math.PI/2; scene.add(scr);
  const frame = new THREE.LineSegments(new THREE.EdgesGeometry(scr.geometry), new THREE.LineBasicMaterial({color:r.c}));
  frame.position.copy(scr.position); frame.rotation.copy(scr.rotation); scene.add(frame);
  SCREENS[k] = {scr, c:r.c, on:false};
  const chair = new THREE.Group();
  const seat = new THREE.Mesh(new THREE.BoxGeometry(.5, .08, .5), mat(0x111827)); seat.position.y = .48; chair.add(seat);
  const back = new THREE.Mesh(new THREE.BoxGeometry(.08, .7, .5), mat(0x111827)); back.position.set(-d * .27, .85, 0); chair.add(back);
  const cr = new THREE.Mesh(new THREE.BoxGeometry(.09, .72, .52), glow(r.c, .6)); cr.position.set(-d * .3, .85, 0); cr.scale.set(.3, 1, 1); chair.add(cr);
  const pole = new THREE.Mesh(new THREE.CylinderGeometry(.04, .2, .46, 12), mat(0x334155)); pole.position.y = .23; chair.add(pole);
  chair.position.set(X(s.x), 0, Z(s.y)); scene.add(chair);
  if (big) { const st = new THREE.Group(); st.position.set(X(s.x) + d * 1.0, .86, Z(s.y) + .7); scene.add(st); papers = {group:st, n:0}; }
  // голограмма профессии
  const holo = emojiSprite(HOLO[k], big ? 1.4 : 1.1);
  const hx = k === "director" ? X(1000) : X(r.x + r.w - 50), hy = k === "director" ? Z(620) : Z(r.y + (r.y === 0 ? 70 : 230));
  holo.position.set(hx, 1.6, hy); scene.add(holo);
  const ring = new THREE.Mesh(new THREE.TorusGeometry(.45, .025, 8, 48), glow(r.c, 2.5)); ring.rotation.x = Math.PI/2; ring.position.set(hx, .05, hy); scene.add(ring);
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(.42, .42, 1.5, 32, 1, true),
    new THREE.MeshBasicMaterial({color:r.c, transparent:true, opacity:.07, side:THREE.DoubleSide, depthWrite:false}));
  beam.position.set(hx, .8, hy); scene.add(beam);
  FLOATERS.push({o:holo, base:1.6, ph:Math.random()*6});
  // табличка
  addLabel("plate", k, new THREE.Vector3(X(r.x) + .5, 1.4, Z(r.y) + .4));
}

function buildDirectorExtras(){
  // голо-доска
  boardCanvas = document.createElement("canvas"); boardCanvas.width = 512; boardCanvas.height = 280;
  boardTex = new THREE.CanvasTexture(boardCanvas);
  const b = new THREE.Mesh(new THREE.PlaneGeometry(4, 2.2), new THREE.MeshBasicMaterial({map:boardTex, transparent:true, opacity:.92, side:THREE.DoubleSide}));
  b.position.set(X(1060), 1.9, Z(18)); scene.add(b);
  const bf = new THREE.LineSegments(new THREE.EdgesGeometry(b.geometry), new THREE.LineBasicMaterial({color:0x22d3ee})); bf.position.copy(b.position); scene.add(bf);
  drawBoard("FOOD HOUSE · AI", ["▲ 24/7 ONLINE", "◆ AGENTS × 8", "● SYSTEM READY"]);
  // ядро ИИ — вращающаяся фигура
  const core = new THREE.Mesh(new THREE.IcosahedronGeometry(.55, 1), new THREE.MeshStandardMaterial({color:0x22d3ee, emissive:0x22d3ee, emissiveIntensity:1.2, wireframe:true}));
  core.position.set(X(1150), 1.6, Z(650)); scene.add(core); FLOATERS.push({o:core, base:1.6, ph:0, spin:true});
  // кофе-станция в коридоре
  const cm = new THREE.Mesh(new THREE.CylinderGeometry(.3, .35, 1.1, 24), glow(0xf97316, .5)); cm.position.set(X(30), .55, Z(335)); scene.add(cm);
  const cup = emojiSprite("☕", .7); cup.position.set(X(30), 1.45, Z(335)); scene.add(cup); FLOATERS.push({o:cup, base:1.45, ph:1});
}

function drawBoard(title, lines){
  const x = boardCanvas.getContext("2d"), w = boardCanvas.width, h = boardCanvas.height;
  x.clearRect(0, 0, w, h);
  const g = x.createLinearGradient(0, 0, w, h); g.addColorStop(0, "rgba(8,47,73,.85)"); g.addColorStop(1, "rgba(46,16,101,.85)");
  x.fillStyle = g; x.fillRect(0, 0, w, h);
  x.strokeStyle = "rgba(34,211,238,.35)"; for (let i = 0; i < w; i += 32) { x.beginPath(); x.moveTo(i, 0); x.lineTo(i, h); x.stroke(); }
  x.fillStyle = "#67e8f9"; x.font = "bold 34px system-ui"; x.fillText(title, 24, 50);
  x.fillStyle = "#e0f2fe"; x.font = "26px system-ui";
  lines.slice(0, 6).forEach((l, i) => x.fillText(l, 28, 100 + i * 36));
  boardTex.needsUpdate = true;
}

// ---------------------------------------------------------------- человечки
function limb(len, r, material){
  const g = new THREE.Group();
  const m = new THREE.Mesh(new THREE.CapsuleGeometry(r, len, 4, 10), material); m.position.y = -len/2 - r/2; m.castShadow = true; g.add(m);
  return g;
}
function makeAgent(k){
  const L = LOOK[k], c = ROOMS[k].c;
  const root = new THREE.Group(), body = new THREE.Group(); root.add(body);
  const suit = mat(L.suit, {metalness:.5, roughness:.35}), pants = mat(L.pants), skin = mat(L.skin, {metalness:0, roughness:.7});
  const torso = new THREE.Mesh(new THREE.CapsuleGeometry(.2, .42, 4, 12), suit); torso.position.y = 1.22; torso.scale.set(1.1, 1, .75); torso.castShadow = true; body.add(torso);
  const stripe = new THREE.Mesh(new THREE.BoxGeometry(.05, .45, .02), glow(c, 2.5)); stripe.position.set(0, 1.24, .155); body.add(stripe);
  const head = new THREE.Group(); head.position.y = 1.66; body.add(head);
  const skull = new THREE.Mesh(new THREE.SphereGeometry(.17, 20, 16), skin); skull.castShadow = true; head.add(skull);
  const hair = new THREE.Mesh(new THREE.SphereGeometry(.178, 20, 12, 0, Math.PI*2, 0, Math.PI/2.1), mat(L.hair, {metalness:.1})); hair.rotation.x = -.25; head.add(hair);
  const visor = new THREE.Mesh(new THREE.TorusGeometry(.165, .028, 8, 32, Math.PI * .9), glow(c, 3)); visor.rotation.set(0, Math.PI * .05 + Math.PI, 0); visor.position.set(0, .02, .0); visor.rotation.x = Math.PI/2; visor.rotation.z = Math.PI * .55; head.add(visor);
  const legL = limb(.42, .075, pants), legR = limb(.42, .075, pants); legL.position.set(-.11, .92, 0); legR.position.set(.11, .92, 0); body.add(legL, legR);
  const armL = limb(.38, .06, suit), armR = limb(.38, .06, suit); armL.position.set(-.29, 1.44, 0); armR.position.set(.29, 1.44, 0); body.add(armL, armR);
  [armL, armR].forEach(a => { const h = new THREE.Mesh(new THREE.SphereGeometry(.065, 10, 8), skin); h.position.y = -.56; a.add(h); });
  const folder = new THREE.Mesh(new THREE.BoxGeometry(.04, .32, .24), glow(c, 1.2)); folder.position.set(0, -.6, .1); folder.visible = false; armR.add(folder);
  const ring = new THREE.Mesh(new THREE.RingGeometry(.32, .38, 40), new THREE.MeshBasicMaterial({color:c, transparent:true, opacity:.6, side:THREE.DoubleSide})); ring.rotation.x = -Math.PI/2; ring.position.y = .02; root.add(ring);
  root.scale.setScalar(1.25); scene.add(root);
  const a = {k, root, body, head, legL, legR, armL, armR, folder, ring,
    x:0, y:0, rot:0, rotT:0, path:null, onArrive:null, phase:Math.random()*6, pose:null, sitting:false, carrying:false,
    chain:Promise.resolve(), state:"idle", trip:false};
  AGENTS[k] = a; addLabel("tag", k, null, a); addLabel("bubble", k, null, a);
  sitAt(a); return a;
}
function setPos(a, x, y){ a.x = x; a.y = y; a.root.position.set(X(x), 0, Z(y)); }
function faceDir(a, dx, dy){ a.rotT = Math.atan2(dx, dy); }
function sitAt(a){ const s = seatOf(a.k); setPos(a, s.x, s.y); a.rotT = a.rot = s.dir > 0 ? Math.PI/2 : -Math.PI/2; a.root.rotation.y = a.rot; a.sitting = true; a.path = null; }

const lerp = (a, b, t) => a + (b - a) * t;
function animateAgent(a, dt, t){
  // движение по пути
  if (a.path) {
    const [tx, ty] = a.path[0], dx = tx - a.x, dy = ty - a.y, d = Math.hypot(dx, dy), v = SPEED * U * dt;
    if (Math.abs(dx) + Math.abs(dy) > 1) faceDir(a, dx, dy);
    if (d <= v || d < .5) { setPos(a, tx, ty); a.path.shift(); if (!a.path.length) { a.path = null; const f = a.onArrive; a.onArrive = null; f && f(); } }
    else setPos(a, a.x + dx/d*v, a.y + dy/d*v);
  }
  let dr = a.rotT - a.rot; while (dr > Math.PI) dr -= Math.PI*2; while (dr < -Math.PI) dr += Math.PI*2;
  a.rot += dr * Math.min(1, dt * 8); a.root.rotation.y = a.rot;
  // позы: целевые углы
  const walking = !!a.path; let lL = 0, lR = 0, aL = 0, aR = 0, aRz = 0, aLz = 0, bodyY = 0, headX = 0, headZ = 0;
  if (walking) {
    a.phase += dt * 7.5; const s = Math.sin(a.phase);
    lL = s * .55; lR = -s * .55; aL = -s * .45; aR = s * .45; bodyY = Math.abs(Math.cos(a.phase)) * .045;
    if (a.carrying) aR = -1.1;
  } else if (a.sitting) {
    lL = lR = -1.5; bodyY = -.42; aL = aR = -1.15;
  }
  const p = a.pose;
  if (p === "typing") { const s = Math.sin(t * 22); aL = -1.15 + s * .12; aR = -1.15 - s * .12; headX = .12 + Math.sin(t * 2) * .04; }
  if (p === "thinking") { aR = -2.7; aRz = .35; headZ = -.12; headX = .1; }
  if (p === "writing") { aR = -2.35 + Math.sin(t * 9) * .2; aRz = Math.sin(t * 5) * .15; }
  if (p === "giving") { aR = -1.5; }
  if (p === "waving") { aR = -2.9; aRz = .4 + Math.sin(t * 10) * .35; }
  if (p === "stretch") { aL = aR = -3.0; aLz = -.2; aRz = .2; }
  const k = Math.min(1, dt * (walking ? 18 : 9));
  a.legL.rotation.x = lerp(a.legL.rotation.x, lL, k); a.legR.rotation.x = lerp(a.legR.rotation.x, lR, k);
  a.armL.rotation.x = lerp(a.armL.rotation.x, aL, k); a.armR.rotation.x = lerp(a.armR.rotation.x, aR, k);
  a.armL.rotation.z = lerp(a.armL.rotation.z, aLz, k); a.armR.rotation.z = lerp(a.armR.rotation.z, aRz, k);
  a.body.position.y = lerp(a.body.position.y, bodyY, k);
  a.head.rotation.x = lerp(a.head.rotation.x, headX, k); a.head.rotation.z = lerp(a.head.rotation.z, headZ, k);
  a.folder.visible = a.carrying;
  a.ring.material.opacity = .35 + Math.sin(t * 3 + a.phase) * .2;
}

// ---------------------------------------------------------------- подписи и облачка (HTML поверх 3D)
function addLabel(kind, k, pos, agent){
  const el = document.createElement("div"); el.className = "o3-" + kind;
  if (kind === "plate") el.dataset.k = k;
  overlay.appendChild(el);
  LABELS.push({kind, k, pos, agent, el});
  if (kind !== "bubble") el.textContent = namesFn(k);
}
const v3 = new THREE.Vector3();
function updateLabels(){
  const w = container.clientWidth, h = container.clientHeight;
  for (const L of LABELS) {
    if (L.agent) v3.set(L.agent.root.position.x, L.kind === "tag" ? 2.65 + L.agent.body.position.y * 1.25 : 3.0 + L.agent.body.position.y * 1.25, L.agent.root.position.z);
    else v3.copy(L.pos);
    v3.project(camera);
    const vis = v3.z < 1 && Math.abs(v3.x) < 1.2 && Math.abs(v3.y) < 1.2;
    L.el.style.display = vis ? "" : "none";
    if (vis) L.el.style.transform = `translate(${(v3.x * .5 + .5) * w}px, ${(-v3.y * .5 + .5) * h}px) translate(-50%, -100%)`;
    if (L.kind === "bubble") L.el.style.zIndex = L.el.classList.contains("show") ? 30 : 1;
  }
}

// ---------------------------------------------------------------- цикл
function frame(){
  const dt = Math.min(clock.getDelta(), .25), t = clock.elapsedTime;   // движение не зависит от частоты кадров
  for (const a of Object.values(AGENTS)) animateAgent(a, dt, t);
  for (const [k, s] of Object.entries(SCREENS)) {
    const target = s.on ? 1.4 + Math.sin(t * 6) * .3 : .25;
    s.scr.material.emissiveIntensity = lerp(s.scr.material.emissiveIntensity, target, .1);
  }
  for (const f of FLOATERS) { f.o.position.y = f.base + Math.sin(t * 1.6 + f.ph) * .12; if (f.spin) { f.o.rotation.y += dt * .6; f.o.rotation.x += dt * .25; } }
  if (window.__particles) window.__particles.rotation.y += dt * .02;
  controls.update();
  renderer.render(scene, camera);
  updateLabels();
  requestAnimationFrame(frame);
}
function resize(){
  const w = container.clientWidth, h = Math.max(360, Math.round(w * .6));
  container.style.height = h + "px"; renderer.setSize(w, h, false); camera.aspect = w / h; camera.updateProjectionMatrix();
}

// ---------------------------------------------------------------- API для страницы
const sleep = ms => new Promise(r => setTimeout(r, ms));
function act(k, fn){ const a = AGENTS[k]; return a.chain = a.chain.then(fn).catch(console.error); }
function walk(k, pts){
  return act(k, () => new Promise(res => { const a = AGENTS[k]; a.sitting = false; a.pose = null; a.path = pts.map(p => [...p]); a.onArrive = res; }));
}
function say(k, text, ms = 2800){
  const L = LABELS.find(l => l.kind === "bubble" && l.k === k); if (!L) return;
  L.el.textContent = text.length > 120 ? text.slice(0, 120) + "…" : text; L.el.classList.add("show");
  clearTimeout(L.t); L.t = setTimeout(() => L.el.classList.remove("show"), ms);
}
const screen = (k, on) => { if (SCREENS[k]) SCREENS[k].on = on; };
function sitDown(k){ const a = AGENTS[k]; sitAt(a); a.pose = null; }

function coffeeTrip(a){
  a.trip = true; const go = [...doorPath(a.k), COFFEE];
  walk(a.k, go);
  act(a.k, async () => { a.rotT = Math.PI; say(a.k, window.__t ? window.__t("s_coffee") : "☕", 2000); await sleep(2300); });
  walk(a.k, homeFrom(go, a.k));
  act(a.k, async () => { sitDown(a.k); a.trip = false; });
}
function idleLife(){
  setInterval(() => {
    if (busy) return;
    for (const a of Object.values(AGENTS)) {
      if (a.state !== "idle" || a.trip || !a.sitting) continue;
      const r = Math.random();
      if (a.k !== "director" && r < .05) coffeeTrip(a);
      else if (r < .3) { a.pose = "typing"; screen(a.k, true); setTimeout(() => { if (a.state === "idle" && !a.trip) { a.pose = null; screen(a.k, false); } }, 2600); }
      else if (r < .38) { a.pose = "thinking"; setTimeout(() => a.state === "idle" && (a.pose = null), 1800); }
      else if (r < .42) { a.pose = "stretch"; setTimeout(() => a.state === "idle" && (a.pose = null), 1200); }
    }
  }, 2300);
}

const API = {
  ok: true,
  setNames(fn){ namesFn = fn; for (const L of LABELS) if (L.kind !== "bubble") L.el.textContent = (L.kind === "plate" ? "◈ " : "") + fn(L.k); },
  setBusy(b){ busy = b; },
  say,
  async thinking(k, text){ const a = AGENTS[k]; a.state = "thinking"; await act(k, async () => { a.pose = "thinking"; say(k, text); await sleep(900); }); },
  async working(k, text){ const a = AGENTS[k]; a.state = "working"; await act(k, async () => { sitDown(k); a.pose = "typing"; screen(k, true); say(k, text, 3500); await sleep(400); }); },
  async idle(k){ const a = AGENTS[k]; a.state = "idle"; await act(k, async () => { a.pose = null; screen(k, false); }); },
  async plan(names, comment, title, waitText){
    const D = AGENTS.director, s = seatOf("director");
    await walk("director", [[1060, 330], BOARD]);
    await act("director", async () => {
      D.rotT = Math.PI; D.pose = "writing"; drawBoard(title, names.map(n => "→ " + n));
      say("director", comment, 3200); await sleep(2400); D.pose = null;
    });
    await walk("director", [[1060, 330], [s.x, s.y]]);
    await act("director", async () => { sitDown("director"); say("director", waitText, 2500); });
  },
  async fetchTask(k, giveText, takeText){
    const a = AGENTS[k], D = AGENTS.director, go = toDirector(k);
    await act(k, async () => { a.pose = null; screen(k, false); });
    await walk(k, go);
    await act(k, async () => {
      a.rotT = Math.PI/2; D.pose = "giving"; say("director", giveText, 2000); await sleep(1100);
      D.pose = null; a.carrying = true; say(k, takeText, 1500); await sleep(700);
    });
    await walk(k, homeFrom(go, k));
    await act(k, async () => { a.carrying = false; sitDown(k); });
  },
  async deliver(k, doneText, thanksText){
    const a = AGENTS[k], go = toDirector(k);
    await act(k, async () => { a.pose = "stretch"; screen(k, false); say(k, doneText, 1500); await sleep(800); a.carrying = true; });
    await walk(k, go);
    await act(k, async () => {
      a.rotT = Math.PI/2; a.pose = "giving"; await sleep(700); a.carrying = false; a.pose = null;
      const sheet = new THREE.Mesh(new THREE.BoxGeometry(.3, .02, .22), glow(ROOMS[k].c, 1.5));
      sheet.position.y = papers.n++ * .03; papers.group.add(sheet);
      say("director", thanksText, 1800); await sleep(800);
    });
    walk(k, homeFrom(go, k)); act(k, async () => sitDown(k));
  },
  async summary(text){
    const D = AGENTS.director;
    await act("director", async () => {
      D.sitting = false; D.pose = "waving"; papers.group.clear(); papers.n = 0; say("director", text, 3500);
      await sleep(1800); D.pose = null; sitDown("director");
    });
  },
  reset(){ for (const a of Object.values(AGENTS)) { a.state = "idle"; a.pose = null; screen(a.k, false); } },
};

// ---------------------------------------------------------------- запуск
function init(){
  container = document.getElementById("office");
  const test = document.createElement("canvas");
  if (!(test.getContext("webgl2") || test.getContext("webgl"))) throw new Error("WebGL недоступен");
  renderer = new THREE.WebGLRenderer({antialias:true, alpha:false, powerPreference:"high-performance"});
  renderer.setPixelRatio(Math.min(window.devicePixelRatio, 1.75));
  renderer.shadowMap.enabled = true; renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  renderer.toneMapping = THREE.ACESFilmicToneMapping; renderer.toneMappingExposure = 1.1;
  container.innerHTML = ""; container.appendChild(renderer.domElement);
  overlay = document.createElement("div"); overlay.className = "o3-overlay"; container.appendChild(overlay);

  scene = new THREE.Scene(); scene.background = new THREE.Color(0x03060d); scene.fog = new THREE.Fog(0x03060d, 34, 70);
  camera = new THREE.PerspectiveCamera(40, 1.6, .1, 200); camera.position.set(1, 22, 19.5);
  controls = new OrbitControls(camera, renderer.domElement);
  controls.target.set(0, 0, .6); controls.enableDamping = true; controls.enablePan = false;
  controls.minDistance = 7; controls.maxDistance = 48; controls.maxPolarAngle = 1.2; controls.minPolarAngle = .35;

  scene.add(new THREE.AmbientLight(0x6b8cff, .35));
  scene.add(new THREE.HemisphereLight(0x7dd3fc, 0x0b0f1a, .55));
  const sun = new THREE.DirectionalLight(0xffffff, 1.1); sun.position.set(8, 20, 10); sun.castShadow = true;
  sun.shadow.mapSize.set(1024, 1024); Object.assign(sun.shadow.camera, {left:-18, right:18, top:12, bottom:-12, near:1, far:50}); scene.add(sun);

  // основание и неоновая сетка
  const ground = new THREE.Mesh(new THREE.PlaneGeometry(80, 60), new THREE.MeshStandardMaterial({color:0x050a14, roughness:.9}));
  ground.rotation.x = -Math.PI/2; ground.position.y = -.01; ground.receiveShadow = true; scene.add(ground);
  const grid = new THREE.GridHelper(80, 80, 0x0e7490, 0x0b2540); grid.position.y = -.005; grid.material.transparent = true; grid.material.opacity = .35; scene.add(grid);
  const corr = new THREE.Mesh(new THREE.PlaneGeometry(900/U, 120/U), new THREE.MeshStandardMaterial({color:0x0a1426, emissive:0x0ea5e9, emissiveIntensity:.06, metalness:.7, roughness:.25}));
  corr.rotation.x = -Math.PI/2; corr.position.set(X(450), .004, Z(360)); corr.receiveShadow = true; scene.add(corr);
  for (let i = 0; i < 18; i++) { const l = new THREE.Mesh(new THREE.BoxGeometry(.5, .01, .05), glow(0x22d3ee, 2)); l.position.set(X(25 + i * 50), .01, Z(360)); scene.add(l); }
  // частицы
  const pg = new THREE.BufferGeometry(), N = 400, arr = new Float32Array(N * 3);
  for (let i = 0; i < N; i++) { arr[i*3] = (Math.random() - .5) * 50; arr[i*3+1] = Math.random() * 10 + .5; arr[i*3+2] = (Math.random() - .5) * 36; }
  pg.setAttribute("position", new THREE.BufferAttribute(arr, 3));
  window.__particles = new THREE.Points(pg, new THREE.PointsMaterial({color:0x67e8f9, size:.06, transparent:true, opacity:.6}));
  scene.add(window.__particles);

  for (const k of Object.keys(ROOMS)) buildRoom(k);
  buildDirectorExtras();
  for (const k of Object.keys(ROOMS)) makeAgent(k);

  clock = new THREE.Clock();
  new ResizeObserver(resize).observe(container); resize();
  idleLife();
  requestAnimationFrame(frame);
}

try { init(); window.__officeResolve(API); }
catch (e) { console.error(e); window.__officeResolve(null, e); }

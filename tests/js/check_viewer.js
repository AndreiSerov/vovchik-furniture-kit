// Проверки скрипта страницы под node с настоящим three.js r147 (без WebGL): печатает JSON с результатами.
// Аргументы: путь к three.min.js, путь к viewer.js, путь к JSON с ключом scene3d (как у `kitchen 3d --json`).
"use strict";
const fs = require("fs");
const THREE = require(process.argv[2]);
const viewer = require(process.argv[3]);
const data = JSON.parse(fs.readFileSync(process.argv[4], "utf8")).scene3d;
const out = {};

// 1. Ориентация: зритель стоит в комнате перед стеной A (y > 0) и смотрит на неё.
const camera = new THREE.PerspectiveCamera(50, 1.5, 0.05, 300);
const p = viewer.toThree(1400, 3000, 1600);
camera.position.set(p[0], p[1], p[2]);
const look = viewer.toThree(1400, 0, 900);
camera.lookAt(look[0], look[1], look[2]);
camera.updateMatrixWorld(true);
const screenX = (x, y, z) => {
  const t = viewer.toThree(x, y, z);
  return new THREE.Vector3(t[0], t[1], t[2]).project(camera).x;
};
const centre = (m) => {
  const b = m.boxes[0];
  return [(b.min[0] + b.max[0]) / 2, (b.min[1] + b.max[1]) / 2, (b.min[2] + b.max[2]) / 2];
};
out.screen = {};
data.modules
  .filter((m) => m.wall === "A" && m.row === "lower")
  .forEach((m) => {
    out.screen[m.label] = { x: centre(m)[0], screenX: screenX(...centre(m)) };
  });
// стена Б начинается у конца A: для зрителя перед A она справа от A
out.wallAStartScreenX = screenX(data.walls[0].start[0], data.walls[0].start[1], 1000);
out.wallAEndScreenX = screenX(data.walls[0].end[0], data.walls[0].end[1], 1000);
const wallB = data.walls.find((w) => w.label === "Б");
out.wallBFarScreenX = screenX(wallB.end[0], wallB.end[1], 1000);

// 2. Стены: лицевая сторона смотрит в комнату; пол — вверх.
const built = viewer.buildScene(THREE, data, {});
const floor = data.floor;
const roomCentre = viewer.toThree((floor.min[0] + floor.max[0]) / 2, (floor.min[1] + floor.max[1]) / 2, 1000);
out.walls = built.walls.map((mesh) => {
  mesh.updateMatrixWorld(true);
  const normal = new THREE.Vector3(0, 0, 1).transformDirection(mesh.matrixWorld);
  const toCentre = new THREE.Vector3(...roomCentre).sub(mesh.position);
  return { label: mesh.userData.label, facing: normal.dot(toCentre), frontOnly: mesh.material.side === THREE.FrontSide };
});
built.floor.updateMatrixWorld(true);
out.floorNormalY = new THREE.Vector3(0, 0, 1).transformDirection(built.floor.matrixWorld).y;
out.modules = Object.keys(built.modules).length;
out.pickLabels = [...new Set(built.pickables.map((m) => m.userData.label))].sort();
out.pickableKinds = [...new Set(built.pickables.map((m) => m.userData.kind))];

// 3. Выделение и снятие.
viewer.select(built, "Н1");
const n1 = built.modules["Н1"].meshes[0].material.emissive.getHex();
viewer.select(built, "Н2");
const n1After = built.modules["Н1"].meshes[0].material.emissive.getHex();
const n2 = built.modules["Н2"].meshes[0].material.emissive.getHex();
const gapSelected = viewer.select(built, "ПМ1");
const gapOpacity = built.modules["ПМ1"].meshes[0].material.opacity;
const cleared = viewer.select(built, null);
out.select = { n1, n1After, n2, gapSelected, gapOpacity, cleared, gapAfter: built.modules["ПМ1"].meshes[0].material.opacity };

// 4. Камера: высота 1.6 м, вся кухня в кадре и для широкого, и для узкого (телефон) экрана.
for (const aspect of [1.6, 0.5]) {
  const cam = new THREE.PerspectiveCamera(55, aspect, 0.05, 300);
  const framed = viewer.frameCamera(THREE, cam, data, aspect);
  cam.updateMatrixWorld(true);
  let worst = 0;
  data.modules.forEach((m) =>
    m.boxes.forEach((b) => {
      for (const x of [b.min[0], b.max[0]])
        for (const y of [b.min[1], b.max[1]])
          for (const z of [b.min[2], b.max[2]]) {
            const t = viewer.toThree(x, y, z);
            const v = new THREE.Vector3(t[0], t[1], t[2]).project(cam);
            worst = Math.max(worst, Math.abs(v.x), Math.abs(v.y));
          }
    })
  );
  out["camera" + aspect] = { y: cam.position.y, worst, distance: framed.distance, target: framed.target.toArray() };
}
// 5. Метки на лицевой грани и габариты коробки (ревью 42).
const lab = viewer.buildScene(THREE, data, { makeLabel: () => ({ texture: null, aspect: 0.375 }) });
out.labels = {};
for (const m of data.modules) {
  const plane = lab.modules[m.label].label;
  plane.updateMatrixWorld(true);
  const normal = new THREE.Vector3(0, 0, 1).transformDirection(plane.matrixWorld);
  const b = m.boxes[0];
  const face = viewer.toThree(
    m.front[0] > 0 ? b.max[0] : m.front[0] < 0 ? b.min[0] : (b.min[0] + b.max[0]) / 2,
    m.front[1] > 0 ? b.max[1] : m.front[1] < 0 ? b.min[1] : (b.min[1] + b.max[1]) / 2,
    (b.min[2] + b.max[2]) / 2
  );
  const dir = new THREE.Vector3(...viewer.toThree(m.front[0], m.front[1], 0)).normalize();
  out.labels[m.label] = {
    facing: normal.dot(dir),
    offset: plane.position.clone().sub(new THREE.Vector3(...face)).dot(dir),
  };
}
const g = lab.modules["Н1"].meshes[0].geometry.parameters;
out.n1box = [g.width, g.height, g.depth];
console.log(JSON.stringify(out));

// Проверки скрипта страницы на кухнях другой формы (одна стена, П-образная): печатает JSON по каждому файлу.
// Аргументы: путь к three.min.js, путь к viewer.js, затем пути к JSON с ключом scene3d.
"use strict";
const fs = require("fs");
const THREE = require(process.argv[2]);
const viewer = require(process.argv[3]);
const out = {};

for (const file of process.argv.slice(4)) {
  const data = JSON.parse(fs.readFileSync(file, "utf8")).scene3d;
  const built = viewer.buildScene(THREE, data, {});
  const floor = data.floor;
  const centre = viewer.toThree((floor.min[0] + floor.max[0]) / 2, (floor.min[1] + floor.max[1]) / 2, 1000);
  const walls = built.walls.map((mesh) => {
    mesh.updateMatrixWorld(true);
    const normal = new THREE.Vector3(0, 0, 1).transformDirection(mesh.matrixWorld);
    const toCentre = new THREE.Vector3(...centre).sub(mesh.position);
    return { label: mesh.userData.label, facing: normal.dot(toCentre), frontOnly: mesh.material.side === THREE.FrontSide };
  });
  const cameras = {};
  for (const aspect of [1.6, 0.5]) {
    const cam = new THREE.PerspectiveCamera(55, aspect, 0.05, 300);
    viewer.frameCamera(THREE, cam, data, aspect);
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
    cameras[aspect] = { y: cam.position.y, worst };
  }
  out[file] = {
    walls,
    modules: data.modules.length,
    pickLabels: [...new Set(built.pickables.map((m) => m.userData.label))].sort(),
    cameras,
  };
}
console.log(JSON.stringify(out));

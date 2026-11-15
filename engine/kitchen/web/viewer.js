/* 3D-вид кухни (тикет 42): рисует сцену `scene3d` (миллиметры, оси расстановки) средствами three.js r147.
 *
 * Оси расстановки: x вдоль первой стены, y от стены в комнату, z вверх. Для зрителя в комнате, смотрящего на
 * стену, такая тройка левая, а three.js правый (y вверх). Поэтому `toThree` не просто переименовывает оси, а
 * меняет y и z местами: это отражение, и кухня выглядит как настоящая комната, а не зеркальная. Оси
 * расстановки (Python) не менялись, план PDF (`views._plan`) тоже.
 *
 * Скрипт не обращается к сети. Файл работает и под node (тесты): `module.exports`. */
(function (root) {
  "use strict";

  var MM = 0.001; // мм → м
  var FOV = 55;
  var FIT = 0.92; // доля половины кадра, в которую должна уложиться кухня
  var SELECTED_EDGE = 0xff8800;
  var EDGE = 0x333333;

  /* Точка комнаты (x, y, z мм) → координаты three.js (м): X = x, Y = z (вверх), Z = y. */
  function toThree(x, y, z) {
    return [x * MM, z * MM, y * MM];
  }

  function vec(THREE, x, y, z) {
    var p = toThree(x, y, z);
    return new THREE.Vector3(p[0], p[1], p[2]);
  }

  function colorOf(THREE, hex) {
    return new THREE.Color(hex);
  }

  function addEdges(THREE, mesh, geometry) {
    var lines = new THREE.LineSegments(
      new THREE.EdgesGeometry(geometry),
      new THREE.LineBasicMaterial({ color: EDGE })
    );
    mesh.add(lines);
    return lines;
  }

  function addWall(THREE, scene, wall) {
    var dx = wall.end[0] - wall.start[0];
    var dy = wall.end[1] - wall.start[1];
    var length = Math.sqrt(dx * dx + dy * dy);
    var geometry = new THREE.PlaneGeometry(length * MM, wall.height * MM);
    // одна сторона: стена видна только изнутри комнаты и не заслоняет вид при вращении снаружи
    var material = new THREE.MeshLambertMaterial({ color: 0xe6e6e6, side: THREE.FrontSide });
    var mesh = new THREE.Mesh(geometry, material);
    var mx = (wall.start[0] + wall.end[0]) / 2;
    var my = (wall.start[1] + wall.end[1]) / 2;
    mesh.position.copy(vec(THREE, mx, my, wall.height / 2));
    mesh.lookAt(vec(THREE, mx + wall.normal[0], my + wall.normal[1], wall.height / 2));
    mesh.userData = { kind: "wall", label: wall.label };
    scene.add(mesh);
    return mesh;
  }

  function addFloor(THREE, scene, floor) {
    var w = floor.max[0] - floor.min[0];
    var d = floor.max[1] - floor.min[1];
    var mesh = new THREE.Mesh(
      new THREE.PlaneGeometry(w * MM, d * MM),
      new THREE.MeshLambertMaterial({ color: 0x9a9a9a, side: THREE.FrontSide })
    );
    mesh.rotation.x = -Math.PI / 2; // нормаль вверх
    mesh.position.copy(vec(THREE, (floor.min[0] + floor.max[0]) / 2, (floor.min[1] + floor.max[1]) / 2, -1));
    mesh.userData = { kind: "floor" };
    scene.add(mesh);
    return mesh;
  }

  function addModule(THREE, scene, module, opts) {
    var entry = { module: module, meshes: [], edges: [], label: null };
    module.boxes.forEach(function (box, index) {
      var sx = box.max[0] - box.min[0];
      var sy = box.max[1] - box.min[1];
      var sz = box.max[2] - box.min[2];
      var geometry = new THREE.BoxGeometry(sx * MM, sz * MM, sy * MM); // X = x, Y = z, Z = y
      var material = module.gap
        ? new THREE.MeshBasicMaterial({ color: colorOf(THREE, box.rgb), transparent: true, opacity: 0.18, depthWrite: false })
        : new THREE.MeshLambertMaterial({ color: colorOf(THREE, box.rgb) });
      var mesh = new THREE.Mesh(geometry, material);
      mesh.position.copy(
        vec(THREE, (box.min[0] + box.max[0]) / 2, (box.min[1] + box.max[1]) / 2, (box.min[2] + box.max[2]) / 2)
      );
      mesh.userData = { kind: "module", label: module.label, gap: module.gap, box: index };
      entry.edges.push(addEdges(THREE, mesh, geometry));
      scene.add(mesh);
      entry.meshes.push(mesh);
      if (index === 0 && opts.makeLabel) {
        entry.label = addLabel(THREE, scene, module, box, opts.makeLabel);
      }
    });
    return entry;
  }

  /* Метка на лицевой грани: плоскость чуть впереди грани, текст читается со стороны комнаты. */
  function addLabel(THREE, scene, module, box, makeLabel) {
    var made = makeLabel(module.label);
    if (!made) return null;
    var nx = module.front[0];
    var ny = module.front[1];
    var cx = (box.min[0] + box.max[0]) / 2;
    var cy = (box.min[1] + box.max[1]) / 2;
    var cz = (box.min[2] + box.max[2]) / 2;
    var half = (Math.abs(nx) * (box.max[0] - box.min[0]) + Math.abs(ny) * (box.max[1] - box.min[1])) / 2;
    var width = Math.min(module.width * 0.9 * MM, 0.4);
    var plane = new THREE.Mesh(
      new THREE.PlaneGeometry(width, width * made.aspect),
      new THREE.MeshBasicMaterial({
        map: made.texture,
        transparent: true,
        depthWrite: false,
        polygonOffset: true,
        polygonOffsetFactor: -2,
        polygonOffsetUnits: -2,
      })
    );
    var fx = cx + nx * (half + 3);
    var fy = cy + ny * (half + 3);
    plane.position.copy(vec(THREE, fx, fy, cz));
    plane.lookAt(vec(THREE, fx + nx, fy + ny, cz));
    plane.userData = { kind: "label", label: module.label };
    scene.add(plane);
    return plane;
  }

  /* Сцена three.js из данных. `opts.makeLabel(text)` → {texture, aspect} (в браузере; под node не задан). */
  function buildScene(THREE, data, opts) {
    opts = opts || {};
    var scene = new THREE.Scene();
    scene.background = new THREE.Color(0xf4f4f4);
    scene.add(new THREE.HemisphereLight(0xffffff, 0x888888, 0.95));
    var sun = new THREE.DirectionalLight(0xffffff, 0.45);
    sun.position.set(-3, 6, 4);
    scene.add(sun);
    var walls = data.walls.map(function (w) {
      return addWall(THREE, scene, w);
    });
    var floor = addFloor(THREE, scene, data.floor);
    var modules = {};
    var pickables = [];
    data.modules.forEach(function (m) {
      var entry = addModule(THREE, scene, m, opts);
      modules[m.label] = entry;
      entry.meshes.forEach(function (mesh) {
        pickables.push(mesh);
      });
    });
    return { scene: scene, walls: walls, floor: floor, modules: modules, pickables: pickables, selected: null };
  }

  /* Подсветка выбранного модуля; `label` = null снимает выделение. */
  function select(built, label) {
    var prev = built.selected && built.modules[built.selected];
    if (prev) paint(prev, false);
    built.selected = label && built.modules[label] ? label : null;
    var next = built.selected && built.modules[built.selected];
    if (next) paint(next, true);
    return built.selected;
  }

  function paint(entry, on) {
    entry.meshes.forEach(function (mesh) {
      var m = mesh.material;
      if (m.emissive) m.emissive.setHex(on ? 0x223a66 : 0x000000);
      else m.opacity = on ? 0.5 : 0.18;
    });
    entry.edges.forEach(function (lines) {
      lines.material.color.setHex(on ? SELECTED_EDGE : EDGE);
    });
  }

  /* Ставит камеру на высоте `data.camera.height` и отодвигает от угла по `direction`, пока вся кухня не
   * уложится в кадр (с запасом); смотрит на угол. Возвращает цель для OrbitControls. */
  function frameCamera(THREE, camera, data, aspect) {
    var cam = data.camera;
    camera.aspect = aspect;
    camera.updateProjectionMatrix();
    var target = vec(THREE, cam.target[0], cam.target[1], cam.target[2]);
    var corners = [];
    data.modules.forEach(function (m) {
      m.boxes.forEach(function (b) {
        [b.min[0], b.max[0]].forEach(function (x) {
          [b.min[1], b.max[1]].forEach(function (y) {
            [b.min[2], b.max[2]].forEach(function (z) {
              corners.push(vec(THREE, x, y, z));
            });
          });
        });
      });
    });
    var probe = new THREE.Vector3();
    var distance = 1500;
    for (; distance <= 60000; distance += 250) {
      camera.position.copy(
        vec(THREE, cam.target[0] + cam.direction[0] * distance, cam.target[1] + cam.direction[1] * distance, cam.height)
      );
      camera.lookAt(target);
      camera.updateMatrixWorld(true);
      var fits = corners.every(function (c) {
        probe.copy(c).project(camera);
        return probe.z < 1 && Math.abs(probe.x) <= FIT && Math.abs(probe.y) <= FIT;
      });
      if (fits) break;
    }
    return { target: target, distance: distance };
  }

  function makeLabelFactory(doc, THREE) {
    return function (text) {
      var canvas = doc.createElement("canvas");
      canvas.width = 256;
      canvas.height = 96;
      var ctx = canvas.getContext("2d");
      ctx.fillStyle = "rgba(255,255,255,0.88)";
      ctx.fillRect(4, 4, 248, 88);
      ctx.fillStyle = "#222222";
      ctx.textAlign = "center";
      ctx.textBaseline = "middle";
      var size = 60;
      ctx.font = "bold " + size + "px sans-serif";
      while (ctx.measureText(text).width > 232 && size > 16) {
        size -= 4;
        ctx.font = "bold " + size + "px sans-serif";
      }
      ctx.fillText(text, 128, 50);
      return { texture: new THREE.CanvasTexture(canvas), aspect: 96 / 256 };
    };
  }

  /* Запуск на странице: WebGL, вращение пальцами и мышью, нажатие на модуль. Любая ошибка — запасной текст. */
  function start(THREE, data, doc, win) {
    var fallback = doc.getElementById("fallback");
    try {
      var host = doc.getElementById("view");
      var renderer = new THREE.WebGLRenderer({ antialias: true });
      renderer.setPixelRatio(Math.min(win.devicePixelRatio || 1, 2));
      host.appendChild(renderer.domElement);
      var camera = new THREE.PerspectiveCamera(FOV, 1, 0.05, 300);
      var built = buildScene(THREE, data, { makeLabel: makeLabelFactory(doc, THREE) });
      var controls = new THREE.OrbitControls(camera, renderer.domElement);
      var info = doc.getElementById("info");
      var infoCaption = doc.getElementById("info-caption");
      var infoSize = doc.getElementById("info-size");
      var raycaster = new THREE.Raycaster();
      var pointer = new THREE.Vector2();

      var render = function () {
        renderer.render(built.scene, camera);
      };
      var resize = function () {
        var w = Math.max(host.clientWidth, 1);
        var h = Math.max(host.clientHeight, 1);
        renderer.setSize(w, h);
        camera.aspect = w / h;
        camera.updateProjectionMatrix();
        render();
      };
      var frame = function () {
        var w = Math.max(host.clientWidth, 1);
        var h = Math.max(host.clientHeight, 1);
        renderer.setSize(w, h);
        var framed = frameCamera(THREE, camera, data, w / h);
        controls.target.copy(framed.target);
        controls.minDistance = 0.4;
        controls.maxDistance = 60;
        controls.update();
        render();
      };
      var show = function (label) {
        select(built, label);
        var module = label && data.modules.filter(function (m) { return m.label === label; })[0];
        if (module) {
          infoCaption.textContent = module.caption;
          infoSize.textContent = module.size_label;
          info.style.display = "block";
        } else {
          info.style.display = "none";
        }
        render();
      };

      // нажатие: один палец или кнопка без сдвига; жест вращения и щипок модуль не выбирают
      var down = null;
      var active = 0;
      var many = false;
      var el = renderer.domElement;
      el.addEventListener("pointerdown", function (e) {
        active += 1;
        if (active > 1) many = true;
        if (active === 1) down = { x: e.clientX, y: e.clientY, t: Date.now() };
      });
      el.addEventListener("pointerup", function (e) {
        active = Math.max(active - 1, 0);
        if (active > 0 || !down) return;
        var tap = !many && Math.abs(e.clientX - down.x) < 8 && Math.abs(e.clientY - down.y) < 8 && Date.now() - down.t < 700;
        down = null;
        many = false;
        if (!tap) return;
        var rect = el.getBoundingClientRect();
        pointer.set(((e.clientX - rect.left) / rect.width) * 2 - 1, -((e.clientY - rect.top) / rect.height) * 2 + 1);
        raycaster.setFromCamera(pointer, camera);
        var hits = raycaster.intersectObjects(built.pickables, false);
        show(hits.length ? hits[0].object.userData.label : null);
      });
      el.addEventListener("pointercancel", function () {
        active = 0;
        down = null;
        many = false;
      });
      controls.addEventListener("change", render);
      win.addEventListener("resize", resize);
      frame();
      fallback.style.display = "none";
    } catch (error) {
      fallback.style.display = "block";
    }
  }

  var api = { toThree: toThree, buildScene: buildScene, frameCamera: frameCamera, select: select, start: start };
  root.KitchenViewer = api;
  if (typeof module !== "undefined" && module.exports) module.exports = api;
})(typeof self !== "undefined" ? self : this);

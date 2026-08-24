import { useEffect, useRef, useState } from "react";
import * as THREE from "three";
import { OrbitControls } from "three/examples/jsm/controls/OrbitControls.js";
import { ColladaLoader } from "three/examples/jsm/loaders/ColladaLoader.js";
import { GLTFLoader } from "three/examples/jsm/loaders/GLTFLoader.js";
import URDFLoader, { type URDFRobot } from "urdf-loader";

export type RobotModelDescriptor = {
  model: "a" | "m";
  version: "v1.0" | "v1.1" | "v1.2" | "v1.3";
  confidence: "detected" | "inferred" | "assumed";
  reason: string;
};

type CameraView = "front" | "side" | "top" | "reset";
const V13_COLLADA_MESHES = new Set([
  "EE_GR_TF.dae",
  "LINK_11_WY.dae",
  "LINK_12_WP.dae",
  "LINK_13_WR.dae",
  "LINK_18_WY.dae",
  "LINK_19_WP.dae",
  "LINK_20_WR.dae",
]);

type ViewerApi = {
  camera: THREE.PerspectiveCamera;
  controls: OrbitControls;
  renderer: THREE.WebGLRenderer;
  scene: THREE.Scene;
  robot: URDFRobot;
  render: () => void;
  fit: (view?: CameraView) => void;
};

function disposeObject(root: THREE.Object3D) {
  root.traverse((child) => {
    const mesh = child as THREE.Mesh;
    mesh.geometry?.dispose?.();
    const materials = Array.isArray(mesh.material) ? mesh.material : mesh.material ? [mesh.material] : [];
    materials.forEach((material) => {
      const candidate = material as THREE.Material & Record<string, unknown>;
      Object.values(candidate).forEach((value) => {
        if (value instanceof THREE.Texture) value.dispose();
      });
      material.dispose();
    });
  });
}

function modelLabel(model: RobotModelDescriptor): string {
  return `${model.model.toUpperCase()} Type · ${model.version.toUpperCase()}`;
}

function applyJointValues(api: ViewerApi, values: Record<string, number>, container: HTMLDivElement | null) {
  Object.keys(api.robot.joints).forEach((joint) => {
    api.robot.setJointValue(joint, values[joint] ?? 0);
  });
  api.robot.updateMatrixWorld(true);
  api.render();
  if (container) {
    container.dataset.jointSignature = Object.keys(values)
      .sort()
      .filter((joint) => Boolean(api.robot.joints[joint]))
      .map((joint) => `${joint}:${(api.robot.joints[joint].jointValue[0] ?? 0).toFixed(6)}`)
      .join("|");
  }
}

export function RobotViewer({
  model,
  jointValues,
  cursorLabel,
}: {
  model: RobotModelDescriptor;
  jointValues: Record<string, number>;
  cursorLabel: string;
}) {
  const containerRef = useRef<HTMLDivElement>(null);
  const apiRef = useRef<ViewerApi | null>(null);
  const jointValuesRef = useRef(jointValues);
  const [status, setStatus] = useState<"loading" | "ready" | "error">("loading");
  const [error, setError] = useState("");

  useEffect(() => {
    jointValuesRef.current = jointValues;
  }, [jointValues]);

  useEffect(() => {
    const container = containerRef.current;
    if (!container) return;
    let disposed = false;
    setStatus("loading");
    setError("");

    const scene = new THREE.Scene();
    scene.background = new THREE.Color(0x0b0e10);
    const camera = new THREE.PerspectiveCamera(42, 1, 0.02, 100);
    camera.up.set(0, 0, 1);
    const renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
    renderer.outputColorSpace = THREE.SRGBColorSpace;
    renderer.setPixelRatio(Math.min(window.devicePixelRatio || 1, 1.5));
    renderer.setSize(Math.max(container.clientWidth, 1), Math.max(container.clientHeight, 1), false);
    renderer.domElement.setAttribute("aria-label", "RB-Y1 3D 자세 뷰어");
    renderer.domElement.setAttribute("role", "img");
    container.appendChild(renderer.domElement);

    const controls = new OrbitControls(camera, renderer.domElement);
    controls.enableDamping = false;
    controls.enablePan = true;
    controls.enableRotate = true;
    controls.screenSpacePanning = true;
    controls.mouseButtons.LEFT = THREE.MOUSE.ROTATE;
    controls.mouseButtons.RIGHT = THREE.MOUSE.PAN;
    controls.minDistance = 0.35;
    controls.maxDistance = 12;

    scene.add(new THREE.HemisphereLight(0xeaf4f2, 0x1a2024, 1.55));
    const keyLight = new THREE.DirectionalLight(0xffffff, 2.4);
    keyLight.position.set(3, 2.5, 4.5);
    scene.add(keyLight);
    const fillLight = new THREE.DirectionalLight(0x89b8d8, 1.1);
    fillLight.position.set(-3, -2, 2.4);
    scene.add(fillLight);
    const grid = new THREE.GridHelper(6, 30, 0x52625f, 0x27302f);
    grid.rotation.x = Math.PI / 2;
    grid.position.z = -0.002;
    scene.add(grid);

    const render = () => {
      if (disposed) return;
      renderer.render(scene, camera);
      container.dataset.cameraPosition = camera.position.toArray().map((value) => value.toFixed(5)).join(",");
      container.dataset.cameraTarget = controls.target.toArray().map((value) => value.toFixed(5)).join(",");
    };
    controls.addEventListener("change", render);

    const manager = new THREE.LoadingManager();
    const colladaLoader = new ColladaLoader(manager);
    const gltfLoader = new GLTFLoader(manager);
    const urdfLoader = new URDFLoader(manager);
    const meshLoadErrors: string[] = [];
    urdfLoader.packages = "";
    urdfLoader.parseCollision = false;
    urdfLoader.loadMeshCb = (path, _manager, done) => {
      const meshName = path.split("/").pop() ?? "";
      if (model.model === "m" && model.version === "v1.3" && V13_COLLADA_MESHES.has(meshName)) {
        colladaLoader.load(
          path,
          (collada) => done(collada.scene),
          undefined,
          (reason) => {
            const meshError = reason instanceof Error ? reason : new Error(String(reason));
            meshLoadErrors.push(`${path}: ${meshError.message}`);
            done(new THREE.Object3D(), meshError);
          },
        );
        return;
      }
      const glbPath = path.replace(/\.[^/.]+$/, ".glb");
      gltfLoader.load(
        glbPath,
        (gltf) => done(gltf.scene),
        undefined,
        (reason) => {
          const meshError = reason instanceof Error ? reason : new Error(String(reason));
          meshLoadErrors.push(`${glbPath}: ${meshError.message}`);
          done(new THREE.Object3D(), meshError);
        },
      );
    };

    let robot: URDFRobot | null = null;
    const fit = (view: CameraView = "reset") => {
      if (!robot) return;
      robot.updateMatrixWorld(true);
      const box = new THREE.Box3().setFromObject(robot);
      const center = box.getCenter(new THREE.Vector3());
      const size = box.getSize(new THREE.Vector3());
      const span = Math.max(size.x, size.y, size.z, 1);
      const distance = span * 0.82 / Math.tan(THREE.MathUtils.degToRad(camera.fov / 2));
      const direction = view === "front"
        ? new THREE.Vector3(1, 0, 0.14)
        : view === "side"
          ? new THREE.Vector3(0, 1, 0.14)
          : view === "top"
            ? new THREE.Vector3(0.001, 0, 1)
            : new THREE.Vector3(1.1, 1.1, 0.78);
      direction.normalize();
      controls.target.copy(center);
      camera.position.copy(center).addScaledVector(direction, distance);
      camera.near = Math.max(distance / 500, 0.01);
      camera.far = Math.max(distance * 8, 30);
      camera.updateProjectionMatrix();
      controls.update();
      render();
    };

    manager.onLoad = () => {
      if (disposed || !robot) return;
      fit();
      if (meshLoadErrors.length > 0) {
        const uniqueErrors = [...new Set(meshLoadErrors)];
        setError(`필수 메시 ${uniqueErrors.length}개를 불러오지 못했습니다. ${uniqueErrors[0]}`);
        setStatus("error");
        return;
      }
      setStatus("ready");
    };

    const modelUrl = `/models/rby1${model.model}/urdf/model_${model.version}.urdf`;
    urdfLoader.load(
      modelUrl,
      (loaded) => {
        if (disposed) {
          disposeObject(loaded);
          return;
        }
        robot = loaded;
        scene.add(loaded);
        const api = { camera, controls, renderer, scene, robot: loaded, render, fit };
        apiRef.current = api;
        applyJointValues(api, jointValuesRef.current, container);
      },
      undefined,
      (reason) => {
        if (disposed) return;
        setError(reason instanceof Error ? reason.message : String(reason));
        setStatus("error");
      },
    );

    const resize = new ResizeObserver(() => {
      const width = Math.max(container.clientWidth, 1);
      const height = Math.max(container.clientHeight, 1);
      camera.aspect = width / height;
      camera.updateProjectionMatrix();
      renderer.setSize(width, height, false);
      render();
    });
    resize.observe(container);

    return () => {
      disposed = true;
      resize.disconnect();
      controls.removeEventListener("change", render);
      controls.dispose();
      apiRef.current = null;
      disposeObject(scene);
      renderer.dispose();
      renderer.domElement.remove();
    };
  }, [model.model, model.version]);

  useEffect(() => {
    const api = apiRef.current;
    if (!api) return;
    applyJointValues(api, jointValues, containerRef.current);
  }, [jointValues, status]);

  function setView(view: CameraView) {
    apiRef.current?.fit(view);
  }

  return <section className="robotViewerPanel" aria-label="로봇 자세 시각화">
    <header className="robotViewerToolbar">
      <div>
        <strong>{modelLabel(model)}</strong>
        <span className={`modelConfidence confidence-${model.confidence}`} title={model.reason}>
          {model.confidence === "detected" ? "로그 확인" : model.confidence === "inferred" ? "신호 추론" : "기본 가정"}
        </span>
      </div>
      <div className="cameraButtons" aria-label="3D 카메라 시점">
        <button type="button" className="textButton" onClick={() => setView("front")} disabled={status !== "ready"}>정면</button>
        <button type="button" className="textButton" onClick={() => setView("side")} disabled={status !== "ready"}>측면</button>
        <button type="button" className="textButton" onClick={() => setView("top")} disabled={status !== "ready"}>상면</button>
        <button type="button" className="textButton" onClick={() => setView("reset")} disabled={status !== "ready"}>리셋</button>
      </div>
    </header>
    <div className="robotCanvas" ref={containerRef} data-viewer-state={status}>
      {status === "loading" && <div className="viewerOverlay">3D 로봇 모델을 불러오는 중입니다.</div>}
      {status === "error" && <div className="viewerOverlay errorText"><strong>3D 로봇 모델을 불러오지 못했습니다.</strong><span>{error}</span></div>}
    </div>
    <footer className="robotViewerStatus">
      <span>{status === "ready" ? "로봇 모델 준비됨" : status === "error" ? "모델 로드 실패" : "모델 로딩 중"}</span>
      <time>{cursorLabel}</time>
      <span>좌클릭: 회전 · 우클릭: 평행이동 · 휠: 확대/축소</span>
    </footer>
  </section>;
}

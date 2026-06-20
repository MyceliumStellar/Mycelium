"use client";

import React, { useEffect, useRef } from "react";
import * as THREE from "three";

export default function MyceliumNetwork() {
  const containerRef = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (typeof window === "undefined" || !containerRef.current) return;

    // 1. Setup Scene, Camera, Renderer
    const container = containerRef.current;
    const width = container.clientWidth;
    const height = container.clientHeight;

    const scene = new THREE.Scene();
    
    // Add subtle ambient fog to fade nodes into the classy black background
    scene.fog = new THREE.FogExp2(0x040405, 0.035);

    const camera = new THREE.PerspectiveCamera(60, width / height, 0.1, 100);
    camera.position.z = 15;

    const renderer = new THREE.WebGLRenderer({ antialias: true, alpha: true });
    renderer.setSize(width, height);
    renderer.setPixelRatio(Math.min(window.devicePixelRatio, 2));
    container.appendChild(renderer.domElement);

    // 2. Node Generation
    const nodeCount = 120;
    const nodes: {
      position: THREE.Vector3;
      velocity: THREE.Vector3;
      basePosition: THREE.Vector3;
      colorType: "cyan" | "purple";
      size: number;
    }[] = [];

    const positions = new Float32Array(nodeCount * 3);
    const colors = new Float32Array(nodeCount * 3);

    const colorCyan = new THREE.Color("#0096c7");
    const colorPurple = new THREE.Color("#6d28d9");

    for (let i = 0; i < nodeCount; i++) {
      // Random position in a cuboid box
      const x = (Math.random() - 0.5) * 30;
      const y = (Math.random() - 0.5) * 20;
      const z = (Math.random() - 0.5) * 15;

      const pos = new THREE.Vector3(x, y, z);
      const vel = new THREE.Vector3(
        (Math.random() - 0.5) * 0.015,
        (Math.random() - 0.5) * 0.015,
        (Math.random() - 0.5) * 0.01
      );

      const colorType = Math.random() > 0.4 ? "cyan" : "purple";
      const activeColor = colorType === "cyan" ? colorCyan : colorPurple;

      nodes.push({
        position: pos,
        velocity: vel,
        basePosition: pos.clone(),
        colorType,
        size: Math.random() * 0.15 + 0.08,
      });

      positions[i * 3] = x;
      positions[i * 3 + 1] = y;
      positions[i * 3 + 2] = z;

      colors[i * 3] = activeColor.r;
      colors[i * 3 + 1] = activeColor.g;
      colors[i * 3 + 2] = activeColor.b;
    }

    // Points geometry & material
    const pointsGeometry = new THREE.BufferGeometry();
    pointsGeometry.setAttribute("position", new THREE.BufferAttribute(positions, 3));
    pointsGeometry.setAttribute("color", new THREE.BufferAttribute(colors, 3));

    // Create a circular particle texture programmatically
    const canvas = document.createElement("canvas");
    canvas.width = 16;
    canvas.height = 16;
    const ctx = canvas.getContext("2d")!;
    const grad = ctx.createRadialGradient(8, 8, 0, 8, 8, 8);
    grad.addColorStop(0, "rgba(255, 255, 255, 1)");
    grad.addColorStop(0.5, "rgba(255, 255, 255, 0.4)");
    grad.addColorStop(1, "rgba(255, 255, 255, 0)");
    ctx.fillStyle = grad;
    ctx.fillRect(0, 0, 16, 16);
    const particleTexture = new THREE.CanvasTexture(canvas);

    const pointsMaterial = new THREE.PointsMaterial({
      size: 0.5,
      map: particleTexture,
      vertexColors: true,
      transparent: true,
      depthWrite: false,
    });

    const pointCloud = new THREE.Points(pointsGeometry, pointsMaterial);
    scene.add(pointCloud);

    // 3. Connections Line Setup
    const maxConnections = 400;
    const linePositions = new Float32Array(maxConnections * 2 * 3);
    const lineColors = new Float32Array(maxConnections * 2 * 3);

    const linesGeometry = new THREE.BufferGeometry();
    linesGeometry.setAttribute("position", new THREE.BufferAttribute(linePositions, 3));
    linesGeometry.setAttribute("color", new THREE.BufferAttribute(lineColors, 3));

    const linesMaterial = new THREE.LineBasicMaterial({
      vertexColors: true,
      transparent: true,
      opacity: 0.25,
      depthWrite: false,
    });

    const connectionLines = new THREE.LineSegments(linesGeometry, linesMaterial);
    scene.add(connectionLines);

    // 4. Mouse Tracking & Parallax Interaction
    const mouse = new THREE.Vector2(-9999, -9999);
    const targetMouse = new THREE.Vector2(0, 0);

    const handleMouseMove = (e: MouseEvent) => {
      // Map screen coordinates to normalized device coordinates (-1 to 1)
      targetMouse.x = (e.clientX / window.innerWidth) * 2 - 1;
      targetMouse.y = -(e.clientY / window.innerHeight) * 2 + 1;
    };

    window.addEventListener("mousemove", handleMouseMove);

    // 5. Window Resize Handler
    const handleResize = () => {
      if (!containerRef.current) return;
      const w = containerRef.current.clientWidth;
      const h = containerRef.current.clientHeight;
      camera.aspect = w / h;
      camera.updateProjectionMatrix();
      renderer.setSize(w, h);
    };

    window.addEventListener("resize", handleResize);

    // 6. Animation Loop
    let animationFrameId = 0;
    const raycaster = new THREE.Raycaster();
    const plane = new THREE.Plane(new THREE.Vector3(0, 0, 1), 0); // Intersection plane

    const animate = () => {
      animationFrameId = requestAnimationFrame(animate);

      // Smooth mouse interpolation
      mouse.x += (targetMouse.x - mouse.x) * 0.08;
      mouse.y += (targetMouse.y - mouse.y) * 0.08;

      // Project mouse position into 3D world space on the z=0 plane
      raycaster.setFromCamera(mouse, camera);
      const mouseWorldPos = new THREE.Vector3();
      raycaster.ray.intersectPlane(plane, mouseWorldPos);

      // Update positions of points attribute
      const pointsPosAttribute = pointsGeometry.attributes.position as THREE.BufferAttribute;
      
      let lineIndex = 0;
      const activeLinePositions = linesGeometry.attributes.position as THREE.BufferAttribute;
      const activeLineColors = linesGeometry.attributes.color as THREE.BufferAttribute;

      // Drift and affect nodes by mouse
      for (let i = 0; i < nodeCount; i++) {
        const node = nodes[i];

        // Organic float movement
        node.position.add(node.velocity);

        // Boundary wrapping
        const boundaryX = 18;
        const boundaryY = 12;
        const boundaryZ = 8;
        if (Math.abs(node.position.x) > boundaryX) node.velocity.x *= -1;
        if (Math.abs(node.position.y) > boundaryY) node.velocity.y *= -1;
        if (Math.abs(node.position.z) > boundaryZ) node.velocity.z *= -1;

        // Mouse reactive displacement
        if (mouse.x > -1000) {
          const distToMouse = node.position.distanceTo(mouseWorldPos);
          if (distToMouse < 4.5) {
            // Push nodes away from cursor
            const pushDir = new THREE.Vector3().subVectors(node.position, mouseWorldPos).normalize();
            const pushForce = (4.5 - distToMouse) * 0.05;
            node.position.addScaledVector(pushDir, pushForce);
          }
        }

        // Return slightly back to base/drift orbit
        const returnForce = new THREE.Vector3().subVectors(node.basePosition, node.position).multiplyScalar(0.005);
        node.position.add(returnForce);
        node.basePosition.add(node.velocity); // Update orbit baseline

        // Update positions buffer for nodes
        pointsPosAttribute.setXYZ(i, node.position.x, node.position.y, node.position.z);
      }
      pointsPosAttribute.needsUpdate = true;

      // Find pairs to connect
      const connectionDist = 4.5;
      for (let i = 0; i < nodeCount; i++) {
        if (lineIndex >= maxConnections) break;

        const nodeA = nodes[i];
        
        for (let j = i + 1; j < nodeCount; j++) {
          if (lineIndex >= maxConnections) break;

          const nodeB = nodes[j];
          const dist = nodeA.position.distanceTo(nodeB.position);

          if (dist < connectionDist) {
            // Push point A
            activeLinePositions.setXYZ(lineIndex * 2, nodeA.position.x, nodeA.position.y, nodeA.position.z);
            // Push point B
            activeLinePositions.setXYZ(lineIndex * 2 + 1, nodeB.position.x, nodeB.position.y, nodeB.position.z);

            // Calculate line opacity based on distance (fades as distance increases)
            const alpha = 1.0 - dist / connectionDist;
            
            // Apply corresponding node colors with custom fading
            const colorA = nodeA.colorType === "cyan" ? colorCyan : colorPurple;
            const colorB = nodeB.colorType === "cyan" ? colorCyan : colorPurple;

            activeLineColors.setXYZ(lineIndex * 2, colorA.r * alpha * 0.7, colorA.g * alpha * 0.7, colorA.b * alpha * 0.7);
            activeLineColors.setXYZ(lineIndex * 2 + 1, colorB.r * alpha * 0.7, colorB.g * alpha * 0.7, colorB.b * alpha * 0.7);

            lineIndex++;
          }
        }
      }

      // Zero out the remaining vertices in the line buffer if we used less than maxConnections
      for (let i = lineIndex; i < maxConnections; i++) {
        activeLinePositions.setXYZ(i * 2, 0, 0, 0);
        activeLinePositions.setXYZ(i * 2 + 1, 0, 0, 0);
        activeLineColors.setXYZ(i * 2, 0, 0, 0);
        activeLineColors.setXYZ(i * 2 + 1, 0, 0, 0);
      }

      activeLinePositions.needsUpdate = true;
      activeLineColors.needsUpdate = true;

      // Gentle camera parallax rotation based on mouse coordinates
      if (mouse.x > -1000) {
        camera.position.x += (mouse.x * 2 - camera.position.x) * 0.05;
        camera.position.y += (mouse.y * 1.5 - camera.position.y) * 0.05;
        camera.lookAt(0, 0, 0);
      }

      renderer.render(scene, camera);
    };

    animate();

    // 7. Cleanup
    return () => {
      cancelAnimationFrame(animationFrameId);
      window.removeEventListener("mousemove", handleMouseMove);
      window.removeEventListener("resize", handleResize);

      // Clean up Three.js objects from memory
      pointsGeometry.dispose();
      pointsMaterial.dispose();
      linesGeometry.dispose();
      linesMaterial.dispose();
      particleTexture.dispose();
      renderer.dispose();
      
      if (container.contains(renderer.domElement)) {
        container.removeChild(renderer.domElement);
      }
    };
  }, []);

  return (
    <div
      ref={containerRef}
      style={{
        position: "absolute",
        top: 0,
        left: 0,
        width: "100%",
        height: "100%",
        pointerEvents: "none",
        zIndex: 0,
      }}
    />
  );
}
